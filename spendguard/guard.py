"""The guard: pure deterministic code that sits between the agent and PayPal.

Nothing the LLM says can change a verdict. The LLM only *proposes*; this module decides.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from .ledger import Ledger
from .policy import Policy, fmt

ALLOW, NEEDS_APPROVAL, DENY, HALT = "allow", "needs_approval", "deny", "halt"
_RANK = {ALLOW: 0, NEEDS_APPROVAL: 1, DENY: 2, HALT: 3}


@dataclass(frozen=True)
class Proposal:
    merchant: str
    item: str
    quantity: int
    quoted_unit_cents: int      # price the agent saw when it chose the item
    checkout_unit_cents: int    # price the merchant charges right now
    currency: str = "USD"

    @property
    def total_cents(self) -> int:
        return self.checkout_unit_cents * self.quantity


@dataclass
class Reason:
    code: str
    verdict: str   # the verdict this rule alone would give
    message: str
    hard: bool

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class Decision:
    verdict: str
    reasons: list = field(default_factory=list)
    reservation: int | None = None

    def to_dict(self):
        return {"verdict": self.verdict, "reasons": [r.to_dict() for r in self.reasons],
                "reservation": self.reservation}


class Guard:
    def __init__(self, policy: Policy, ledger: Ledger | None = None):
        policy.validate()
        self.policy = policy
        self.ledger = ledger or Ledger()
        self._lock = threading.RLock()
        self.consecutive_failures = 0
        self.halted = False
        self.halt_reason = ""

    # ---- circuit breaker -------------------------------------------------
    def record_failure(self, why: str = "payment failed"):
        with self._lock:
            self.consecutive_failures += 1
            n = self.policy.max_consecutive_failures
            if n and self.consecutive_failures >= n and not self.halted:
                self.halted = True
                self.halt_reason = f"{self.consecutive_failures} failures in a row (last: {why})"

    def record_success(self):
        with self._lock:
            self.consecutive_failures = 0

    def reset(self):
        """Human-only. The agent has no tool that reaches this."""
        with self._lock:
            self.halted = False
            self.halt_reason = ""
            self.consecutive_failures = 0

    # ---- evaluation ------------------------------------------------------
    def evaluate(self, p: Proposal, approved: bool = False) -> Decision:
        """Pure check, no reservation. `approved` clears only soft reasons."""
        pol, reasons = self.policy, []
        cur = pol.currency

        def add(code, verdict, msg, hard):
            reasons.append(Reason(code, verdict, msg, hard))

        if self.halted:
            add("halted", HALT, f"Agent is halted: {self.halt_reason}. A human must reset it.", True)
        if p.quantity < 1 or p.quoted_unit_cents < 0 or p.checkout_unit_cents <= 0:
            add("bad_input", DENY, "Quantity or price is not a valid positive number.", True)
        if p.currency != cur:
            add("currency", DENY, f"Currency {p.currency} is not the policy currency {cur}.", True)
        if pol.allowed_merchants and p.merchant not in pol.allowed_merchants:
            add("merchant_not_allowed", DENY, f"Merchant '{p.merchant}' is not on the allow list.", True)

        total = p.total_cents
        if total > pol.per_order_limit:
            add("per_order_limit", DENY,
                f"Order {fmt(total, cur)} exceeds the per-order limit {fmt(pol.per_order_limit, cur)}.", True)
        today = self.ledger.spent_today()
        if today + total > pol.daily_limit:
            add("daily_limit", DENY,
                f"Spent today {fmt(today, cur)} + {fmt(total, cur)} exceeds the daily limit "
                f"{fmt(pol.daily_limit, cur)}.", True)
        at_m = self.ledger.spent_today_at(p.merchant)
        if at_m + total > pol.per_merchant_daily_limit:
            add("merchant_daily_limit", DENY,
                f"Spent today at {p.merchant} {fmt(at_m, cur)} + {fmt(total, cur)} exceeds the "
                f"per-merchant limit {fmt(pol.per_merchant_daily_limit, cur)}.", True)

        # soft rules (a human may approve these)
        if total > pol.approval_threshold:
            add("approval_threshold", NEEDS_APPROVAL,
                f"Order {fmt(total, cur)} is above the {fmt(pol.approval_threshold, cur)} "
                f"auto-approve threshold.", False)
        if p.quoted_unit_cents > 0 and p.checkout_unit_cents > p.quoted_unit_cents:
            drift = (p.checkout_unit_cents - p.quoted_unit_cents) * 100.0 / p.quoted_unit_cents
            if drift > pol.max_price_drift_pct:
                add("price_drift", NEEDS_APPROVAL,
                    f"Price rose {drift:.1f}% between quote ({fmt(p.quoted_unit_cents, cur)}) and checkout "
                    f"({fmt(p.checkout_unit_cents, cur)}); limit is {pol.max_price_drift_pct:g}%.", False)

        verdict = ALLOW
        for r in reasons:
            eff = r.verdict
            if approved and not r.hard:
                continue  # a human approved this exact proposal
            if _RANK[eff] > _RANK[verdict]:
                verdict = eff
        return Decision(verdict, reasons)

    def authorize(self, p: Proposal, approved: bool = False) -> Decision:
        """Evaluate and, if allowed, reserve the money atomically (no race between two calls)."""
        with self.ledger.lock, self._lock:
            d = self.evaluate(p, approved)
            if d.verdict == ALLOW:
                d.reservation = self.ledger.reserve(p.merchant, p.total_cents)
            return d
