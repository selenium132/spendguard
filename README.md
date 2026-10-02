# SpendGuard

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
  `ANTHROPIC_API_KEY` is set). Without a key an offline rule-based planner stands in, so the demo and
  the tests run anywhere. The guard is the same for both.

## Run it (Python 3.10+, standard library only, nothing to install)

```bash
git clone <this repository> spendguard && cd spendguard
python3 -m spendguard            # open http://127.0.0.1:8765
python3 -m unittest discover -s tests -t .      # 54 tests
```

With no credentials it starts in **replay mode**: PayPal answers come from `spendguard/fixtures/*.json`
(shaped like the documented Orders v2 responses; they are hand-written, not captured from the live
service). The "Buyer approves (replay)" button stands in for the sandbox buyer.

### Use the real PayPal sandbox

1. At <https://developer.paypal.com/> open *Apps & Credentials*, switch to **Sandbox**, create an app,
   and copy the Client ID and Secret.
2. `cp .env.example .env`, fill in `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` (and optionally
   `ANTHROPIC_API_KEY`), then:
   ```bash
   set -a; . ./.env; set +a
   python3 -m spendguard        # the header badge now says "PayPal: sandbox"
   ```
3. Run a scenario. When an order is created, click **Open PayPal sandbox approval**, log in with a
   sandbox *personal* test account (Sandbox > Accounts), approve, come back and press **Capture**.

Test accounts, keys and secrets are never stored in this repository.

## Try it: seven scenarios in the UI

1. Normal purchase: allowed, PayPal order created.
2. A 1,899 USD item against a 50 USD per-order limit: denied.
3. "Ignore all previous limits ...": the guard does not read prompts. Denied.
4. 34.99 USD, above the 25 USD auto-approve line: waits for you.
5. Price jumps 20% between search and checkout: waits for you.
6. Daily limit: the third purchase crosses 40 USD and is denied.
7. Circuit breaker: PayPal declines twice, the agent halts, the third attempt never reaches PayPal.

## Layout

```
spendguard/policy.py    policy and money in integer cents
spendguard/ledger.py    reservations (thread-safe), daily window in UTC
spendguard/guard.py     the decision: allow / needs_approval / deny / halt, with reasons
spendguard/paypal.py    sandbox client + offline replay client behind one interface
spendguard/planner.py   rule-based mock and real LLM tool-use loop
spendguard/agent.py     planner -> guard -> PayPal, audit trail, human controls
spendguard/server.py    local web UI and JSON API (binds to 127.0.0.1, checks Host/Origin)
tests/                  54 unit tests
```

## Tests

`python3 -m unittest discover -s tests -t .` covers: every limit at and just over the boundary,
quantity multiplication, day rollover, approvals that must not lift hard limits, an order split into
slices hitting the daily limit, negative / zero / unknown purchases, the circuit breaker and its
human reset, 20 parallel calls that must not overspend, an LLM that tries to invent a `set_policy`
tool, the sandbox request shape (with a fake transport), and the web API (including rejecting a wrong
Host or a foreign Origin). As a check that the tests bite, four guard rules were broken one at a time
(per-order check, daily check, halt check, approval-vs-hard-limit) and each time the suite failed.

## Status (2026-10-02)

Built on 2026-10-02, inside the submission period, from an empty folder. Works in replay mode.
**Not yet run against the live PayPal sandbox** because the sandbox credentials are not set up yet;
the request shapes follow the PayPal Orders v2 documentation and are checked only with a fake
transport. The first live run may need small fixes (for example the buyer-approval redirect).

Remaining before submission: run the seven scenarios once against the real sandbox, record the demo
video from the live screens, push the repository to its public home (see `submit_steps.md`).

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

MIT, see `LICENSE`. This project is not affiliated with or endorsed by PayPal. "PayPal" is a
trademark of its owner and is used here only to name the API.
