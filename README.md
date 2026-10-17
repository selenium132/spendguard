# SpendGuard

**An AI agent that can pay with PayPal can overspend, buy twice or keep retrying; SpendGuard puts limits the model cannot change between the agent and PayPal.** (Agentic commerce, with a brake.)

![Audit trail: an "ignore your limits" prompt is denied, PayPal is never called](shots/gallery_v2/3_denied_audit_trail.png)

## Guard off vs guard on

Same agent, same orders, seven demo scenarios, offline replay (no sandbox calls, nobody clicks Approve). Reproduce: `python3 -m spendguard.compare`, or open `/compare` in the app.

| Scenario | Guard off: sent to PayPal | Guard on: sent to PayPal |
|---|---|---|
| 1 Normal purchase | 1 request, 12.99 USD | 1 request, 12.99 USD |
| 2 Over the per-order limit | 1 request, 1,899.00 USD | 0 |
| 3 "Ignore your limits" | 1 request, 3,798.00 USD | 0 |
| 4 Needs a human | 1 request, 34.50 USD | 0 (waits for a click) |
| 5 Price +20% at checkout | 1 request, 15.59 USD | 0 (waits for a click) |
| 6 Daily limit 40 USD | 3 requests, 43.97 USD | 2 requests, 34.98 USD |
| 7 Circuit breaker | 3 requests, 45.00 USD (declined by the stand-in) | 2 requests, 30.00 USD, then halted |
| **Total** | **11 requests, 5,849.05 USD** | **5 requests, 77.97 USD** |

"Guard off" is a demo-only class (`UnguardedGuard` in `spendguard/compare.py`); the app never uses it. These are replay numbers.

## Run it in 30 seconds (Python 3.10+, nothing to install)

```bash
git clone https://github.com/selenium132/spendguard && cd spendguard
python3 -m spendguard          # open http://127.0.0.1:8765 and click scenario 3
python3 -m unittest discover -s tests -t .    # 74 tests (3 of them need the live sandbox)
```

Note on AI: the default agent is an offline rule-based planner (the header says so). Set `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) and a real LLM drives the same tools through function calling (`spendguard/planner.py`; order: Anthropic, Gemini, mock; `SPENDGUARD_PLANNER=mock` forces the mock). The Gemini planner was run live on 8 scenarios on 2026-10-04 JST against the offline PayPal client: 10 proposals, 4 sent (69.96 USD), 5 denied (9,752.99 USD), 1 held for a human (34.50 USD); results and the raw JSON are in `raw/gemini_run_20261004.md`. The free Gemini tier is 20 requests per day per model, so the trial used two models and one run per scenario. The Anthropic planner is tested with a fake transport only. The guard behaves the same for all planners.

---

**Hard spending limits and stop conditions for AI agents that pay with PayPal.**

An AI agent that can check out for you is useful until it buys the wrong thing, buys it twice, or
keeps retrying after something went wrong. SpendGuard puts a small piece of ordinary code between
the agent and PayPal. You set the rules first. The agent (and the LLM behind it) can only *propose*
a purchase; the guard decides.

| Rule | Type | What happens |
|---|---|---|
| Per-order limit | hard | denied, no PayPal call |
| Per-day limit and per-merchant-per-day limit | hard | denied, no PayPal call (reservations are atomic, so parallel calls cannot overspend) |
| Merchant allow list, currency | hard | denied |
| Circuit breaker: N failures in a row | hard | agent is **halted**; only a human can reset it |
| Auto-approve threshold | soft | the agent waits for a human click |
| Price rose more than X% between quote and checkout | soft | the agent waits for a human click |

*Hard* limits can never be lifted by an approval. An approval only clears *soft* rules, and the
exact proposal is re-checked against the hard limits at the moment you click.

The agent has two tools, `search_shop` and `purchase`. There is no tool to change the policy,
approve a purchase or reset the breaker, so a prompt-injected or confused model has nothing to call.

## How PayPal and AI are used

- **PayPal**: Orders API v2 on the free **sandbox** (`POST /v2/checkout/orders`, then
  `POST /v2/checkout/orders/{id}/capture`, OAuth2 client credentials, `PayPal-Request-Id`
  idempotency header). The agent creates the order; PayPal's own buyer-approval step is a second gate;
  capture then moves the money. The production host is deliberately not supported.
- **AI**: an LLM calls the shop tools through tool use (Anthropic Messages API when
  `ANTHROPIC_API_KEY` is set, Google Gemini function calling when `GEMINI_API_KEY` is set). Without a key an offline rule-based planner stands in, so the demo and
  the tests run anywhere. The guard is the same for all of them.

## Run it (Python 3.10+, standard library only, nothing to install)

```bash
git clone <this repository> spendguard && cd spendguard
python3 -m spendguard            # open http://127.0.0.1:8765
python3 -m unittest discover -s tests -t .      # 74 tests (3 of them run only against the live sandbox)
```

With no credentials it starts in **replay mode**: PayPal answers come from `spendguard/fixtures/*.json`
(hand-written, then corrected against real sandbox responses; see `sandbox_run.md`). The "Buyer approves (replay)" button stands in for the sandbox buyer.

### Use the real PayPal sandbox

1. At <https://developer.paypal.com/> open *Apps & Credentials*, switch to **Sandbox**, create an app,
   and copy the Client ID and Secret.
2. `cp .env.example .env`, fill in `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` (and optionally
   `GEMINI_API_KEY` or `ANTHROPIC_API_KEY`), then:
   ```bash
   set -a; . ./.env; set +a
   python3 -m spendguard        # the header badge now says "PayPal: sandbox"
   ```
   Set `PAYPAL_BUYER=card` to let PayPal's public sandbox test card pay (order captured on creation),
   for when you have no sandbox buyer login. Live tests: `SPENDGUARD_LIVE_TESTS=1 python3 -m unittest discover -s tests -t .`
3. Otherwise run a scenario. When an order is created, click **Open PayPal sandbox approval**, log in with a
   sandbox *personal* test account (Sandbox > Accounts), approve, come back and press **Capture**.

Test accounts, keys and secrets are never stored in this repository.

## Try it: seven scenarios in the UI

1. Normal purchase: allowed, PayPal order created.
2. A 1,899 USD item against a 50 USD per-order limit: denied.
3. "Ignore all previous limits ...": the guard does not read prompts. Denied.
4. 34.50 USD, above the 25 USD auto-approve line: waits for you.
5. Price jumps 20% between search and checkout: waits for you.
6. Daily limit: the third purchase crosses 40 USD and is denied.
7. Circuit breaker: PayPal declines twice, the agent halts, the third attempt never reaches PayPal.

## Layout

```
spendguard/policy.py    policy and money in integer cents
spendguard/ledger.py    reservations (thread-safe), daily window in UTC
spendguard/guard.py     the decision: allow / needs_approval / deny / halt, with reasons
spendguard/paypal.py    sandbox client + offline replay client behind one interface
spendguard/planner.py   rule-based mock and real LLM tool-use loops (Anthropic, Gemini)
spendguard/agent.py     planner -> guard -> PayPal, audit trail, human controls
spendguard/server.py    local web UI and JSON API (binds to 127.0.0.1, checks Host/Origin)
tests/                  74 tests, 3 of them live sandbox only
raw/mutation_check.py   disables each guard rule in turn and runs the tests
```

## Tests

`python3 -m unittest discover -s tests -t .` covers: every limit at and just over the boundary,
quantity multiplication, day rollover, approvals that must not lift hard limits, an order split into
slices hitting the daily limit, negative / zero / unknown purchases, the circuit breaker and its
human reset, 20 parallel calls that must not overspend, an LLM that tries to invent a `set_policy`
tool, the sandbox request shape (with a fake transport), and the web API (including rejecting a wrong
Host or a foreign Origin). As a check that the tests bite, `raw/mutation_check.py` disables each of the 9 guard rules in turn
(halt, currency, merchant allow list, per-order, daily, per-merchant daily, auto-approve line, price drift,
approval-vs-hard-limit); on 2026-10-10 the suite failed for every one (1 to 8 failing tests each).

## Status (2026-10-02)

Built on 2026-10-02, inside the submission period, from an empty folder. Works in replay mode and
**was run against the real PayPal sandbox on 2026-10-02** (log: `sandbox_run.md`):

- OAuth2 token, `POST /v2/checkout/orders` and capture are real calls to `api-m.sandbox.paypal.com`.
- All seven scenarios were run live. Denied, halted and over-limit proposals produced **no** PayPal
  request at all; allowed ones produced real orders with `COMPLETED` captures; the circuit breaker was
  driven by genuine `INSTRUMENT_DECLINED` answers from PayPal.
- **Limit on what was verified:** no sandbox *buyer* login was available, so the live runs use
  `PAYPAL_BUYER=card` (PayPal's public sandbox test card in `payment_source.card`), where PayPal captures
  while creating the order. The default flow (buyer approves on PayPal's page) was checked up to the real
  `PAYER_ACTION_REQUIRED` response with its approval link and PayPal's `ORDER_NOT_APPROVED` refusal of an
  early capture; the approval page itself was not driven.
- Replay fixtures are still hand-written, but were corrected to match the real response shapes.

Demo video (2:45, unlisted on YouTube): https://youtu.be/VJlUQvbI36g , recorded from the live sandbox run.

## Why this idea

The proposed idea ("a shopping agent with limits and stop conditions") was kept. The Resources page
names agents that "actually transact", and the criteria ask for a "credible, specific case" for a
"real problem". The problem here is concrete: an agent that holds a payment credential needs limits
that do not depend on the model behaving. The novelty is the split: the model proposes, deterministic
code decides, and the human controls are not reachable from the agent's tool list.

## AI use and disclosure

Written with Claude Code (an AI coding agent) directed by the repository owner, who sets the
scope and checks the results. The design, code and tests were produced in that way.

## License

MIT, see `LICENSE` (full text at the repository root). This project is not affiliated with or endorsed by PayPal. "PayPal" is a
trademark of its owner and is used here only to name the API.
