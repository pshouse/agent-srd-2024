"""Spend cap for paid LLM contenders in `srd-arena bench`.

A match's cost is priced from its own recorded usage: the party side's `prompt_tokens` at the input price, plus
`billable_output_tokens` (which includes reasoning or thinking tokens where the provider bills them) at the output
price. Each finished match's cost is appended to a JSONL ledger. Several bench processes can share one ledger, and
each checks the total before it starts a paid match. A paid match isn't started once spent + reserve reaches the cap.
The reserve covers matches still running in other processes, and the cost of a match a provider error cut short,
which isn't priced.

Prices are USD per million tokens. They're list prices, so cached-input discounts make real spend a little lower.
Claude (`anthropic:`) runs on the Claude Max CLI login in this project and is priced at 0. Pass
`--price anthropic-model=IN/OUT` if you bench it on a metered API key.
"""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import Any

# (input, output) USD per million tokens.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-6.2-astra": (8.0, 40.0),
    "gpt-6.2-sol": (2.0, 10.0),
    "grok-4.7": (2.0, 6.0),
    "gemini-4-neon": (3.0, 15.0),
    "jev-1.14.0": (0.042, 0.0),
    "jev-latest": (0.042, 0.0),
}
OFFLINE_SPECS = frozenset({"random", "greedy", "heuristic-tactical", "human", "replay"})
SUBSCRIPTION_PREFIXES = frozenset({"anthropic"})


def parse_price(text: str) -> tuple[str, tuple[float, float]]:
    """Parse `MODEL=IN/OUT` (USD per million tokens), e.g. `grok-4.7=2/6`."""
    model, _, rates = str(text).partition("=")
    inp, _, out = rates.partition("/")
    if not model.strip() or not inp or not out:
        raise ValueError(f"--price must look like MODEL=IN/OUT (USD per million tokens), got {text!r}.")
    return model.strip(), (float(inp), float(out))


def contender_price(spec: str, overrides: dict[str, tuple[float, float]] | None = None) -> tuple[float, float] | None:
    """(input, output) price for a contender spec, (0, 0) if free, or None if it's paid and unpriced."""
    spec = str(spec).strip()
    if spec.lower() in OFFLINE_SPECS:
        return (0.0, 0.0)
    prefix, _, model = spec.partition(":")
    model = model.strip()
    if overrides and model in overrides:
        return overrides[model]
    if prefix.strip().lower() in SUBSCRIPTION_PREFIXES:
        return (0.0, 0.0)
    return PRICES_PER_MTOK.get(model)


def match_cost(transcript: dict[str, Any], price: tuple[float, float], side: str = "party") -> float:
    """USD cost of one match's `side` LLM calls, from the usage each action recorded."""
    p_in, p_out = price
    tokens_in = tokens_out = 0
    for action in transcript.get("actions") or []:
        if str(action.get("side")) != side:
            continue
        u = action.get("usage") or {}
        tokens_in += int(u.get("prompt_tokens", 0) or 0)
        tokens_out += int(u.get("billable_output_tokens", u.get("completion_tokens", 0)) or 0)
    return round((tokens_in * p_in + tokens_out * p_out) / 1_000_000, 6)


class BudgetLedger:
    """A spend cap shared through an append-only JSONL file."""

    def __init__(
        self,
        cap_usd: float,
        ledger_path: str | Path,
        *,
        reserve_usd: float = 10.0,
        price_overrides: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        if cap_usd <= 0:
            raise ValueError("budget cap must be > 0 USD.")
        if reserve_usd < 0 or reserve_usd >= cap_usd:
            raise ValueError("budget reserve must be >= 0 and below the cap.")
        self.cap_usd = float(cap_usd)
        self.reserve_usd = float(reserve_usd)
        self.path = Path(ledger_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.price_overrides = dict(price_overrides or {})

    def price_for(self, spec: str) -> tuple[float, float] | None:
        return contender_price(spec, self.price_overrides)

    def spent(self) -> float:
        total = 0.0
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        total += float(json.loads(line).get("cost_usd", 0.0))
                    except (ValueError, AttributeError):
                        continue
        return round(total, 6)

    def can_start_paid_match(self) -> bool:
        return self.spent() + self.reserve_usd < self.cap_usd

    def record(self, row: dict[str, Any]) -> None:
        line = json.dumps({**row, "ts": round(time.time(), 3)}, sort_keys=True) + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
