# Encore Party Booking, Demo v1 Spec

## Goal
A parent picks a date that is verified open, sees a live price, and the slot is held exclusively for them. Paying the $100 deposit (his existing policy) instantly locks the date and sends a confirmation text, with zero human involvement. The date is guaranteed before any money moves. The owner gets a text the second it happens. Demoable on a phone, styled to match encoregaming.us.

This is a standalone system on its own URL. It never touches the existing website or the owner's live Square account (sandbox only until he approves).

## Stack
- Backend: FastAPI (Python 3.12). Local dev with uvicorn. Deploy target: AWS Lambda via Mangum behind a Function URL, provisioned with Terraform.
- DB: Neon Postgres free tier for dev and prod. SQLAlchemy + Alembic migrations. Tests run against a local Postgres started with Docker Compose (`docker compose up -d db`), never SQLite: the constraints under test are Postgres behavior.
- Payments: Square Sandbox, hosted Checkout (Payment Links) + Webhooks.
- Notifications: Twilio trial SMS (delivers only to verified numbers), with a log-to-console fallback when Twilio env vars are absent. Optional Discord webhook for owner notifications if `DISCORD_WEBHOOK_URL` is set.
- Frontend: static HTML + vanilla JS, mobile first, deployed to S3 + CloudFront. Dark theme matching the Encore site (near-black background, blue accents, condensed uppercase headings).
- Local webhook development: ngrok with a static domain (free tier offers one) so the webhook URL does not change between runs.
- CI: GitHub Actions runs the test suite on every push with a Postgres service container.

## Business rules (taken from the live site, verify with owner before real launch)

### Packages
All packages are private full-venue buyouts, Sat and Sun mornings, ages 6+.

| Package | Price | Included kids | Duration |
|---|---|---|---|
| Standard | $399 | up to 15 | 3 hours |
| Plus | $549 | up to 20 | 3 hours |
| Premium | $749 | up to 25 | 4 hours |

- Additional kids: +$35 each beyond the included headcount.
- Headcount: minimum 1 kid; hard cap per party 30 kids for the demo (confirm with owner).
- Minimum lead time: a slot is bookable only if its date is at least `MIN_LEAD_DAYS` (env, default 2) days away. Confirm the number with the owner.
  - Counted in calendar days in America/New_York, not hours: with the default of 2, a Thursday request can take Saturday and a Friday request cannot.

### Add-ons
| Add-on | Price | Notes |
|---|---|---|
| Tournament + Livestream | $199 | |
| Highlight Reel | $50 | only selectable with Tournament + Livestream; server rejects it otherwise |
| Extra Hour | $75 | on/off, not a quantity; display-only effect on duration in v1 |
| VR Gaming Session | $60 | included free in Premium |
| DDR / Stepmania Pad | $40 | included free in Premium |
| Gamer Decorations | $30 | included free in Plus and Premium |
| Pizza Pack | $40 each | quantity field, ~10 kids per pack |
| Drinks Pack | $20 each | quantity field |

Included-free add-ons must show as "Included" for those packages and add $0.

The live site's booking form also offers "Venue Buyout, custom quote (off-hours)". That is not a package in this system; it stays with the existing site form (see Non-goals).

### Money
- Deposit to book: $100 flat, paid online.
- Balance: total minus deposit, due day of event, collected in person. Out of scope to collect, but the confirmation page and SMS must state the remaining balance.
- The total is snapshotted on the hold at creation time and copied to the booking. Later price changes never alter an existing hold or booking.
- Refund rule: full refund if cancelled 7+ days out. Store the data needed to apply it (booking created_at, event date, cancelled_at); refund automation is out of scope for demo.

## Inventory model
- One party per slot (every package is a full-venue buyout, so a slot can hold exactly one booking).
- A slot is (date, start_time). Demo rule: one bookable slot per weekend day at 10:00 AM America/New_York, stored as timestamptz in UTC. Duration is display-only in v1.
- A generator materializes slots for the next 8 weekends from a template. It is idempotent and runs lazily whenever availability is requested. Owner can block a date in admin. Blocking is refused (409) while the slot has an active hold or a live booking; otherwise a parent could pay into a blocked slot.
- Slot states are computed, not stored: open, held (active unexpired hold exists), booked, blocked, past (includes inside the lead-time window).

## Booking flow (the money path)
1. `GET /api/availability` returns the next 8 weekends with per-slot status.
2. `POST /api/quote` with {package, kids, addons} returns an itemized total. Server-side pricing is the source of truth; the UI only mirrors it. The same pricing function is reused at hold time; the client never sends prices.
3. `POST /api/holds` with {slot_id, package, kids, addons, first_name, last_name, phone, email, notes}:
   - validates inputs (package rules, kid cap, add-on dependencies, phone normalized to E.164, valid email),
   - computes and stores total_cents plus a snapshot of package, kids, and add-ons on the hold,
   - creates a 15-minute hold (the database constraint decides who wins, see Correctness),
   - creates the Square payment link for the $100 deposit using hold_id as the Square `idempotency_key` so a retry never creates a second link, with `redirect_url` pointing at the confirmation page with the hold_id in the query string,
   - stores the Square order_id from the payment link response on the hold,
   - if creating the payment link fails, cancels the hold (freeing the slot) and returns 502,
   - returns {hold_id, checkout_url, expires_at}.
4. Parent pays on the Square sandbox checkout page (test card).
5. Square webhook hits `POST /api/webhooks/square`. Handler must, in this order: verify the HMAC signature header against the raw body, acknowledge with 200 and ignore any event that is not a payment with status COMPLETED, check event-level and payment-level idempotency, find the hold by the payment's order_id, verify amount is exactly 10000 cents USD, convert hold to booking inside one transaction, commit, then send notifications (best-effort, after commit, never inside the transaction).
6. Confirmation page: static `confirm.html?hold={hold_id}`. It polls `GET /api/holds/{hold_id}` until the status leaves `pending_payment`. Statuses: `pending_payment`, `booked` (returns confirmation_code and booking details), `expired`, `cancelled` (admin cancel, or the payment link could not be created; show a plain "this hold was cancelled" message), `paid_after_expiry` (show: "We received your payment. This slot's hold had expired, so we'll call you within the hour to lock in your date."). The permanent confirmation is `confirm.html?code={confirmation_code}` backed by `GET /api/bookings/{code}`. Confirmation codes are random and unguessable; the endpoint returns only what the confirmation page needs.
   - The hold_id passes through Square's redirect and browser history, so a `booked` hold returns only: confirmation_code, first name, package, date, time, kids, total, and balance. Never phone, email, or notes. `GET /api/bookings/{code}` returns the same fields.

Square sends its own payment receipt email; that covers the payment record. Our SMS covers the booking.

## Correctness requirements
- **Double-booking**: exactly one active hold-or-booking per slot, enforced by a database constraint (partial unique index treating expired holds as inactive, or an equivalent design), never by an application-level check alone. Two simultaneous hold requests: one gets 201, the other gets 409 plus a list of alternative open slots.
- **Hold expiry**: expires_at column, 15 minutes. Availability and hold creation treat expired holds as free (lazy expiry). A cleanup sweep is a later nice-to-have.
- **Hold abuse**: holds cost nothing and lock revenue, so cap them: at most 1 active hold per normalized phone number, 1 per email, and 5 per client IP. Over the cap returns 429 with the existing hold's expiry.
  - "Active" means an unexpired hold in `pending_payment`. A booked party does not count, so the same parent can hold another date.
  - Client IP is the connection's source IP (in Lambda, the source IP of the Function URL request). The API is not behind CloudFront, so a client-sent X-Forwarded-For is spoofable and is ignored, unless `TRUST_PROXY_HEADERS` is set because a trusted proxy (ngrok in dev) sits in front.
- **Webhook signature**: verify Square's HMAC signature header against the raw request body per current Square docs. Reject mismatches with 401. Do not parse the body before verifying. Note: Square's signature covers the notification URL too, so `SQUARE_WEBHOOK_NOTIFICATION_URL` must match the URL configured in the Square dashboard exactly.
- **Webhook idempotency**: persist processed Square event ids and the payment id on the booking. Replaying the same event, or receiving payment.created and payment.updated for the same payment, yields one booking and zero additional notifications.
- **Payments that cannot become a normal booking**: a second, different completed payment on an order that already produced a booking, a completed payment whose amount is not exactly 10000 cents USD, or a completed payment whose order_id matches no hold. Each is recorded as a payment flag (never a second booking), and surfaced in admin for the owner to resolve in Square.
- **Payment on expired hold**: if a completed payment references a hold that expired (someone paid at minute 16), do not silently drop it: record it, mark the hold `paid_after_expiry`, notify the owner "payment received on expired hold, call parent," and surface it in admin. This applies even when nobody else has taken the slot: v1 never auto-books a late payment; the owner decides.
- **Notifications never block money**: the booking is committed before any SMS or Discord call. A notification failure is logged to the notifications table and shown in admin; it never fails the webhook or rolls back the booking.
- **Pricing**: computed only on the server, from one function, covered by table-driven tests.

## Admin (demo scope)
- Single owner login: password from env (compared with constant-time comparison), HTTPS-only session cookie. Nothing fancier.
- Bookings list: date, time, package, kids, add-ons, total, deposit status, contact, notes.
- Block / unblock a date.
- Manually cancel a booking (frees the slot, records cancelled_at; refund handled by owner in Square for now).
- Payment flags visible (expired-hold payments, duplicate payments, amount mismatches, unmatched orders).
- Notification log with failures visible.

## Notifications
- Parent SMS: "You're booked! {Package} party {date} {time} at Encore Esports, {kids} kids. $100 deposit received, balance ${x} due day of party. 538 Main St, 2nd floor, entrance on Division St. Questions: (646) 203-8087. Details: {confirmation link}"
  - {confirmation link} is the permanent `confirm.html?code={confirmation_code}` URL on the frontend origin, so a parent who closes the tab can get back to the confirmation.
- Owner SMS: "NEW PARTY: {package} {date} {time}, {kids} kids, {addons}. Deposit paid. {parent name} {phone}."
- Owner Discord webhook (optional): same content as the owner SMS, posted if `DISCORD_WEBHOOK_URL` is set. Free and permanent, unlike SMS.
- Every send attempt is written to a notifications table (channel, recipient, body, status, error, sent_at).
- Twilio trial only delivers to verified numbers and prefixes messages with a trial notice: verify Tyler's number now, the owner's before demo day. If Twilio env vars are missing, log the message body instead so tests and dev never depend on Twilio.

## Non-goals for demo v1
Refund automation, balance collection, chatbot, Start.gg sync, memberships, email notifications, coupon codes, editing a confirmed booking, multi-slot days, staff accounts, calendar sync, venue buyout lead form (the existing site form already handles it), package upgrade suggestions.

## Demo script
On a phone, in under 3 minutes:
1. Pick Standard, set 18 kids, add a Pizza Pack: total updates live to $544 (399 + 3x35 + 40). Balance shown as $444 after deposit.
2. Pick an open Saturday, hold starts, 15:00 countdown visible.
3. Pay with Square test card, land on the confirmation page, which flips from "confirming your payment" to booked within seconds.
4. Confirmation SMS arrives on the phone; owner SMS (and Discord post, if configured) arrives on the second phone.
5. Second phone tries to book the same Saturday: sees it held/booked and gets offered the next open slot.
6. Admin page already shows the booking.

## Required tests
1. `test_concurrent_holds`: N parallel hold requests for one slot (real parallel Postgres connections) yield exactly one success and N-1 conflicts. Each request uses a distinct phone, email, and client IP so the hold caps (429) cannot mask the slot conflict (409).
2. `test_webhook_replay`: identical webhook delivered twice, and payment.created followed by payment.updated for the same payment, yield one booking and one recorded notification per recipient.
3. `test_webhook_bad_signature`: tampered body or wrong key yields 401 and no state change.
4. `test_webhook_ignores_incomplete`: a payment event with a non-COMPLETED status returns 200 and creates nothing.
5. `test_pricing_table`: table-driven cases covering included kids boundaries (15/16, 20/21, 25/26), included-free add-ons per package, pack quantities, Highlight Reel without Tournament rejected.
6. `test_expired_hold_payment`: payment event referencing an expired hold creates a flag, not a booking.
7. `test_hold_limits`: a second active hold for the same phone number returns 429; a hold on a slot inside the lead-time window is rejected.

## Deployment notes
- Alembic migrations run from the laptop against Neon's direct connection string. Lambda uses Neon's pooled connection string.
- CORS: the API allows only the frontend origin, read from env.
- If the AWS deploy fights back on demo week, demo from the laptop through ngrok and deploy after the owner says yes. The demo does not depend on AWS.

## Before going live
- Owner reviews every price and rule in this file.
- Live Square credentials entered by the owner in the hosting environment, never in the repo.
- US business texting registration (A2P 10DLC) is required before Twilio delivers to real parents; it takes days and a small fee. Discord webhook is the zero-cost owner channel in the meantime.
- Point book.encoregaming.us at CloudFront and swap the "Book a Party" link on the existing site.
