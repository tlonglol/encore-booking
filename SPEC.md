# Encore Party Booking, Demo v1 Spec

## Goal
A parent picks a date that is verified open, sees a live price, and the slot is held exclusively for them. Paying the $100 deposit (his existing policy) instantly locks the date and sends a confirmation text and email, with zero human involvement. The date is guaranteed before any money moves. The owner gets a text the second it happens. Demoable on a phone, styled to match encoregaming.us.

This is a standalone system on its own URL. It never touches the existing website or the owner's live Square account (sandbox only until he approves).

## Stack
- Backend: FastAPI (Python 3.12). Local dev with uvicorn. Deploy target: AWS Lambda via Mangum behind a Function URL, provisioned with Terraform.
- DB: Neon Postgres free tier for dev and prod. SQLAlchemy + Alembic migrations. Tests run against a local Postgres started with Docker Compose (`docker compose up -d db`), never SQLite: the constraints under test are Postgres behavior.
- Payments: Square Sandbox, hosted Checkout (Payment Links) + Webhooks.
- Notifications: Twilio trial SMS (delivers only to verified numbers), with a log-to-console fallback when Twilio env vars are absent. Parent confirmation email over SMTP (stdlib `smtplib`, any provider's SMTP relay), with the same log-to-console fallback when the SMTP env vars are absent. Optional Discord webhook for owner notifications if `DISCORD_WEBHOOK_URL` is set.
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
- The total is snapshotted on the hold at the checkout call (the "Pay $100" step, when package, add-ons, and headcount are all known) and copied to the booking. Later price changes never alter a snapshotted hold or booking.
- Refund and reschedule promise, printed above the pay button: "Free reschedule up to 7 days before your party. Deposit fully refundable for 14 days." Interpreted as: full refund if cancelled within 14 days of booking (and before the party), or 7+ days before the party; reschedules handled by phone. This is more generous than the site's bare 7-day rule and needs the owner's explicit sign-off before launch (see Before going live). Store the data needed to apply it (booking created_at, event date, cancelled_at); refund automation is out of scope for demo.

## Inventory model
- Weekend mornings are open for online booking by default; the owner blocks exceptions. Weekdays are request-only (see Party requests). `WEEKEND_DEFAULT` (env: `open` | `request`, default `open`) flips weekends into the same request-only behavior; while it is `request`, holds are refused server-side for every date.
- One party per slot (every package is a full-venue buyout, so a slot can hold exactly one booking).
- A slot is (date, start_time). Demo rule: one bookable slot per weekend day at 10:00 AM America/New_York, stored as timestamptz in UTC. Duration is display-only in v1. Weekday request dates have no slot row; a request stores its own date and preferred start time.
- A generator materializes weekend slots for the next 8 weekends from a template. It is idempotent and runs lazily whenever availability is requested. Owner can block a date in admin. Blocking is refused (409) while the slot has an active hold or a live booking; otherwise a parent could pay into a blocked slot. Weekday dates cannot be blocked in v1; the owner simply declines their requests.
- Availability covers every date from today through the last day of the 8th weekend. Slot states are computed, not stored: open, held (active unexpired hold exists), booked, blocked, past (inside the lead-time window), request (weekdays; weekends too when `WEEKEND_DEFAULT=request`).
- The date grid is a weekend-first list of the next 8 weekends (Saturday and Sunday cards grouped by weekend), each card showing the date, 10:00 AM, and the state as a word on the card, never by color alone: Open, On hold ("may free up in a few minutes"), Booked, Unavailable (blocked or inside the lead-time window; the API keeps blocked and past distinct because admin unblocks), and Request (weekends while `WEEKEND_DEFAULT=request`). Booked and held dates stay visible but disabled; they are social proof, never hidden. The grid re-fetches availability every 30 seconds and when the tab regains focus, so a lapsed hold flips back to open without a reload.

## Booking funnel (the money path)
Five pages, one decision per page, exactly four forward clicks from landing to payment: date card, SELECT, Lock in, Pay. Pages 1 through 4 carry a 4-step progress indicator (Date / Package / Extras / Confirm). Back navigation works at every step without losing state (selections live in sessionStorage) and going back never releases a hold early. A sticky price bar is visible on every page: always one rolled-up number, itemization only behind a Details toggle. Copy rules, hard constraints: no fake urgency or viewer counts, nothing pre-selected or pre-checked, neutral decline copy, no cost appearing on a later page than the one that introduces it (package price on pages 1 and 2, add-on prices on page 3, the per-extra-kid price on the package cards before headcount is entered on page 4), and nothing that would read badly screenshotted in a local parents' Facebook group.

1. **Landing + availability (`index.html`)**: pitch and the date grid on one page. Under the headline: "Private venue parties from $399". Above the grid, a one-line informational tier strip (Standard $399 / Plus $549 / Premium $749, not clickable, prices from `GET /api/catalog`). The advancing click is tapping an OPEN date card; there is no separate Book Now button. The tap calls `POST /api/holds` with {slot_id}: an anonymous 15-minute hold, no contact details yet, the database constraint decides who wins (see Correctness). Returning to this page with an active hold shows a resume banner, and tapping a different open date sends `replace_hold_id` so the previous hold is cancelled in the same transaction: a parent swaps dates instead of stacking holds. Below the grid: "Don't see your date? Get notified" captures an email only (`POST /api/waitlist`, stored lowercased in a waitlist table, duplicates collapse silently), and "Want a weekday? Tell us your date" starts the same pages in request mode (see Party requests). The sticky bar reads "from $399".
2. **Package (`package.html`)**: persistent header "Holding {Day}, {Mon D} at 10:00 AM". Three package cards; Plus keeps the Most Popular badge but is NOT pre-selected. The advancing click is the SELECT button on a card, which advances immediately: no separate Continue, no select-then-confirm. The sticky bar updates to the selected package total the moment SELECT is clicked.
3. **Add-ons (`addons.html`)**: same header. Toggles and quantity steppers never navigate; they only update the sticky total. Every toggle starts off and every stepper at 0, no exceptions (coming back via Back restores the parent's own earlier picks). Included-free add-ons show "Included" and add $0; Highlight Reel stays disabled with a one-line explanation until Tournament + Livestream is on. The advancing click is the sticky-bar button, "Lock in {Mon D} ($100 today)", with the full total visible in the bar next to it.
4. **Info + deposit (`checkout.html`)**, one page, never split: header "You're holding {Day}, {Mon D} at 10:00 AM" plus a live countdown of the real remaining hold time (`GET /api/holds/{id}` seconds_remaining, rendered against a monotonic clock; no fake or resetting timers). If the hold expires, say the date was released and show a clear path back to the grid. Form fields, in order, six max: parent email (first, persisted on blur via `PATCH /api/holds/{id}/email` so abandoners are recoverable), parent name, phone, kid's name, kid's age, estimated headcount. Collapsed order summary: one rolled-up total with an expandable itemized breakdown, deposit math explicit ("${total} total, $100 today, ${remainder} day of party"); nothing is added to the total after this page. Directly above the pay button, the risk-reversal line from Money. The only button on the page: "Pay $100 & lock it in". It calls `POST /api/holds/{id}/checkout` with {package, kids, addons, first_name, last_name, phone, email, kid_name, kid_age}, which:
   - verifies the hold is still `pending_payment` and unexpired (409 otherwise, nothing changed),
   - validates inputs (package rules, kid cap, add-on dependencies, phone normalized to E.164, valid email) and prices server-side; the client never sends a price,
   - snapshots package, kids, add-ons, line items, and total onto the hold,
   - enforces the phone and email caps (see Correctness),
   - creates the Square payment link for the $100 deposit using hold_id as the Square `idempotency_key` so a retry never creates a second link, with `redirect_url` pointing at the confirmation page with the hold_id in the query string, and stores the returned order_id on the hold (an existing link is reused),
   - if creating the payment link fails, returns 502 and keeps the hold: the parent's date is not released over a Square blip, and the button can be retried,
   - returns {checkout_url, party}; the page then redirects to Square's hosted checkout (sandbox test card).
5. Square webhook hits `POST /api/webhooks/square`. Handler must, in this order: verify the HMAC signature header against the raw body, acknowledge with 200 and ignore any event that is not a payment with status COMPLETED, check event-level and payment-level idempotency, find the hold by the payment's order_id, verify amount is exactly 10000 cents USD, convert hold to booking inside one transaction (this is the atomic hold-to-booked flip; the unique indexes make it safe against webhook retries), commit, then send notifications (best-effort, after commit, never inside the transaction).
6. **Confirmation (`confirm.html?hold={hold_id}`)**: polls `GET /api/holds/{hold_id}` until the status leaves `pending_payment`. Statuses: `pending_payment`, `booked` (confirmation_code and booking details), `expired`, `cancelled` (admin cancel or a swapped date; plain "this hold was cancelled" message), `paid_after_expiry` (show: "We received your payment. This slot's hold had expired, so we'll call you within the hour to lock in your date."). A booked confirmation shows date, time, package, add-ons, total, deposit paid, balance due day of party, what happens next ("Encore will text you the week of your party"), Add to Calendar buttons (a Google Calendar link and an Apple/.ics file from `GET /api/bookings/{code}/calendar.ics`), and the deferred upsell line: "You can add Pizza Packs, Drinks, or VR anytime before the party" pointing at (646) 203-8087 (self-serve add-on editing stays out of v1; see Before going live). The permanent confirmation is `confirm.html?code={confirmation_code}` backed by `GET /api/bookings/{code}`. Confirmation codes are random and unguessable; the endpoint returns only what the confirmation page needs.
   - The hold_id passes through Square's redirect and browser history, so a `booked` hold returns only: confirmation_code, first name, package, date, time, kids, total, and balance. Never phone, email, or notes. `GET /api/bookings/{code}` returns the same fields.

Square sends its own payment receipt email; that covers the payment record. Our SMS and email cover the booking.

## Party requests (stage 1)
Weekdays (and weekends while `WEEKEND_DEFAULT=request`) take requests instead of holds. A request is an inquiry: no hold, no payment, no slot is taken, and the owner replies personally.
1. "Want a weekday? Tell us your date" (or tapping a Request date card) runs the same funnel pages in request mode: no hold is created, the headers read "Requesting {Day}, {Mon D}", there is no countdown, and the info page adds a preferred start time (9:00 AM to 8:00 PM) and ends with one primary action, "Send request", instead of payment.
2. `POST /api/requests` validates exactly like a hold (the pricing function prices the summary; phone normalized to E.164) and stores a `requests` row: date, preferred time, status (`new`; later statuses reserved for stage 2), a snapshot of package, kids, add-ons, line items, and total, contact fields, client IP, timestamps.
3. The date must be a request date: weekday, or weekend while `WEEKEND_DEFAULT=request` and the date is not booked or blocked. Weekend dates while weekends are `open` are rejected with "book it online". Dates inside the lead-time window are rejected with the call-us message; dates more than a year out are rejected.
4. After commit, the owner is notified by SMS and Discord (same rules and notifications table as booking notices, keyed by request id, best-effort, never blocking the response). The parent sees an on-page confirmation; no parent SMS in stage 1.
5. Duplicates: at most one `new` request per phone per date and per email per date, enforced by partial unique indexes; a duplicate returns 409 with a friendly "we already have your request" message.
6. Admin shows the requests list read-only; acting on a request is by phone in stage 1 (automation is stage 2, see Before going live).

## Correctness requirements
- **Double-booking**: exactly one active hold-or-booking per slot, enforced by a database constraint (partial unique index treating expired holds as inactive, or an equivalent design), never by an application-level check alone. Two simultaneous hold requests: one gets 201, the other gets 409 plus a list of alternative open slots.
- **Hold expiry**: expires_at column, 15 minutes. Availability and hold creation treat expired holds as free (lazy expiry). A cleanup sweep is a later nice-to-have.
- **Hold abuse**: holds cost nothing and lock revenue, so cap them. Creation is anonymous, so the caps split: at most 5 active holds per client IP at creation time, and at checkout time at most 1 other active hold per normalized phone number and per email (both 429 with the blocking hold's expiry). A same-browser date change passes `replace_hold_id`, cancelling the old hold in the same transaction. A hold that never reaches checkout carries no contact and lapses in 15 minutes.
  - "Active" means an unexpired hold in `pending_payment`. A booked party does not count, so the same parent can hold another date.
  - Client IP is the connection's source IP (in Lambda, the source IP of the Function URL request). The API is not behind CloudFront, so a client-sent X-Forwarded-For is spoofable and is ignored, unless `TRUST_PROXY_HEADERS` is set because a trusted proxy (ngrok in dev) sits in front.
- **Webhook signature**: verify Square's HMAC signature header against the raw request body per current Square docs. Reject mismatches with 401. Do not parse the body before verifying. Note: Square's signature covers the notification URL too, so `SQUARE_WEBHOOK_NOTIFICATION_URL` must match the URL configured in the Square dashboard exactly.
- **Webhook idempotency**: persist processed Square event ids and the payment id on the booking. Replaying the same event, or receiving payment.created and payment.updated for the same payment, yields one booking and zero additional notifications.
- **Payments that cannot become a normal booking**: a second, different completed payment on an order that already produced a booking, a completed payment whose amount is not exactly 10000 cents USD, or a completed payment whose order_id matches no hold. Each is recorded as a payment flag (never a second booking), and surfaced in admin for the owner to resolve in Square.
- **Payment on expired hold**: if a completed payment references a hold that expired (someone paid at minute 16), do not silently drop it: record it, mark the hold `paid_after_expiry`, notify the owner "payment received on expired hold, call parent," and surface it in admin. This applies even when nobody else has taken the slot: v1 never auto-books a late payment; the owner decides.
- **Notifications never block money**: the booking is committed before any SMS or Discord call. A notification failure is logged to the notifications table and shown in admin; it never fails the webhook or rolls back the booking.
- **Checkout on a dead hold**: the checkout call on an expired, cancelled, or already-booked hold returns 409 and changes nothing; a hold that carries a payment link always carries a complete snapshot (database check constraint).
- **Pricing**: computed only on the server, from one function, covered by table-driven tests.

## Admin (demo scope)
- Single owner login: password from env (compared with constant-time comparison), HTTPS-only session cookie. Nothing fancier.
- Bookings list: date, time, package, kids, kid's name and age, add-ons, total, deposit status, contact, notes.
- Block / unblock a date.
- Manually cancel a booking (frees the slot, records cancelled_at; refund handled by owner in Square for now).
- Payment flags visible (expired-hold payments, duplicate payments, amount mismatches, unmatched orders).
- Party requests list: date, preferred time, package, kids, add-ons, total, contact, notes, status. Read-only in stage 1.
- Notification log with failures visible.
- Leads: the waitlist emails, and abandoned checkouts (expired or cancelled holds that captured an email, last 14 days) so the owner can follow up.

## Notifications
- Parent SMS: "You're booked! {Package} party {date} {time} at Encore Esports, {kids} kids. $100 deposit received, balance ${x} due day of party. 538 Main St, 2nd floor, entrance on Division St. Questions: (646) 203-8087. Details: {confirmation link}"
  - {confirmation link} is the permanent `confirm.html?code={confirmation_code}` URL on the frontend origin, so a parent who closes the tab can get back to the confirmation.
- Owner SMS: "NEW PARTY: {package} {date} {time}, {kids} kids, {addons}. Deposit paid. {parent name} {phone}."
- Owner Discord webhook (optional): same content as the owner SMS, posted if `DISCORD_WEBHOOK_URL` is set. Free and permanent, unlike SMS.
- Owner request SMS (and Discord): "PARTY REQUEST: {package} {date} at {preferred time}, {kids} kids, {addons}. Total ${x}. {parent name} {phone} {email}." Notes stay in admin, not in the SMS.
- Parent email (channel `email`, same after-commit timing and the same one-claim-per-(booking, audience, channel) rule as the SMS): subject "You're booked: {Package} party {date}". Body: confirmation code and the permanent `confirm.html?code={confirmation_code}` link, date and time, package and kids, itemized total (from the booking's line items), the $100 deposit received, balance due day of party, "538 Main St, 2nd floor, entrance on Division St", the 7-day full-refund window, and (646) 203-8087. When SMTP credentials (`SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`) are not all set, the body is logged to the console and the row is marked `logged`, exactly like the SMS fallback.
- Every send attempt is written to a notifications table (channel, recipient, body, status, error, sent_at).
- Written and tested but not sent by anything yet (v1 has no scheduler; see Before going live): a week-of reminder SMS to the parent, and a review-request SMS 1 to 2 days after the party with the Google review link from `GOOGLE_REVIEW_URL`.
- Twilio trial only delivers to verified numbers and prefixes messages with a trial notice: verify Tyler's number now, the owner's before demo day. If Twilio env vars are missing, log the message body instead so tests and dev never depend on Twilio.

## Non-goals for demo v1
Refund automation, balance collection, chatbot, Start.gg sync, memberships, owner and request email (email is parent booking confirmation only), coupon codes, editing a confirmed booking, multi-slot days, staff accounts, calendar sync, venue buyout lead form (the existing site form already handles it), package upgrade suggestions.

## Demo script
On a phone, in under 3 minutes:
1. Open the page: pitch, "Private venue parties from $399", the tier strip, and the weekend grid with Open / Booked / On hold written on the cards.
2. Tap an open Saturday (forward click 1): the package page opens with "Holding Sat, {date} at 10:00 AM".
3. SELECT Standard (click 2): add-ons page; the sticky bar reads $399.
4. Toggle one Pizza Pack: bar updates to $439 without navigating. Tap "Lock in {date} ($100 today)" (click 3).
5. Checkout page: live countdown, enter details, set headcount 18: total updates to $544, "$544 total, $100 today, $444 day of party".
6. Tap "Pay $100 & lock it in" (click 4), pay with the Square test card, land on the confirmation page, which flips from "confirming your payment" to booked within seconds, with Add to Calendar buttons.
7. Confirmation SMS (and email) arrives on the phone; owner SMS (and Discord post, if configured) arrives on the second phone.
8. Second phone: the grid shows that Saturday as Booked; tapping it does nothing.
9. Admin page already shows the booking.
10. Tap "Want a weekday?", enter a date: the same pages run in request mode and end with Send request; the owner phone gets the priced request text, and admin shows the request.

## Required tests
1. `test_concurrent_holds`: N parallel hold requests for one slot (real parallel Postgres connections) yield exactly one success and N-1 conflicts. Each request uses a distinct client IP so the IP cap (429) cannot mask the slot conflict (409).
2. `test_webhook_replay`: identical webhook delivered twice, and payment.created followed by payment.updated for the same payment, yield one booking and one recorded notification per recipient.
3. `test_webhook_bad_signature`: tampered body or wrong key yields 401 and no state change.
4. `test_webhook_ignores_incomplete`: a payment event with a non-COMPLETED status returns 200 and creates nothing.
5. `test_pricing_table`: table-driven cases covering included kids boundaries (15/16, 20/21, 25/26), included-free add-ons per package, pack quantities, Highlight Reel without Tournament rejected.
6. `test_expired_hold_payment`: payment event referencing an expired hold creates a flag, not a booking.
7. `test_hold_limits`: a sixth active hold from one IP returns 429; checkout with a phone number (or email) that already has another active hold returns 429; a hold on a slot inside the lead-time window is rejected; checkout on an expired hold returns 409 and books nothing.
8. `test_requests`: a weekday request stores a server-priced snapshot and produces exactly one owner notification per configured channel; a second `new` request for the same phone and date returns 409; a weekend request is rejected while `WEEKEND_DEFAULT=open` and accepted when `request`; a hold is refused while `WEEKEND_DEFAULT=request`; a request inside the lead-time window is rejected.

## Deployment notes
- Alembic migrations run from the laptop against Neon's direct connection string. Lambda uses Neon's pooled connection string.
- CORS: the API allows only the frontend origin, read from env.
- If the AWS deploy fights back on demo week, demo from the laptop through ngrok and deploy after the owner says yes. The demo does not depend on AWS.

## Before going live
- Owner reviews every price and rule in this file.
- Live Square credentials entered by the owner in the hosting environment, never in the repo.
- US business texting registration (A2P 10DLC) is required before Twilio delivers to real parents; it takes days and a small fee. Discord webhook is the zero-cost owner channel in the meantime.
- Point book.encoregaming.us at CloudFront and swap the "Book a Party" link on the existing site.
- The checkout risk-reversal line ("Free reschedule up to 7 days before your party. Deposit fully refundable for 14 days.") promises more than the site's 7-day rule and is applied by hand today; the owner must approve the wording, or it reverts to the plain 7-day rule.
- A daily scheduled job (EventBridge + Lambda) to send the week-of reminder and the day-after review request; the templates exist and are tested, nothing triggers them yet.
- Self-serve add-on editing for a confirmed booking (the confirmation page's "add anytime" line currently points at the phone number). Additions only, priced by the same quote function, balance collected day-of.
- Requests stage 2: the owner SMS/Discord carries approve and decline links, each a single-use token; the link opens a page whose button POSTs the action (never acted on GET); approve creates a 24-hour hold and texts the parent the checkout link; decline texts a polite no with open alternatives.
- A weekly nudge to the owner summarizing `new` requests not yet acted on.
- Pay-to-request (optional): the parent pays the deposit up front to submit a request, with an automatic Square refund if the request is declined or expires unanswered.
- Pay in full as an alternative to the $100 deposit. Requires automated Square refunds first: the 7-day full-refund rule is applied by hand today, which is tolerable for $100 and not for a full balance.
