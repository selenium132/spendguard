"""A tiny pretend merchant catalog so the demo is self-contained.

`quote` is what the agent sees while browsing; `checkout_price` is what the merchant
charges when the agent pays. Scenarios make them differ to exercise the price-drift stop.
"""
from __future__ import annotations

CATALOG = {
    "usb-c-cable": {"name": "USB-C cable 2 m", "merchant": "cable-corner", "cents": 1299},
    "desk-lamp": {"name": "LED desk lamp", "merchant": "lumen-shop", "cents": 3450},
    "notebook-3pack": {"name": "Notebook 3-pack", "merchant": "paper-hub", "cents": 899},
    "monitor-27": {"name": "27 inch monitor", "merchant": "pixel-depot", "cents": 24900},
    "gpu-pro": {"name": "Workstation GPU", "merchant": "pixel-depot", "cents": 189900},
    "mouse": {"name": "Wireless mouse", "merchant": "cable-corner", "cents": 2199},
    "flaky-gadget": {"name": "Gadget from a flaky store", "merchant": "flaky-store", "cents": 1500},
}


def search(query: str):
    q = query.lower()
    return [{"sku": k, **v} for k, v in CATALOG.items()
            if q in k or q in v["name"].lower() or q in v["merchant"]]


def quote(sku: str) -> int:
    return CATALOG[sku]["cents"]


def checkout_price(sku: str, surge: dict | None = None) -> int:
    """Price now. `surge` maps sku -> percent increase (scenario hook)."""
    base = CATALOG[sku]["cents"]
    pct = (surge or {}).get(sku, 0)
    return int(round(base * (100 + pct) / 100))
