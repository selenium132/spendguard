"""DEMO ONLY: run the same agent and the same orders with the guard OFF and ON, offline.

`UnguardedGuard` approves every proposal without checking any limit. It exists only so this module
can show what the guard prevents. Nothing in the app, the server's scenarios or the agent uses it;
it is not reachable from the agent's tools, and the comparison always uses the offline replay
PayPal client (no network, no credentials). The numbers are therefore replay numbers: they count
requests that would have been sent to PayPal, not sandbox calls.

    python3 -m spendguard.compare
"""
from __future__ import annotations

from . import paypal, planner
from .agent import Shopper
from .guard import ALLOW, Decision, Guard, Proposal
from .policy import Policy, fmt


class UnguardedGuard(Guard):
    """Guard OFF (demo only): every proposal is allowed, no limit or breaker is consulted."""

    def authorize(self, p: Proposal, approved: bool = False) -> Decision:
        with self.ledger.lock, self._lock:
            return Decision(ALLOW, [], self.ledger.reserve(p.merchant, p.total_cents))

    def record_failure(self, why: str = "payment failed"):
        with self._lock:
            self.consecutive_failures += 1     # counted, but nothing ever halts


def run_scenario(sc: dict, guard_on: bool) -> dict:
    """One scenario on a fresh offline PayPal. No human clicks anything: waiting items stay waiting."""
    pp = paypal.ReplayPayPal()
    policy = Policy()
    if sc["policy"]:
        policy = Policy.from_dict({**policy.to_dict(), **sc["policy"]})
    g = (Guard if guard_on else UnguardedGuard)(policy)
    sh = Shopper(g, pp)
    sh.surge = dict(sc["surge"])
    pl = planner.RuleBasedPlanner()
    for t in sc["tasks"]:
        sh.run_task(t, pl)
    sent = [c for c in pp.calls if c[0] == "create_order"]
    created = [o for o in sh.orders.values()]
    return {"sent_requests": len(sent), "sent_cents": sum(c[2] for c in sent),
            "orders_created": len(created), "created_cents": sum(o["proposal"].total_cents for o in created),
            "waiting_for_human": sum(1 for v in sh.pending.values() if v["status"] == "waiting"),
            "halted": g.halted}


def run_all() -> list[dict]:
    from .server import SCENARIOS    # lazy: server imports this module for the /compare page
    rows = []
    for key, sc in SCENARIOS.items():
        rows.append({"key": key, "title": sc["title"], "off": run_scenario(sc, False), "on": run_scenario(sc, True)})
    return rows


def totals(rows: list[dict]) -> dict:
    return {side: {"sent_requests": sum(r[side]["sent_requests"] for r in rows),
                   "sent_cents": sum(r[side]["sent_cents"] for r in rows),
                   "orders_created": sum(r[side]["orders_created"] for r in rows),
                   "created_cents": sum(r[side]["created_cents"] for r in rows)} for side in ("off", "on")}


def render_text(rows: list[dict]) -> str:
    def cell(x):
        return f"{x['sent_requests']} / {fmt(x['sent_cents'])}"
    out = ["Replay numbers (offline PayPal stand-in, same agent, same orders, nobody clicks Approve).",
           "Cell = requests sent to PayPal / total amount in those requests.", "",
           f"{'Scenario':34} {'Guard OFF':>20} {'Guard ON':>20}"]
    for r in rows:
        out.append(f"{r['title']:34} {cell(r['off']):>20} {cell(r['on']):>20}")
    t = totals(rows)
    out.append(f"{'Total':34} {cell(t['off']):>20} {cell(t['on']):>20}")
    out.append(f"Orders actually created in the stand-in (the rest were declined): OFF {t['off']['orders_created']} "
               f"/ {fmt(t['off']['created_cents'])}, ON {t['on']['orders_created']} / {fmt(t['on']['created_cents'])}")
    return "\n".join(out)


def render_html(rows: list[dict]) -> str:
    from html import escape as e
    t = totals(rows)

    def td(x, cls):
        return f'<td class="{cls}">{x["sent_requests"]} request{"s" if x["sent_requests"] != 1 else ""} &middot; {e(fmt(x["sent_cents"]))}</td>'
    body = "".join(f'<tr><td>{e(r["title"])}</td>{td(r["off"], "off")}{td(r["on"], "on")}</tr>' for r in rows)
    body += f'<tr class="tot"><td>Total</td>{td(t["off"], "off")}{td(t["on"], "on")}</tr>'
    return ("<!doctype html><html lang=en><head><meta charset=utf-8><title>SpendGuard: guard off vs on</title><style>"
            "body{font:16px/1.45 system-ui,sans-serif;background:#f6f7f9;color:#16202a;margin:0}"
            "header{background:#102a43;color:#fff;padding:14px 24px}h1{margin:0;font-size:20px}"
            "main{max-width:980px;margin:18px auto;padding:0 18px}"
            "table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #dde2e8}"
            "th,td{padding:9px 12px;border-bottom:1px solid #dde2e8;text-align:left}"
            "th{background:#eef2f6;font-size:13px;text-transform:uppercase;letter-spacing:.03em}"
            "td.off{color:#b3261e;font-weight:600}td.on{color:#1a7f4b;font-weight:600}tr.tot td{border-top:2px solid #16202a;font-size:18px}"
            "p{color:#5b6773;font-size:14px}</style></head><body>"
            "<header><h1>Same agent, same orders: guard OFF vs guard ON</h1></header><main>"
            "<p>What reaches PayPal in the seven demo scenarios. <b>Replay numbers</b>: offline PayPal stand-in, "
            "nobody clicks Approve, no sandbox call was made. Guard OFF is a demo-only switch "
            "(<code>spendguard/compare.py</code>) that approves every proposal.</p>"
            "<table><tr><th>Scenario</th><th>Guard OFF: sent to PayPal</th><th>Guard ON: sent to PayPal</th></tr>"
            + body + "</table></main></body></html>")


if __name__ == "__main__":
    print(render_text(run_all()))
