# Live PayPal sandbox run record

Run on 2026-10-02 (JST, about 22:05-22:20) from a Mac against `https://api-m.sandbox.paypal.com`,
with a sandbox REST app (client id / secret kept outside the repository; never printed or logged).
Order ids are shown by their last 4 characters only.

## 1. Authentication
`POST /v1/oauth2/token` (client credentials): HTTP 200, token received.

## 2. Buyer approval: which route was used
| Route | Result |
|---|---|
| Default (`payment_source.paypal`, buyer approves on PayPal's page) | Order create is real: HTTP 200, `PAYER_ACTION_REQUIRED`, a `payer-action` link (order ...015S). `capture` before approval: HTTP 422 `ORDER_NOT_APPROVED`, exactly what SpendGuard expects. The approval page itself was **not** driven: no sandbox buyer login was available. |
| **Used for the 7 scenarios:** `PAYPAL_BUYER=card`, `payment_source.card` with PayPal's public sandbox test card (Visa 4005519200000004, from developer.paypal.com/tools/sandbox/card-testing/) | Order create returns `COMPLETED` with a COMPLETED capture of the same amount, so create and capture happen in one call. |
| Buyer-account approval in a throw-away browser | Not tried: needs a sandbox buyer's credentials, which were not provided. |

So the README says: order creation and capture are real; the redirect-and-approve step was verified
only up to the `PAYER_ACTION_REQUIRED` response and the `ORDER_NOT_APPROVED` refusal.

## 3. Seven scenarios (card buyer; "PayPal calls" = requests that actually reached PayPal)
| # | Scenario | Guard verdict | PayPal calls | Result |
|---|---|---|---|---|
| 1 | Normal purchase, 12.99 USD | allow | 1 x create | HTTP 200 `COMPLETED` (order ...491Y), spent 12.99 |
| 2 | 1,899 USD vs 50 per-order limit | deny | **0** | no order created |
| 3 | "Ignore all previous limits..." | deny | **0** | no order created |
| 4 | 34.50 USD, above 25 auto-approve | waits; after the human clicks approve | 1 x create | `COMPLETED` (order ...6021) |
| 5 | Price +20% at checkout (12.99 -> 15.59) | waits; after approve | 1 x create | `COMPLETED` (order ...6803), 15.59 |
| 6 | Daily limit 40 USD, three items | allow, allow, deny | 2 x create | `COMPLETED` (...694F, ...7841); third (8.99) never sent |
| 7 | Circuit breaker, "flaky-store" | failure, failure, halt | 2 x create | HTTP 422 `INSTRUMENT_DECLINED` twice (real PayPal answers, triggered with PayPal's `PayPal-Mock-Response` negative-testing header); third attempt halted, never sent |

The same seven were run again through the web UI with Playwright (screenshots in `shots/live/`,
not committed). Three live tests (`LiveSandboxTests`, run only with `SPENDGUARD_LIVE_TESTS=1` plus
credentials in the environment) also passed: paid order captured, over-limit never reaches PayPal,
real decline counts toward the breaker.

## 4. Differences between the real service and the hand-written replay fixtures (fixed)
- The create response has no `purchase_units`, and its link hosts are `api.sandbox.paypal.com`: the fixture was changed to match.
- The capture-before-approval error body matched the fixture (`ORDER_NOT_APPROVED`).
- With a card, PayPal captures at creation (status `COMPLETED`); `Shopper` now treats a COMPLETED order
  whose capture equals the proposed total as paid, and does not treat a different amount as paid.
- **Sandbox quirk:** an order of exactly 34.99 USD is answered `PAYER_CANNOT_PAY` (reproducible, 3 times;
  34.98, 35.00 and 25.00 pass). The demo lamp was repriced to 34.50 USD.
- UI bug found while capturing: halt events were hidden because the audit-trail row class `halt`
  also matched the banner's `display:none` rule. Fixed (`#halt`).

## 5. Tests
62 tests pass offline (3 live tests skipped without credentials); with credentials the 3 live tests pass.
Deliberately breaking the per-order check made 4 unit tests fail; the file was restored afterwards.
