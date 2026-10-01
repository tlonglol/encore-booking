# Encore Party Booking, Demo v1 Spec

## Goal
A parent picks a date that is verified open, sees a live price, and the slot is held exclusively for them. Paying the $100 deposit (his existing policy) instantly locks the date and sends a confirmation text and email, with zero human involvement. The date is guaranteed before any money moves. The owner gets a text the second it happens. Demoable on a phone, styled to match encoregaming.us.

This is a standalone system on its own URL. It never touches the existing website or the owner's live Square account (sandbox only until he approves).

## Stack
- Backend: FastAPI (Python 3.12). Local dev with uvicorn. Deploy target: AWS Lambda via Mangum behind a Function URL, provisioned with Terraform.
- DB: Neon Postgres free tier for dev and prod. SQLAlchemy + Alembic migrations. Tests run against a local Postgres started with Docker Compose (`docker compose up -d db`), never SQLite: the constraints under test are Postgres behavior.
- Payments: Square Sandbox, hosted Checkout (Payment Links) + Webhooks.
- Notifications: Twilio trial SMS (delivers only to verified numbers), with a log-to-console fallback when Twilio env vars are absent. Parent confirmation email over SMTP (stdlib `smtplib`, any provider's SMTP relay), with the same log-to-console fallback when the SMTP env vars are absent. Optional Discord webhook for owner notifications if `DISCORD_WEBHOOK_URL` is set.
- Frontend: static HTML + vanilla JS, responsive (desktop and phone are both first-class), deployed to S3 + CloudFront. `index.html` is a recreation of the live Birthday Parties page at encoregaming.us, with the booking engine replacing the old booking form in the last section. Dark theme matching the Encore site (near-black background, blue accents, condensed uppercase headings).
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
- Balance: total minus deposit, due at the party, collected in person. "Due at the party" is the one phrase for it everywhere (rail, info page, confirmation, SMS, email, calendar file).
- Money reads like a receipt, never a promo banner: every currency amount renders in the body font in sentence case, with tabular figures, and never in the condensed display-caps face. Amounts are modest and bold rather than oversized, and no money line stacks caps, oversized type and colour together.
- Tax is never left unsaid. `TAX_MODE` (env: `included` | `line_item`, default `included`) decides how: `included` renders an "Includes sales tax" note under the total, and `line_item` adds a server-computed "Sales tax" line to the quote at `TAX_RATE_PERCENT` (a decimal percent, computed with exact decimal arithmetic rounded half up) so it is part of the snapshot and the total like any other line. The default needs the owner's answer before launch (see Before going live). Out of scope to collect, but the confirmation page and SMS must state the remaining balance.
- The total is snapshotted on the hold as a server-priced draft while the parent builds the party, then re-priced and re-snapshotted at the checkout call; the booking copies the checkout snapshot. Later price changes never alter a snapshotted hold or booking. A draft write never creates a payment link and never finalizes anything.
- Refund and reschedule promise, printed above the pay button: "Cancel or reschedule free up to 7 days before your party. Inside 7 days, your deposit isn't refundable, but you can reschedule once." This matches the live site's 7-day rule and adds one courtesy reschedule inside the window; reschedules are arranged by phone. Store the data needed to apply it (booking created_at, event date, cancelled_at); refund automation is out of scope for demo.

## Inventory model
- Weekend mornings are open for online booking by default; the owner blocks exceptions. Weekdays are request-only (see Party requests). `WEEKEND_DEFAULT` (env: `open` | `request`, default `open`) flips weekends into the same request-only behavior; while it is `request`, holds are refused server-side for every date.
- One party per slot (every package is a full-venue buyout, so a slot can hold exactly one booking).
- A slot is (date, start_time). Demo rule: one bookable slot per weekend day at 10:00 AM America/New_York, stored as timestamptz in UTC. Duration is display-only in v1. Weekday request dates have no slot row; a request stores its own date and preferred start time.
- A generator materializes weekend slots for the weekends being asked about, from a template: 8 by default, and up to a hard cap of 52 (a year) when the parent expands the list. It is idempotent and runs lazily whenever availability is requested. Owner can block a date in admin. Blocking is refused (409) while the slot has an active hold or a live booking; otherwise a parent could pay into a blocked slot. Weekday dates cannot be blocked in v1; the owner simply declines their requests.
- Availability covers every date from today through the last day of the 8th weekend. Slot states are computed, not stored: open, held (active unexpired hold exists), booked, blocked, past (inside the lead-time window), request (weekdays; weekends too when `WEEKEND_DEFAULT=request`).
- The date grid is a weekend-first list of the next 8 weekends (Saturday and Sunday cards grouped by weekend), each card showing the date, 10:00 AM, and the state as a word on the card, never by color alone: Open, On hold ("may free up in a few minutes"), Booked, Unavailable (blocked or inside the lead-time window; the API keeps blocked and past distinct because admin unblocks), and Request (weekends while `WEEKEND_DEFAULT=request`). Booked and held dates stay visible but disabled; they are social proof, never hidden. The grid re-fetches availability every 30 seconds and when the tab regains focus, so a lapsed hold flips back to open without a reload. A "Show more weekends" button under the grid loads 8 more weekends each press (`GET /api/availability?weekends=N`, N capped at 52 by the server); past the cap the button gives way to a line pointing at the phone number and the waitlist. Weekends loaded this way obey every rule the first 8 do: same states, same lead time, same exclusivity.

## Landing page
`index.html` recreates the live Birthday Parties page section for section, using its real copy, and ends with the booking engine where the old booking form was. Content that is not the engine is static: it never decides a price and never writes anything. Package and add-on prices shown here come from `GET /api/catalog`, so there is no second copy of the prices in JavaScript.

1. **Nav**: ENCORE ESPORTS wordmark, the live site's links (Home, Events, Games, Food Menu, Monroe Esports, Birthday Parties, Contact, all absolute to encoregaming.us), and a BOOK A PARTY button pinned top-right that scrolls to the engine. Sticky.
2. **Hero**: "Level Up / Their Birthday" (solid white over blue-outlined), the site's intro paragraph, the four chips. No gallery or photo placeholders: we have no photos.
3. **Stats strip**: Platforms / Staff Host / Ages / Available.
4. **Reviews banner**: 4.9 · 161 Google reviews.
5. **Choose Your Package**: three cards from `GET /api/catalog` with the site's full feature lists, including the features a tier does *not* include shown muted, MOST POPULAR on Plus. Each card's **Book Now** button is Door A: it opens the funnel at `/book/date` with that tier already active.
6. **Rent the whole place**: venue-buyout callout linking to the live site's contact page. Off-hours buyouts stay with the existing site (see Non-goals).
7. **Premium Add-ons**: display only, prices from the catalog. Selection happens in the engine.
8. **Parent FAQ**: the site's six cards.
9. **What Parents Say**: the site's three Google review quotes.
10. **Book Your Party**: Door B. A "See open weekends" button opens `/book/date` with no tier active, and the panel repeats "from $399" so a price is visible before the first click.

Layout: responsive, not mobile-only. At desktop the container matches the live site (max-width 1100px, 2rem side padding) with the site's grids (3-column packages, 2-column add-ons and FAQ, 3-column quotes, 4-column stats); a single breakpoint at 768px collapses every grid to one column. The demo happens on a phone, so phone width stays first-class.

The landing page never holds a date. Every path into the funnel leaves it for `/book/*`.

## Booking funnel (the money path)
A single-page app served at `/book/*`, separate from the landing page. The shell (banner, right rail, sticky bar) renders once and never moves; only the left content block swaps between steps.

**Two doors, one merge point.**
- **Door A (package-led)**: a Book Now button on a landing-page package card opens `/book/date` with that tier active. Selecting a date and confirming it goes straight to Extras: the package decision is already made.
- **Door B (date-led)**: "See open weekends" opens `/book/date` with no tier active and prices reading "from $399". Confirming a date goes to Select Plan, then Extras.
- A Door B parent who taps a package tab before tapping a date has merged into Door A: confirming a date now goes straight to Extras.
- Both doors converge on Extras → Info + Deposit → Confirmation.
- Forward clicks on the happy path from the date page: Confirm date, select, Lock in, Pay, preceded by a date tap that only selects. Door A spends its "select" at the door, so it reaches payment in three forward clicks after the date tap.

**State and URLs.**
- One booking draft, `{date, package, addons, headcount, holdExpiry}`, kept in sessionStorage and mirrored to the hold row in Postgres once the hold starts (`PATCH /api/holds/{id}/draft`, which prices server-side and stores the snapshot; the client never sends a price, and a draft write never creates a payment link).
- `pushState` per step so browser Back/Forward walk steps with no page load; `popstate` re-renders. Sub-changes inside a step (tab switches, add-on toggles, headcount) use `replaceState`, so Back walks steps and never un-toggles an add-on.
- The server returns the same shell for every `/book/*` path. On a cold load (refresh or a pasted link) the app reads the URL and restores the draft from sessionStorage, then from the hold row.
- Deep-entry guard: if the URL names a later step than the draft supports, the parent lands on the earliest incomplete step instead.
- Refresh mid-funnel restores the same step with everything intact.

**Persistent frame.**
- Banner: Encore logo and phone number, fixed, never moves after the first click.
- Right rail, "Party Summary", numbered: 1 Date · 2 Package · 3 Extras · 4 Info & Deposit. Completed sections keep their text permanently; only the highlight moves. Each completed section has an edit control that jumps back without clearing downstream choices unless they are invalidated (a date change re-checks the hold; a package change keeps the add-ons that still apply, drops the ones the new tier includes free).
- The active package line in the rail carries that tier's top two or three unlocks, so the selection explains itself.
- Deposit and total are pinned at the rail's foot next to the forward button: the money and the advance click live in one place that never moves. The money block is a step-aware three-level hierarchy, computed from the booking draft and switched by the active step (the same state that drives the rail's step highlight, with no second copy of step logic):
  - **Pre-payment steps (date, package, extras)**: line 1 is the total, primary white (the decision number while configuring). Line 2 is "$100 today" in the active tier accent, bold, followed on the same line by the remainder in muted plain text: " · $689 due at the party". There is no separate reassurance sentence and no third line: the two amounts together already say what is paid now and what is paid later. The remainder never takes the accent colour, which marks the action amount only.
  - **Info + Deposit step**: the rail is a receipt, always open, no DETAILS expander. It lists the party's line items, then a thin divider, then Total, Due today and Due at the party, labels left and amounts right-aligned. Total and Due today are bold, Due today in the tier accent. Under Total sits the tax line or the "Includes sales tax" note from Money. On a phone, where the rail is collapsed to the bar, the bar reads "Due today: $100" in the tier accent and the receipt renders inline in the left column instead, so there is exactly one money block per viewport: the rail on desktop, the inline block on a phone.
  - **A weekday request** pays nothing now, so it keeps the pre-payment hierarchy on every step.
  - Amounts update live as package, add-ons or headcount change, with the same subtle crossfade used for tier price changes. Itemization stays behind DETAILS only.
  - **Every change attributes itself.** When an add-on toggle, a quantity stepper or the kids stepper moves the total, a small chip appears beside it naming the change with its sign and amount: "+$60 VR Gaming", "−$40 Pizza Pack", "+$35 · 26th kid". It appears with the new total, fades after about 1.5 seconds, and there is only ever one: a new change replaces it at once, chips never stack, queue or persist. Additions take the fixed brand blue, the same colour as the add-on card's own price and its selected border, so the toggle, the price and the chip speak one colour; removals are muted. Nothing bounces or flashes. The phone bar carries the same chip, compacted. Kids at or below the package cap cost nothing and so get no chip, and an action that drops a dependent add-on (turning Tournament off also drops Highlight Reel) chips the full amount under the name of the thing the parent actually clicked.
  - The DETAILS button carries a live count of priced line items ("DETAILS · 4 items"): the package, each add-on, the kid overage and the tax line when there is one. It is the same set the payment step's receipt itemizes, so the two always agree.
  - The rail's Extras line stays names-only ("20 kids · Pizza Pack") and never shows prices; the payment step's receipt is the only full ledger.
  - **Phones**: the sticky bar mirrors this compacted. Pre-payment shows the total prominent with "$100 today" in accent beside it; the remainder collapses into the expanded summary sheet, where it renders muted after the deposit. On the payment step the bar flips to "Due now: $100". The Confirm button's "Confirming holds this date for 15 minutes." microcopy stays below the button.
- On phones the rail collapses into the sticky bar, with the summary as a tap-to-expand sheet.

**Tier accents.** Each tier owns an accent colour used wherever it is referenced (Standard cyan, Plus Encore blue, Premium violet). On a tab switch the date-card prices change colour with a short crossfade and the numbers swap instantly (no counting animation); the sticky total, the rail's package line, the SELECT button and the step highlight all inherit it. Colour lives in text, never in card backgrounds. `prefers-reduced-motion` skips the colour crossfade.

**Explaining the tiers.** One shared package-feature structure feeds all four surfaces, so they cannot drift: the landing-page cards, the diff strip, the tab popovers and the compare drawer.
- Diff strip: one line under the tab bar, in the new tier's colour, updating on the same beat as the price animation, e.g. "Plus adds: 6 games, decorations + balloon tower, favor bags". Moving up a tier shows what it adds. Moving down, or landing on the base tier, shows only what that tier includes ("Standard includes: 4 games, private buyout, birthday banner"). The line carries no price, since each tab already shows its own, and there is no "drops" wording and no minus amount anywhere; the compare drawer shows what a lower tier lacks.
- Tab popovers: an info control per tab opens a compact card with that tier's unlocks in its colour and the below-tier items greyed.
- "Compare all plans" opens a drawer over the date page. It never leaves the funnel.

1. **Date** (`/book/date`): the next 8 weekends. OPEN is tappable, BOOKED stays visible but dead (social proof), HELD is a third distinct disabled state for someone else's live 15-minute hold. A package tab strip sits above the grid with each tier's price in the tab. Tapping a date only selects it (shown as "Selected"): nothing is held and the page does not advance. The forward button in the rail then reads "Confirm {Mon D}", and pressing it calls `POST /api/holds` with {slot_id}: an anonymous 15-minute hold, no contact details yet, the database constraint decides who wins (see Correctness). Confirming a different open date sends `replace_hold_id`, so the previous hold is cancelled in the same transaction and a parent swaps dates instead of stacking them. Under the grid, "Don't see your date? Get notified" captures an email only (`POST /api/waitlist`). Weekday dates run the same steps in request mode (see Party requests).
   - **Birthday finder**: a small feature card under the tab strip and its colored line, so it is not missed: it spans the content column, is bordered in the fixed brand blue (never the tier accent), and sits in the same family as the Compare All Plans button. It holds a cake line icon (lucide "cake", brand blue, no emoji), the bold title "When's the big day?", a small "(optional)" tag, one muted benefit line ("We'll line up the closest open weekends."), and the date entry inline: one compact button-like control, a brand-blue calendar icon followed by three small numeric month / day / year boxes as a single unit, that supports both ways of entering a date. Clicking or tapping a month / day / year box focuses it for typing (typing advances to the next box, and Backspace in an empty box steps back to the previous one). A dedicated calendar button sits at the control's right edge, its full height and about 40px wide, set off by a divider, brightening on hover: it is the only thing that opens the native calendar. Clicking anywhere else in the control is for typing. Typed and picked dates share one validation path, and the weekend grid reorganizes only once a complete, valid date is entered, never part-way through typing. Editing or clearing one box never wipes the others or the applied birthday; only a complete real date applies, and only emptying all three (or the Clear link) removes it. It stays compact enough that the first weekend row still peeks above the fold. It is never required, and entry is constrained to a real calendar date (never free text). Entering a date reorganizes the grid into two sections: "Closest to the birthday" pinned on top, holding the two or three relevant weekends with honest labels ("weekend of", "4 days before", "3 days after"), and "All weekends", a collapsed expander holding the rest (nothing is ever removed). A weekday birthday shows the weekend before and the weekend after; a weekend birthday shows its own weekend first, with the weekends before and after as alternates beneath. Every suggested weekend shows its live OPEN / BOOKED / ON HOLD state and none is ever pre-selected. If every suggested weekend is unavailable, the section extends to the nearest open weekend with a plain label ("Closest open: Sat Nov 7, 3 weeks after") and offers "Get notified". A birthday past the default window loads and pins that range on demand, which is what keeps the default at 8 weekends. A past date is refused politely, a date beyond what is bookable online says so and points at the phone and the waitlist, and clearing the input restores the full default grid. The birthday saves onto the draft (`birthday` on `PATCH /api/holds/{id}/draft`, and again on checkout and on a request), is validated server-side (today through 366 days out), and is copied to the booking.
2. **Select Plan** (`/book/plan`, Door B only): the three cards, Plus keeping its Most Popular badge, nothing pre-selected. SELECT advances immediately: no separate Continue, no confirm step. The header carries the held date.
3. **Extras** (`/book/addons`): toggles and steppers never navigate; they only update the total. Every toggle starts off and every stepper at 0, no exceptions. Included-free add-ons show "Included" and add $0; Highlight Reel stays disabled with a one-line reason until Tournament + Livestream is on. **Pizza and drinks overlap**: the Pizza Pack already includes drinks ("2 large pizzas + drinks, feeds ~10 kids"), so while Pizza Pack is 1 or more the Drinks Pack card shows one inline note in the green "included" style, computed from the headcount, e.g. "Your Pizza Pack includes drinks. It covers ~10 of your 18 kids. Add a Drinks Pack to cover everyone." The note shows whichever add-on was picked first, and the two steppers stay fully independent: nothing is auto-zeroed, disabled or hidden, and removing the drinks is the parent's call. The headcount stepper lives here, defaulting to the package's included cap, with overage priced live in the tier colour ("+2 kids · +$70") so the total never jumps on a later step. The advancing click is the forward button, now active: "Lock in {Mon D} ($100 today)", with the full total beside it.
4. **Info + Deposit** (`/book/info`), one page, never split: under the shell's persistent hold header (see Hold timer below). Five fields, in order: parent email (first, persisted on blur via `PATCH /api/holds/{id}/email` so abandoners are recoverable), parent name, phone, kid's name, kid's age. Headcount was captured on Extras. The money and the policy live in the rail, next to the button that spends it; the left column carries only the hold header and the form. On a phone the receipt renders inline here instead, since the rail is collapsed. Nothing is added to the total on this step. Directly above the button, in the rail where the click happens, a compact form of the promise from Money naming the real cutoff date rather than a count of days: "Free cancel or reschedule until Nov 1 · details", the date computed as the party date minus 7 days. "details" expands in place to the full wording including the hour the free window closes (the party's start time on that day), since the backend refunds on a 7-days-before-the-start basis. The left column is the hold header and the form only. The only button: "Pay $100 & lock it in", which calls `POST /api/holds/{id}/checkout` with {package, kids, addons, first_name, last_name, phone, email, kid_name, kid_age}, which:
   - verifies the hold is still `pending_payment` and unexpired (409 otherwise, nothing changed),
   - validates inputs (package rules, kid cap, add-on dependencies, phone normalized to E.164, valid email) and prices server-side; the client never sends a price,
   - snapshots package, kids, add-ons, line items, and total onto the hold,
   - enforces the phone and email caps (see Correctness),
   - creates the Square payment link for the $100 deposit using hold_id as the Square `idempotency_key` so a retry never creates a second link, with `redirect_url` pointing at the confirmation page with the hold_id in the query string, and stores the returned order_id on the hold (an existing link is reused),
   - if creating the payment link fails, returns 502 and keeps the hold: the parent's date is not released over a Square blip, and the button can be retried,
   - returns {checkout_url, party}; the page then redirects to Square's hosted checkout (sandbox test card).
5. Square webhook hits `POST /api/webhooks/square`. Handler must, in this order: verify the HMAC signature header against the raw body, acknowledge with 200 and ignore any event that is not a payment with status COMPLETED, check event-level and payment-level idempotency, find the hold by the payment's order_id, verify the amount matches the hold's own `deposit_cents` in USD, convert hold to booking inside one transaction (this is the atomic hold-to-booked flip; the unique indexes make it safe against webhook retries), commit, then send notifications (best-effort, after commit, never inside the transaction).
6. **Confirmation (`confirm.html?hold={hold_id}`)**, a real page because Square redirects to it: polls `GET /api/holds/{hold_id}` until the status leaves `pending_payment`. Statuses: `pending_payment`, `booked` (confirmation_code and booking details), `expired`, `cancelled` (admin cancel or a swapped date; plain "this hold was cancelled" message), `paid_after_expiry` (show: "We received your payment. This slot's hold had expired, so we'll call you within the hour to lock in your date."). A booked confirmation shows date, time, package, add-ons, the birthday line ("Leo turns 8 · Oct 14") when one was given, total, deposit paid, due at the party, what happens next ("Encore will text you the week of your party"), Add to Calendar buttons (a Google Calendar link and an Apple/.ics file from `GET /api/bookings/{code}/calendar.ics`), and the deferred upsell line: "You can add Pizza Packs, Drinks, or VR anytime before the party" pointing at (646) 203-8087 (self-serve add-on editing stays out of v1; see Before going live). The permanent confirmation is `confirm.html?code={confirmation_code}` backed by `GET /api/bookings/{code}`. Confirmation codes are random and unguessable; the endpoint returns only what the confirmation page needs.
   - The hold_id passes through Square's redirect and browser history, so a `booked` hold returns only: confirmation_code, first name, package, date, time, kids, total, balance, and the birthday child's first name, age and birthday (only once booked, never on a pending hold). Never phone, email, or notes. `GET /api/bookings/{code}` returns the same fields.

**Hold timer.** The base hold is 15 minutes and the hold row's expiry timestamp on the server is the single source of truth. The client only renders time remaining from it: on a refresh, a cold load, a tab returning to the foreground or a page restored from the back/forward cache it asks the server how long is left (`GET /api/holds/{id}` seconds_remaining) and never restarts the clock locally.
- One timer component lives in the persistent shell, attached to the "HOLDING SUN, OCT 4 · 10:00 AM" header line above the step content. It reads the hold from the draft state, renders on every step where a hold exists (including the date step on the way back), is never duplicated in the rail, and never unmounts or resets when the parent moves between steps. A weekday request has no hold, so its header reads "Requesting …" with no timer.
- Display: the hold line is one unit — "HOLDING SAT, OCT 10 · 10:00 AM · 14:32 · Change date". The clock is live mm:ss from the first second, in tabular figures so the digits do not jiggle, one step up from body text and muted so it does not compete with the step title; under 2 minutes it turns the tier accent colour, which is its only state change. A real countdown shown plainly is a trust signal rather than pressure — it is the pattern parents already know from ticketing, and the seconds prove the hold is real and monotonic. Nothing else on the page counts down.
- "Change date" sits in the same unit, right after the clock: it returns to the date step with the hold intact. Date, clock and the way out in one visual unit means nobody has to wonder what is counting down or what happens to their date at zero; pressure with a visible exit reads as logistics, pressure without one reads as a tactic. It is hidden on the date step itself, where it would do nothing, and in request mode, which has no hold.
- On a phone the line wraps into two: the date on the first, the clock and "Change date" on the second.
- The server enforces expiry: a pay call on a dead hold fails cleanly (409, nothing changes). When the client sees the hold has expired, by its own countdown reaching zero, by the server saying so on return, or by a rejected pay call, it shows a modal: "Still there? Your hold on Oct 4 expired." with a Re-hold button and a "Pick another date" button. Re-hold asks the server for a fresh hold on the same slot (so availability is re-checked by the same database constraint as any hold): on success the 15 minutes restart, and the package, add-ons, headcount and typed details are kept and re-saved on the new hold; if someone else took the date, the modal says so and offers only the way back to the grid. The Re-hold button is the only thing that ever restarts the timer, and the modal has no other way out.
- Returning to the date step mid-hold shows the held date as SELECTED with the hold intact. Confirming a different date swaps the hold: the old one is released in the same transaction (its date flips back to open) and the new one starts with a fresh 15 minutes.

**Trust rules, hard constraints.** A price is visible before the first click ("from $399" on the hero and the date page). No fake urgency, no viewer counts, no timer that is not the real hold. Nothing pre-checked. Decline copy stays neutral. No fee that first appears at checkout, which is why there is no reservation fee and the word "estimated" appears nowhere. Everything must survive being screenshotted in a local parents' Facebook group.

Square sends its own payment receipt email; that covers the payment record. Our SMS and email cover the booking.

## Party requests (stage 1)
Weekdays (and weekends while `WEEKEND_DEFAULT=request`) take requests instead of holds. A request is an inquiry: no hold, no payment, no slot is taken, and the owner replies personally.
1. "Want a weekday? Tell us your date" (or tapping a Request date card) runs the same engine steps in request mode: no hold is created, the header reads "Requesting {Day}, {Mon D}", there is no countdown, and the Confirm step adds a preferred start time (9:00 AM to 8:00 PM) and ends with one primary action, "Send request", instead of payment.
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
- **Checkout on a dead hold**: the checkout call on an expired, cancelled, or already-booked hold returns 409 and changes nothing; a hold that carries a payment link always carries a complete snapshot (database check constraint). Draft writes obey the same rule: a draft on a dead hold is refused, and a draft never writes contact details, a payment link, or a booking.
- **Pricing**: computed only on the server, from one function, covered by table-driven tests.

## Admin (demo scope)
- Single owner login: password from env (compared with constant-time comparison), HTTPS-only session cookie. Nothing fancier.
- Bookings list: date, time, package, kids, kid's name and age, add-ons, total, deposit status, contact, notes, and the day-of birthday line ("Leo turns 8 Oct 14, party Oct 10") when a birthday was given.
- Block / unblock a date.
- Manually cancel a booking (frees the slot, records cancelled_at; refund handled by owner in Square for now).
- Payment flags visible (expired-hold payments, duplicate payments, amount mismatches, unmatched orders).
- Party requests list: date, preferred time, package, kids, add-ons, total, contact, notes, birthday when given, status. Read-only in stage 1.
- Notification log with failures visible.
- Leads: the waitlist emails, and abandoned checkouts (expired or cancelled holds that captured an email, last 14 days) so the owner can follow up.

## Notifications
- Parent SMS: "You're booked! {Package} party {date} {time} at Encore Esports, {kids} kids. $100 deposit received, ${x} due at the party. 538 Main St, 2nd floor, entrance on Division St. Questions: (646) 203-8087. Details: {confirmation link}"
  - {confirmation link} is the permanent `confirm.html?code={confirmation_code}` URL on the frontend origin, so a parent who closes the tab can get back to the confirmation.
- Owner SMS: "NEW PARTY: {package} {date} {time}, {kids} kids, {addons}. Deposit paid. {parent name} {phone}."
- Owner Discord webhook (optional): same content as the owner SMS, posted if `DISCORD_WEBHOOK_URL` is set. Free and permanent, unlike SMS.
- Owner request SMS (and Discord): "PARTY REQUEST: {package} {date} at {preferred time}, {kids} kids, {addons}. Total ${x}. {parent name} {phone} {email}." Notes stay in admin, not in the SMS.
- Parent email (channel `email`, same after-commit timing and the same one-claim-per-(booking, audience, channel) rule as the SMS): subject "You're booked: {Package} party {date}". Body: confirmation code and the permanent `confirm.html?code={confirmation_code}` link, date and time, package and kids, itemized total (from the booking's line items), the $100 deposit received, due at the party, "538 Main St, 2nd floor, entrance on Division St", the cancel-and-reschedule promise from Money, and (646) 203-8087. When SMTP credentials (`SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`) are not all set, the body is logged to the console and the row is marked `logged`, exactly like the SMS fallback.
- Every send attempt is written to a notifications table (channel, recipient, body, status, error, sent_at).
- Written and tested but not sent by anything yet (v1 has no scheduler; see Before going live): a week-of reminder SMS to the parent, and a review-request SMS 1 to 2 days after the party with the Google review link from `GOOGLE_REVIEW_URL`.
- Twilio trial only delivers to verified numbers and prefixes messages with a trial notice: verify Tyler's number now, the owner's before demo day. If Twilio env vars are missing, log the message body instead so tests and dev never depend on Twilio.

## Non-goals for demo v1
Refund automation, balance collection, chatbot, Start.gg sync, memberships, owner and request email (email is parent booking confirmation only), coupon codes, editing a confirmed booking, multi-slot days, staff accounts, calendar sync, venue buyout lead form (the existing site form already handles it), package upgrade suggestions.

## Demo script
On a phone, in under 3 minutes:
1. Open the page: the recreated Birthday Parties page (hero, stats, reviews, packages, add-ons, FAQ, quotes), ending in Book Your Party with the weekend grid and Open / Booked / On hold written on the cards.
2. Tap Book Now on Plus (Door A): the funnel opens at /book/date with the Plus tab active and Plus prices on the date cards.
3. Tap an open Saturday (forward click 1): the hold starts and Extras opens, the package decision already made.
4. Toggle one Pizza Pack and set headcount to 18: the rail and bar update without navigating. Tap "Lock in {date} ($100 today)" (click 2).
5. Info + Deposit: the hold timer above the form, five fields, "Due now: $100" then "Total $544 · $444 due at the party".
6. Tap "Pay $100 & lock it in" (click 3), pay with the Square test card, land on the confirmation page, which flips from "confirming your payment" to booked within seconds, with Add to Calendar buttons.
7. Confirmation SMS (and email) arrives on the phone; owner SMS (and Discord post, if configured) arrives on the second phone.
8. Second phone: the grid shows that Saturday as Booked; tapping it does nothing.
9. Admin page already shows the booking.
10. Back on the landing page, "See open weekends" (Door B) opens the same funnel with no tier active and "from $399" on the cards; a tab switch recolours the prices and the diff strip explains what changes.
11. Tap "Want a weekday?", enter a date: the same steps run in request mode and end with Send request; the owner phone gets the priced request text, and admin shows the request.

## Required tests
1. `test_concurrent_holds`: N parallel hold requests for one slot (real parallel Postgres connections) yield exactly one success and N-1 conflicts. Each request uses a distinct client IP so the IP cap (429) cannot mask the slot conflict (409).
2. `test_webhook_replay`: identical webhook delivered twice, and payment.created followed by payment.updated for the same payment, yield one booking and one recorded notification per recipient.
3. `test_webhook_bad_signature`: tampered body or wrong key yields 401 and no state change.
4. `test_webhook_ignores_incomplete`: a payment event with a non-COMPLETED status returns 200 and creates nothing.
5. `test_pricing_table`: table-driven cases covering included kids boundaries (15/16, 20/21, 25/26), included-free add-ons per package, pack quantities, Highlight Reel without Tournament rejected.
6. `test_expired_hold_payment`: payment event referencing an expired hold creates a flag, not a booking.
7. `test_hold_limits`: a sixth active hold from one IP returns 429; checkout with a phone number (or email) that already has another active hold returns 429; a hold on a slot inside the lead-time window is rejected; checkout on an expired hold returns 409 and books nothing.
8. `test_birthday`: a birthday saves on the draft and is validated server-side (a past date and one beyond 366 days are refused), is copied to the booking, appears in the booked confirmation and the admin bookings list as the day-of line, and is never exposed on a pending hold.
9. `test_hold_draft`: a draft write prices server-side and stores the snapshot, ignores any price the client sends, is refused on an expired hold, and never creates a payment link or a booking.
10. `test_requests`: a weekday request stores a server-priced snapshot and produces exactly one owner notification per configured channel; a second `new` request for the same phone and date returns 409; a weekend request is rejected while `WEEKEND_DEFAULT=open` and accepted when `request`; a hold is refused while `WEEKEND_DEFAULT=request`; a request inside the lead-time window is rejected.

## Deployment notes
- Alembic migrations run from the laptop against Neon's direct connection string. Lambda uses Neon's pooled connection string.
- CORS: the API allows only the frontend origin, read from env.
- If the AWS deploy fights back on demo week, demo from the laptop through ngrok and deploy after the owner says yes. The demo does not depend on AWS.

## Before going live
- Owner reviews every price and rule in this file.
- Live Square credentials entered by the owner in the hosting environment, never in the repo.
- US business texting registration (A2P 10DLC) is required before Twilio delivers to real parents; it takes days and a small fee. Discord webhook is the zero-cost owner channel in the meantime.
- Point book.encoregaming.us at CloudFront and swap the "Book a Party" link on the existing site.
- Sales tax: confirm whether the advertised prices already include it (`TAX_MODE=included`, the default, which prints "Includes sales tax" under the total) or whether tax is added at checkout (`TAX_MODE=line_item` plus `TAX_RATE_PERCENT`, which adds a Sales tax line to every quote and raises the total). The receipt states one or the other; it is never silent.
- The refund and reschedule promise is applied by hand (the owner refunds in Square and rebooks by phone); the owner should confirm the "one courtesy reschedule inside 7 days" wording before launch.
- A daily scheduled job (EventBridge + Lambda) to send the week-of reminder and the day-after review request; the templates exist and are tested, nothing triggers them yet.
- Split the pizza and drinks catalog: Pizza Pack becomes pizza only, repriced to roughly $35, and Drinks Pack owns all drinks. This needs the owner's sign-off and a price change in `pricing.py`. If approved, the Extras note simplifies to pure capacity math ("1 Pizza Pack feeds ~10 of your 18 kids").
- Self-serve add-on editing for a confirmed booking (the confirmation page's "add anytime" line currently points at the phone number). Additions only, priced by the same quote function, balance collected at the party.
- Requests stage 2: the owner SMS/Discord carries approve and decline links, each a single-use token; the link opens a page whose button POSTs the action (never acted on GET); approve creates a 24-hour hold and texts the parent the checkout link; decline texts a polite no with open alternatives.
- A weekly nudge to the owner summarizing `new` requests not yet acted on.
- Pay in full as an alternative to the $100 deposit, as a second chip on the Info step. Requires automated Square refunds first: refunding a full party by hand inside the promise window is a much bigger liability than refunding a $100 deposit.
- Pay-to-request (optional): the parent pays the deposit up front to submit a request, with an automatic Square refund if the request is declined or expires unanswered.
