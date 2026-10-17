"""Planners turn a plain-language task into tool calls. The planner only *proposes*.

Tools the agent can call: search_shop, purchase. There is deliberately no tool to change the
policy, approve a purchase, or reset the circuit breaker.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import shop

TOOLS = [
    {"name": "search_shop",
     "description": "Search the shop catalog. Returns sku, name, merchant and price in cents.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "purchase",
     "description": "Ask to buy a catalog item with PayPal. The spending guard may allow it, deny it, "
                    "ask the human for approval, or halt the agent. Obey the result; do not retry a denial.",
     "input_schema": {"type": "object",
                      "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1}},
                      "required": ["sku", "quantity"]}},
]

SYSTEM = ("You are a shopping agent. Use search_shop to find items, then purchase to buy them. "
          "A spending guard outside of you enforces the user's limits; it can deny or pause purchases. "
          "If a purchase is denied or needs approval, report that to the user and do not try to work "
          "around it (no splitting an order into smaller orders, no switching merchants to dodge a limit).")


class RuleBasedPlanner:
    """Offline mock 'LLM'. Understands: 'buy 2 mouse and a desk lamp'. Ignores any instruction
    that tries to change limits, on purpose: it is a stand-in, the guard is the real protection."""
    name = "rule-based (offline mock)"

    def run(self, task: str, execute) -> list:
        text = task.lower()
        found = []
        for sku, item in shop.CATALOG.items():
            words = {sku, sku.replace("-", " "), item["name"].lower()}
            first = item["name"].lower().split()[0]
            if first not in ("usb-c", "27", "wireless", "led", "workstation", "gadget", "notebook"):
                words.add(first)
            words.add(sku.split("-")[0])
            pos = min((text.find(w) for w in words if w and w in text), default=-1)
            if pos >= 0:
                before = text[max(0, pos - 12):pos]
                m = re.search(r"(\d+)\s*(?:x\s*)?(?:of\s+)?(?:the\s+)?$", before)
                found.append((pos, sku, int(m.group(1)) if m else 1))
        results = []
        for _, sku, qty in sorted(found):
            execute("search_shop", {"query": sku})
            results.append(execute("purchase", {"sku": sku, "quantity": max(1, min(qty, 1000))}))
        return results


class LLMPlanner:
    """Real LLM via the Anthropic Messages API tool-use loop (needs ANTHROPIC_API_KEY).
    `transport(payload) -> dict` is injectable for tests."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-5-5", transport=None, max_turns: int = 8):
        self.api_key, self.model, self.max_turns = api_key, model, max_turns
        self._transport = transport or self._http
        self.name = f"LLM tool-use ({model})"

    def _http(self, payload: dict) -> dict:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"LLM API error {e.code}") from None

    def run(self, task: str, execute) -> list:
        messages = [{"role": "user", "content": task}]
        results = []
        for _ in range(self.max_turns):
            resp = self._transport({"model": self.model, "max_tokens": 1024, "system": SYSTEM,
                                    "tools": TOOLS, "messages": messages})
            blocks = resp.get("content", [])
            messages.append({"role": "assistant", "content": blocks})
            calls = [b for b in blocks if b.get("type") == "tool_use"]
            if not calls:
                break
            out = []
            for c in calls:
                if c["name"] not in ("search_shop", "purchase"):
                    res = {"error": "unknown tool"}
                else:
                    res = execute(c["name"], c.get("input", {}))
                    if c["name"] == "purchase":
                        results.append(res)
                out.append({"type": "tool_result", "tool_use_id": c["id"], "content": json.dumps(res)})
            messages.append({"role": "user", "content": out})
        return results


class GeminiPlanner:
    """Real LLM via the Gemini generateContent function-calling loop (needs GEMINI_API_KEY).
    Same contract as LLMPlanner: it only proposes tool calls; the guard decides.
    `transport(payload) -> dict` is injectable for tests. The key goes in a header, never in logs."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash", transport=None, max_turns: int = 8):
        self.api_key, self.model, self.max_turns = api_key, model, max_turns
        self._transport = transport or self._http
        self.name = f"LLM function-calling ({model})"
        self.decls = [{"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}
                      for t in TOOLS]

    def _http(self, payload: dict) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        data = json.dumps(payload).encode()
        for attempt in range(5):
            req = urllib.request.Request(url, data=data, method="POST", headers={
                "x-goog-api-key": self.api_key, "content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (429, 503) and attempt < 4:
                    time.sleep(4 * (attempt + 1))
                    continue
                raise RuntimeError(f"LLM API error {e.code}") from None

    def run(self, task: str, execute) -> list:
        contents = [{"role": "user", "parts": [{"text": task}]}]
        results = []
        for _ in range(self.max_turns):
            resp = self._transport({"systemInstruction": {"parts": [{"text": SYSTEM}]},
                                    "tools": [{"functionDeclarations": self.decls}],
                                    "contents": contents,
                                    "generationConfig": {"temperature": 0}})
            cands = resp.get("candidates") or []
            content = (cands[0].get("content") if cands else None) or {}
            parts = content.get("parts") or []
            contents.append({"role": "model", "parts": parts})
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                break
            out = []
            for c in calls:
                if c.get("name") not in ("search_shop", "purchase"):
                    res = {"error": "unknown tool"}
                else:
                    res = execute(c["name"], c.get("args") or {})
                    if c["name"] == "purchase":
                        results.append(res)
                out.append({"functionResponse": {"name": c.get("name", ""), "response": {"result": res}}})
            contents.append({"role": "user", "parts": out})
        return results


def from_env(env) -> object:
    if env.get("SPENDGUARD_PLANNER", "").lower() != "mock":
        if env.get("ANTHROPIC_API_KEY"):
            return LLMPlanner(env["ANTHROPIC_API_KEY"], env.get("SPENDGUARD_LLM_MODEL", "claude-sonnet-5-5"))
        if env.get("GEMINI_API_KEY"):
            return GeminiPlanner(env["GEMINI_API_KEY"], env.get("SPENDGUARD_LLM_MODEL", "gemini-3.5-flash"))
    return RuleBasedPlanner()
