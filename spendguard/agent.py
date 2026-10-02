"""Shopper: wires planner -> guard -> PayPal and keeps an audit trail."""
from __future__ import annotations

import itertools
import threading
import time

from . import shop
from .guard import ALLOW, NEEDS_APPROVAL, DENY, HALT, Guard, Proposal
from .paypal import PayPalError, approve_url, captured_cents
from .policy import fmt


class Shopper:
    def __init__(self, guard: Guard, paypal, clock=time.time):
        self.guard, self.paypal, self._clock = guard, paypal, clock
        self._lock = threading.RLock()
        self.events: list[dict] = []
        self.pending: dict[int, dict] = {}     # approval id -> {"proposal", "status"}
        self.orders: dict[str, dict] = {}      # paypal order id -> info
        self.surge: dict[str, float] = {}      # scenario hook: sku -> % price increase at checkout
        self._quotes: dict[str, int] = {}
        self._ids = itertools.count(1)

    # ---- audit -------------------------------------------------------------
    def log(self, kind: str, message: str, **data):
        with self._lock:
            ev = {"seq": len(self.events) + 1, "t": self._clock(), "kind": kind, "message": message, **data}
            self.events.append(ev)
            return ev

    # ---- tools exposed to the agent ---------------------------------------------
    def execute(self, tool: str, args: dict) -> dict:
        if tool == "search_shop":
            hits = shop.search(str(args.get("query", "")))
            for h in hits:
                self._quotes[h["sku"]] = h["cents"]
            self.log("search", f"agent searched '{args.get('query', '')}' ({len(hits)} hits)")
            return {"results": hits}
        if tool == "purchase":
            return self.purchase(str(args.get("sku", "")), args.get("quantity", 1))
        return {"error": f"unknown tool {tool}"}

    def purchase(self, sku: str, quantity) -> dict:
        if sku not in shop.CATALOG:
            self.log("deny", f"agent asked for unknown item '{sku}'")
            return {"status": DENY, "reasons": ["unknown item"]}
        try:
            qty = int(quantity)
        except (TypeError, ValueError):
            qty = 0
        item = shop.CATALOG[sku]
        quoted = self._quotes.get(sku, item["cents"])
        p = Proposal(item["merchant"], sku, qty, quoted, shop.checkout_price(sku, self.surge),
                     self.guard.policy.currency)
        self.log("proposal", f"agent proposes {qty} x {item['name']} at {item['merchant']} "
                             f"= {fmt(p.total_cents, p.currency)}", sku=sku)
        d = self.guard.authorize(p)
        reasons = [r.message for r in d.reasons if r.hard or d.verdict == NEEDS_APPROVAL]
        if d.verdict == ALLOW:
            return self._pay(p, d.reservation)
        if d.verdict == NEEDS_APPROVAL:
            pid = next(self._ids)
            with self._lock:
                self.pending[pid] = {"id": pid, "proposal": p, "status": "waiting", "reasons": reasons}
            self.log("needs_approval", f"waiting for a human: {' '.join(reasons)}", approval_id=pid)
            return {"status": NEEDS_APPROVAL, "approval_id": pid, "reasons": reasons}
        self.log(d.verdict, f"{d.verdict.upper()}: {' '.join(reasons)}", sku=sku)
        return {"status": d.verdict, "reasons": reasons}

    # ---- money movement (only reachable after the guard allowed it) --------------------
    def _pay(self, p: Proposal, reservation: int) -> dict:
        ref = f"{p.merchant}:{p.item}"
        try:
            order = self.paypal.create_order(ref, p.total_cents, p.currency, f"{p.quantity} x {p.item}")
        except PayPalError as e:
            self.guard.ledger.release(reservation)
            self.guard.record_failure(e.issue)
            self.log("failure", f"PayPal refused the order ({e.issue}); consecutive failures = "
                                f"{self.guard.consecutive_failures}", sku=p.item)
            if self.guard.halted:
                self.log("halt", f"HALTED: {self.guard.halt_reason}")
            return {"status": "failed", "reasons": [e.issue], "halted": self.guard.halted}
        oid = order["id"]
        if order.get("status") == "COMPLETED" and captured_cents(order) == p.total_cents:
            # card buyer: PayPal captured the order while creating it (no separate approval step)
            with self._lock:
                self.orders[oid] = {"order_id": oid, "proposal": p, "reservation": reservation,
                                    "state": "paid", "approve_url": None}
            self.guard.ledger.commit(reservation)
            self.guard.record_success()
            self.log("order_created", f"PayPal order {oid} created for {fmt(p.total_cents, p.currency)}", order_id=oid)
            self.log("paid", f"captured {fmt(p.total_cents, p.currency)} for order {oid} (test card)", order_id=oid)
            return {"status": "paid", "order_id": oid}
        with self._lock:
            self.orders[oid] = {"order_id": oid, "proposal": p, "reservation": reservation,
                                "state": "awaiting_buyer", "approve_url": approve_url(order)}
        self.log("order_created", f"PayPal order {oid} created for {fmt(p.total_cents, p.currency)}; "
                                  f"waiting for the buyer to approve", order_id=oid)
        return {"status": "order_created", "order_id": oid, "approve_url": approve_url(order)}

    def capture(self, order_id: str) -> dict:
        with self._lock:
            o = self.orders.get(order_id)
        if o is None or o["state"] != "awaiting_buyer":
            return {"status": "error", "reasons": ["unknown or finished order"]}
        try:
            self.paypal.capture_order(order_id)
        except PayPalError as e:
            if e.issue == "ORDER_NOT_APPROVED":
                self.log("waiting", f"order {order_id} not approved by the buyer yet", order_id=order_id)
                return {"status": "awaiting_buyer", "reasons": [e.issue]}
            self.guard.ledger.release(o["reservation"])
            o["state"] = "failed"
            self.guard.record_failure(e.issue)
            self.log("failure", f"capture failed ({e.issue})", order_id=order_id)
            if self.guard.halted:
                self.log("halt", f"HALTED: {self.guard.halt_reason}")
            return {"status": "failed", "reasons": [e.issue]}
        self.guard.ledger.commit(o["reservation"])
        o["state"] = "paid"
        self.guard.record_success()
        p = o["proposal"]
        self.log("paid", f"captured {fmt(p.total_cents, p.currency)} for order {order_id}", order_id=order_id)
        return {"status": "paid", "order_id": order_id}

    # ---- human controls (the agent has no tool that reaches these) ---------------------
    def approve(self, approval_id: int) -> dict:
        with self._lock:
            item = self.pending.get(approval_id)
            if not item or item["status"] != "waiting":
                return {"status": "error", "reasons": ["no such waiting approval"]}
            item["status"] = "approved"
        d = self.guard.authorize(item["proposal"], approved=True)
        if d.verdict != ALLOW:  # a hard limit changed since (e.g. the daily limit filled up)
            item["status"] = "blocked"
            reasons = [r.message for r in d.reasons if r.hard]
            self.log(d.verdict, f"approval {approval_id} could not proceed: {' '.join(reasons)}")
            return {"status": d.verdict, "reasons": reasons}
        self.log("approved", f"human approved #{approval_id}", approval_id=approval_id)
        return self._pay(item["proposal"], d.reservation)

    def reject(self, approval_id: int) -> dict:
        with self._lock:
            item = self.pending.get(approval_id)
            if not item or item["status"] != "waiting":
                return {"status": "error", "reasons": ["no such waiting approval"]}
            item["status"] = "rejected"
        self.log("rejected", f"human rejected #{approval_id}", approval_id=approval_id)
        return {"status": "rejected"}

    def reset_halt(self):
        self.guard.reset()
        self.log("reset", "human reset the circuit breaker")

    def run_task(self, task: str, planner) -> dict:
        self.log("task", f"task: {task}")
        results = planner.run(task, self.execute)
        return {"planner": planner.name, "results": results}

    def snapshot(self) -> dict:
        g = self.guard
        with self._lock:
            return {
                "mode": self.paypal.mode,
                "policy": g.policy.to_dict(),
                "spent_today": g.ledger.spent_today(),
                "halted": g.halted, "halt_reason": g.halt_reason,
                "consecutive_failures": g.consecutive_failures,
                "pending": [{"id": k, "item": v["proposal"].item, "merchant": v["proposal"].merchant,
                             "total_cents": v["proposal"].total_cents, "status": v["status"],
                             "reasons": v["reasons"]} for k, v in self.pending.items()],
                "orders": [{"order_id": k, "state": v["state"], "approve_url": v["approve_url"],
                            "total_cents": v["proposal"].total_cents, "merchant": v["proposal"].merchant,
                            "item": v["proposal"].item} for k, v in self.orders.items()],
                "events": list(self.events[-200:]),
                "surge": dict(self.surge),
            }
