"""Thin PayPal Orders v2 layer. Two modes behind one interface:

* sandbox : real calls to https://api-m.sandbox.paypal.com (needs PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)
* replay  : answers from spendguard/fixtures/*.json (no network, no credentials)

The live production host is deliberately not supported: this project is sandbox-only.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
FIXTURES = Path(__file__).parent / "fixtures"


class PayPalError(Exception):
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = body or {}
        issue = ""
        if self.body.get("details"):
            issue = self.body["details"][0].get("issue", "")
        self.issue = issue or self.body.get("name", "ERROR")
        super().__init__(f"PayPal {status}: {self.issue}")


def _fixture(name: str, **vals) -> dict:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    for k, v in vals.items():
        text = text.replace("{{" + k + "}}", str(v))
    return json.loads(text)


def _value(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def captured_cents(order: dict) -> int | None:
    """Total of COMPLETED captures inside an order response, or None if there are none."""
    total, found = 0, False
    for pu in order.get("purchase_units", []):
        for cap in pu.get("payments", {}).get("captures", []):
            if cap.get("status") == "COMPLETED":
                found = True
                whole, _, frac = cap["amount"]["value"].partition(".")
                total += int(whole) * 100 + int((frac + "00")[:2])
    return total if found else None


def approve_url(order: dict) -> str | None:
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link.get("href")
    return None


class ReplayPayPal:
    """Offline stand-in. Mirrors the real rule that an order cannot be captured until the buyer approves."""
    mode = "replay"

    def __init__(self, decline_references: tuple = ("flaky-store",)):
        self._orders: dict[str, dict] = {}
        self._approved: set[str] = set()
        self._n = 0
        self.decline_references = decline_references
        self.calls: list[tuple] = []

    def create_order(self, reference_id: str, cents: int, currency: str, description: str,
                     request_id: str | None = None) -> dict:
        self.calls.append(("create_order", reference_id, cents))
        if any(reference_id.startswith(d) for d in self.decline_references):
            raise PayPalError(422, _fixture("error_declined.json"))
        self._n += 1
        oid = f"REPLAYORDER{self._n:05d}"
        order = _fixture("create_order_ok.json", order_id=oid, reference=reference_id,
                         currency=currency, value=_value(cents), description=description)
        self._orders[oid] = {"cents": cents, "currency": currency, "reference": reference_id, "captured": False}
        return order

    def buyer_approves(self, order_id: str):
        """Replay only: stands in for the sandbox buyer clicking Approve on PayPal's page."""
        if order_id not in self._orders:
            raise KeyError(order_id)
        self._approved.add(order_id)

    def capture_order(self, order_id: str, request_id: str | None = None) -> dict:
        self.calls.append(("capture_order", order_id))
        o = self._orders.get(order_id)
        if o is None:
            raise PayPalError(404, {"name": "RESOURCE_NOT_FOUND", "details": [{"issue": "INVALID_RESOURCE_ID"}]})
        if order_id not in self._approved:
            raise PayPalError(422, _fixture("error_not_approved.json"))
        if o["captured"]:
            raise PayPalError(422, {"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_ALREADY_CAPTURED"}]})
        o["captured"] = True
        return _fixture("capture_ok.json", order_id=order_id, reference=o["reference"],
                        currency=o["currency"], value=_value(o["cents"]),
                        capture_id="REPLAYCAP" + order_id[-5:])


class SandboxPayPal:
    mode = "sandbox"

    # PayPal's public sandbox test card (developer.paypal.com/tools/sandbox/card-testing/)
    TEST_CARD = {"number": "4005519200000004", "expiry": "2030-12", "security_code": "123",
                 "name": "Sandbox Buyer"}

    def __init__(self, client_id: str, client_secret: str, transport=None, buyer: str = "paypal",
                 mock_decline_references: tuple = ("flaky-store",)):
        """buyer="paypal": order waits for a sandbox buyer to approve on PayPal's page (PAYER_ACTION_REQUIRED).
        buyer="card": the order carries PayPal's public test card, so PayPal captures it on creation
        (status COMPLETED). Needed when no sandbox buyer login is available."""
        if buyer not in ("paypal", "card"):
            raise ValueError("buyer must be 'paypal' or 'card'")
        self.buyer = buyer
        self.mock_decline_references = mock_decline_references
        if not client_id or not client_secret:
            raise ValueError("PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are required for sandbox mode")
        self._id, self._secret = client_id, client_secret
        self._token: str | None = None
        self._transport = transport or self._http
        self.base = SANDBOX_BASE

    @staticmethod
    def _http(method: str, url: str, headers: dict, data: bytes | None):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"{}")
            except ValueError:
                body = {}
            return e.code, body

    def _auth(self) -> str:
        if self._token:
            return self._token
        basic = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
        status, body = self._transport(
            "POST", f"{self.base}/v1/oauth2/token",
            {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            b"grant_type=client_credentials")
        if status != 200:
            raise PayPalError(status, body)
        self._token = body["access_token"]
        return self._token

    def _call(self, method: str, path: str, payload: dict | None, request_id: str | None,
              extra_headers: dict | None = None):
        headers = {"Authorization": f"Bearer {self._auth()}", "Content-Type": "application/json",
                   "PayPal-Request-Id": request_id or str(uuid.uuid4()), **(extra_headers or {})}
        data = json.dumps(payload).encode() if payload is not None else None
        status, body = self._transport(method, self.base + path, headers, data)
        if status == 401:  # token expired once: refresh and retry
            self._token = None
            headers["Authorization"] = f"Bearer {self._auth()}"
            status, body = self._transport(method, self.base + path, headers, data)
        if status >= 300:
            raise PayPalError(status, body)
        return body

    def create_order(self, reference_id: str, cents: int, currency: str, description: str,
                     request_id: str | None = None) -> dict:
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": reference_id[:256],
                "description": description[:127],
                "amount": {"currency_code": currency, "value": _value(cents)},
            }],
        }
        if self.buyer == "card":
            payload["payment_source"] = {"card": dict(self.TEST_CARD)}
        else:
            payload["payment_source"] = {"paypal": {"experience_context": {
                "user_action": "PAY_NOW",
                "return_url": "http://localhost:8765/paypal/return",
                "cancel_url": "http://localhost:8765/paypal/cancel"}}}
        extra = None
        if self.buyer == "card" and any(reference_id.startswith(d) for d in self.mock_decline_references):
            # PayPal's documented negative-testing header: the sandbox answers with a genuine INSTRUMENT_DECLINED
            extra = {"PayPal-Mock-Response": json.dumps({"mock_application_codes": "INSTRUMENT_DECLINED"})}
        return self._call("POST", "/v2/checkout/orders", payload, request_id, extra)

    def capture_order(self, order_id: str, request_id: str | None = None) -> dict:
        return self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id)


def from_env(env=None):
    """Sandbox if both credentials are set (and PAYPAL_MODE != replay), otherwise replay."""
    env = os.environ if env is None else env
    if env.get("PAYPAL_MODE", "").lower() != "replay" and env.get("PAYPAL_CLIENT_ID") and env.get("PAYPAL_CLIENT_SECRET"):
        return SandboxPayPal(env["PAYPAL_CLIENT_ID"], env["PAYPAL_CLIENT_SECRET"],
                             buyer=env.get("PAYPAL_BUYER", "paypal").lower())
    return ReplayPayPal()
