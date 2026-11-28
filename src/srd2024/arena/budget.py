"""Spend cap for paid LLM contenders in `srd-arena bench`. It fails closed.

The cap is enforced in two layers, so total spend can't exceed it whatever the number of parallel bench processes:

1. **Reserve before start.** A paid match starts only if it can reserve `match_limit_usd` in the shared ledger,
   under an exclusive lock, with committed spend (settled costs plus open reservations) + the reservation <= cap.
2. **Per-match meter.** Before every HTTP attempt the driver asks the `MatchMeter` whether the attempt's worst case
   fits in the match limit: input bounded by the request's size in bytes, output by the `max_tokens` cap it sends. If not,
   the match is aborted (`MatchBudgetExceeded`) before the call is made, so a match never spends more than its
   reservation. After each attempt the meter is charged:
   - a response with usage is charged its real usage (input at the input price; `billable_output_tokens`, which
     includes reasoning or thinking where the provider bills it, at the output price);
   - a response with missing, non-numeric or negative usage, or one that can't be parsed, is charged the worst case;
   - a timeout or 5xx is charged the worst case (the provider may have done and billed the work);
   - a 429, connection error or refused request is charged 0 (rejected before any work).
   When the match ends, finished or aborted, its reservation settles to the metered total.

The ledger is an append-only JSONL file, created only by `BudgetLedger.init`. It opens with a header that records
the cap. Every read takes a shared lock, and every write an exclusive lock plus fsync. The bench refuses to run if
the ledger is missing, has no header, has a cap that doesn't match, or has a corrupt, truncated or negative line.
A reservation that is never settled (a crashed process) stays counted at its full amount.

Prices are USD per million tokens (list prices, so cached-input discounts make real spend a little lower). Claude
(`anthropic:`) runs on the Claude Max CLI login in this project and is priced at 0. Pass `--price MODEL=IN/OUT`
to bench it on a metered key.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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
# A request's input can't be more tokens than its UTF-8 JSON body has bytes (a token always covers at least one byte).
# Real tokenizers average ~4 bytes a token, so this over-estimates input about 4x on purpose.


class LedgerError(RuntimeError):
    """The spend ledger is missing, corrupt or inconsistent. Paid work stops (fail closed)."""


def parse_price(text: str) -> tuple[str, tuple[float, float]]:
    """Parse `MODEL=IN/OUT` (USD per million tokens), e.g. `grok-4.7=2/6`."""
    model, _, rates = str(text).partition("=")
    inp, _, out = rates.partition("/")
    if not model.strip() or not inp or not out:
        raise ValueError(f"--price must look like MODEL=IN/OUT (USD per million tokens), got {text!r}.")
    price = (float(inp), float(out))
    if any(not math.isfinite(p) or p < 0 for p in price):
        raise ValueError(f"--price rates must be finite and >= 0, got {text!r}.")
    return model.strip(), price


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


def _usd(tokens_in: float, tokens_out: float, price: tuple[float, float]) -> float:
    return (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000


def _valid_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


class MatchBudgetExceeded(RuntimeError):
    """The next call could push this match past its reservation. The match is aborted before the call."""

    reason = "match_budget"


class MatchMeter:
    """Running cost of one paid match, checked before and charged after every HTTP attempt."""

    def __init__(self, price: tuple[float, float], limit_usd: float) -> None:
        self.price = price
        self.limit_usd = float(limit_usd)
        self.spent_usd = 0.0
        self.calls = 0
        self.worst_case_charges = 0

    def call_bound_usd(self, payload: dict[str, Any], max_output_tokens: int) -> float:
        tokens_in = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        return _usd(tokens_in, max(0, int(max_output_tokens)), self.price)

    def check(self, bound_usd: float) -> None:
        if self.spent_usd + bound_usd > self.limit_usd + 1e-12:
            raise MatchBudgetExceeded(
                f"the next call could bring this match to ${self.spent_usd + bound_usd:.2f}, over its "
                f"${self.limit_usd:.2f} limit (spent ${self.spent_usd:.2f} so far)"
            )

    def charge_usage(self, usage: Any, bound_usd: float) -> float:
        """Charge a response's usage, or the worst case when usage is missing or invalid."""
        u = usage if isinstance(usage, dict) else {}
        tin = _valid_count(u.get("prompt_tokens"))
        tout = _valid_count(u.get("billable_output_tokens", u.get("completion_tokens")))
        if tin is None or tout is None:
            return self.charge_bound(bound_usd)
        cost = _usd(tin, tout, self.price)
        self.spent_usd += cost
        self.calls += 1
        if self.spent_usd > self.limit_usd + 1e-12:
            # The provider reported more than the worst case allowed for (it shouldn't). Stop now, so the overshoot
            # is at most this one call.
            raise MatchBudgetExceeded(
                f"this match reached ${self.spent_usd:.2f}, over its ${self.limit_usd:.2f} limit (a reply's usage "
                "exceeded its worst-case bound)"
            )
        return cost

    def charge_bound(self, bound_usd: float) -> float:
        self.spent_usd += bound_usd
        self.calls += 1
        self.worst_case_charges += 1
        return bound_usd


def match_cost(transcript: dict[str, Any], price: tuple[float, float], side: str = "party") -> float:
    """Recomputed cost of a match's recorded LLM calls (for reports). Raises if a call has invalid usage."""
    tokens_in = tokens_out = 0
    for action in transcript.get("actions") or []:
        if str(action.get("side")) != side or not action.get("usage"):
            continue
        u = action["usage"]
        tin = _valid_count(u.get("prompt_tokens"))
        tout = _valid_count(u.get("billable_output_tokens", u.get("completion_tokens")))
        if tin is None or tout is None:
            raise LedgerError(f"action seq {action.get('seq')} has missing or invalid usage; it can't be priced.")
        tokens_in += tin
        tokens_out += tout
    return round(_usd(tokens_in, tokens_out, price), 6)


class BudgetLedger:
    """A spend cap shared by bench processes through one append-only JSONL ledger."""

    def __init__(
        self,
        cap_usd: float,
        ledger_path: str | Path,
        *,
        match_limit_usd: float = 5.0,
        price_overrides: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        if not math.isfinite(cap_usd) or cap_usd <= 0:
            raise ValueError("budget cap must be > 0 USD.")
        if not math.isfinite(match_limit_usd) or match_limit_usd <= 0 or match_limit_usd > cap_usd:
            raise ValueError("per-match limit must be > 0 and no more than the cap.")
        self.cap_usd = float(cap_usd)
        self.match_limit_usd = float(match_limit_usd)
        self.path = Path(ledger_path)
        self.price_overrides = dict(price_overrides or {})
        self._read()  # fail closed now: missing, corrupt or wrong-cap ledgers refuse to start

    @classmethod
    def init(cls, cap_usd: float, ledger_path: str | Path, **kwargs: Any) -> "BudgetLedger":
        """Create a new ledger. Refuses to overwrite an existing one."""
        path = Path(ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise LedgerError(f"budget ledger {path} already exists; reuse it, or pick a new path.") from None
        header = {"type": "header", "cap_usd": float(cap_usd), "created": round(time.time(), 3), "version": 1}
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(header, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return cls(cap_usd, path, **kwargs)

    def price_for(self, spec: str) -> tuple[float, float] | None:
        return contender_price(spec, self.price_overrides)

    # -- reading ---------------------------------------------------------------------------------------------------

    @contextmanager
    def _locked(self, mode: str, lock: int) -> Iterator[Any]:
        if not self.path.exists():
            raise LedgerError(f"budget ledger {self.path} is missing; it isn't recreated after use (fail closed).")
        with self.path.open(mode, encoding="utf-8") as fh:
            fcntl.flock(fh, lock)
            try:
                yield fh
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _parse(self, text: str) -> tuple[float, dict[str, float]]:
        """Return (settled total, {open reservation id: amount}); raise LedgerError on anything unexpected."""
        if text and not text.endswith("\n"):
            raise LedgerError(f"budget ledger {self.path} ends with a truncated line.")
        lines = text.splitlines()
        if not lines:
            raise LedgerError(f"budget ledger {self.path} is empty (no header).")
        settled = 0.0
        open_res: dict[str, float] = {}
        for i, line in enumerate(lines, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                raise LedgerError(f"budget ledger {self.path} line {i} is corrupt.") from None
            if not isinstance(row, dict):
                raise LedgerError(f"budget ledger {self.path} line {i} is not an object.")
            kind = row.get("type")
            if i == 1:
                if kind != "header":
                    raise LedgerError(f"budget ledger {self.path} has no header line.")
                if abs(float(row.get("cap_usd", -1)) - self.cap_usd) > 1e-9:
                    raise LedgerError(
                        f"budget ledger {self.path} was created for a ${row.get('cap_usd')} cap, not ${self.cap_usd}."
                    )
                continue
            amount = row.get("amount_usd")
            if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
                raise LedgerError(f"budget ledger {self.path} line {i} has a missing or negative amount.")
            rid = row.get("id")
            if kind == "reserve" and isinstance(rid, str) and rid not in open_res:
                open_res[rid] = float(amount)
            elif kind == "settle" and isinstance(rid, str) and rid in open_res:
                del open_res[rid]
                settled += float(amount)
            else:
                raise LedgerError(f"budget ledger {self.path} line {i} is not a valid reserve/settle row.")
        return settled, open_res

    def _read(self) -> tuple[float, dict[str, float]]:
        with self._locked("r", fcntl.LOCK_SH) as fh:
            return self._parse(fh.read())

    def spent(self) -> float:
        """Settled spend only."""
        return round(self._read()[0], 6)

    def committed(self) -> float:
        """Settled spend plus open reservations."""
        settled, open_res = self._read()
        return round(settled + sum(open_res.values()), 6)

    # -- writing ---------------------------------------------------------------------------------------------------

    def _append(self, fh: Any, row: dict[str, Any]) -> None:
        fh.seek(0, os.SEEK_END)
        fh.write(json.dumps({**row, "ts": round(time.time(), 3)}, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())

    def reserve(self, meta: dict[str, Any]) -> str | None:
        """Reserve one match's limit if it fits under the cap. Returns a reservation id, or None if it doesn't fit."""
        with self._locked("r+", fcntl.LOCK_EX) as fh:
            settled, open_res = self._parse(fh.read())
            if settled + sum(open_res.values()) + self.match_limit_usd > self.cap_usd + 1e-9:
                return None
            rid = uuid.uuid4().hex
            self._append(fh, {"type": "reserve", "id": rid, "amount_usd": self.match_limit_usd, **meta})
            return rid

    def settle(self, rid: str, actual_usd: float, meta: dict[str, Any]) -> None:
        if not math.isfinite(actual_usd) or actual_usd < 0:
            raise LedgerError(f"refusing to settle reservation {rid} with an invalid cost {actual_usd!r}.")
        with self._locked("r+", fcntl.LOCK_EX) as fh:
            _, open_res = self._parse(fh.read())
            if rid not in open_res:
                raise LedgerError(f"reservation {rid} is not open in {self.path}.")
            self._append(fh, {"type": "settle", "id": rid, "amount_usd": round(float(actual_usd), 6), **meta})
