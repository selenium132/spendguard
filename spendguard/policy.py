"""The user's spending policy. Amounts are integer cents so there is no float rounding."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from decimal import Decimal, ROUND_HALF_UP


def to_cents(value) -> int:
    """'12.34' / 12.34 / Decimal -> 1234. Rejects negatives and NaN."""
    d = Decimal(str(value))
    if not d.is_finite() or d < 0:
        raise ValueError(f"invalid amount: {value!r}")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt(cents: int, currency: str = "USD") -> str:
    return f"{cents // 100}.{cents % 100:02d} {currency}"


@dataclass
class Policy:
    """Set by the human before the agent runs. The agent (and the LLM) cannot change it.

    Hard limits can never be lifted by an approval. Soft limits ask a human.
    """
    currency: str = "USD"
    per_order_limit: int = 5000        # HARD: max for one order (cents)
    daily_limit: int = 10000           # HARD: max total per UTC day (cents)
    per_merchant_daily_limit: int = 6000  # HARD: max per merchant per UTC day (cents)
    approval_threshold: int = 2500     # SOFT: orders above this need a human click
    max_price_drift_pct: float = 5.0   # SOFT: quote -> checkout increase above this needs a human click
    max_consecutive_failures: int = 2  # HARD: circuit breaker; halts until a human resets it
    allowed_merchants: list = field(default_factory=list)  # HARD: empty list = any merchant

    def validate(self) -> None:
        for name in ("per_order_limit", "daily_limit", "per_merchant_daily_limit",
                     "approval_threshold", "max_consecutive_failures"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not (0 <= float(self.max_price_drift_pct) <= 1000):
            raise ValueError("max_price_drift_pct must be between 0 and 1000")
        if self.approval_threshold > self.per_order_limit:
            raise ValueError("approval_threshold cannot exceed per_order_limit")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Policy":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        p = cls(**known)
        p.validate()
        return p

    @classmethod
    def load(cls, path: str) -> "Policy":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
