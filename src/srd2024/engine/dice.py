"""Counter-based SHA-256 deterministic PRNG and 5e-2024 dice engine.

Implements STAGE2-SPEC §5.1:
- Tiny JSON-serializable state: `{"seed": int, "draw_index": int}`.
- Cross-platform identical draws via SHA-256.
- Deterministic `roll_overrides` queue for exact unit testing.
- `roll_d20(mode)` supporting normal / advantage / disadvantage.
- `roll_dice_expr(expr, crit=False)` doubling dice counts only on critical hits.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_TERM_RE = re.compile(r"([+-]?)\s*(?:(\d+)\s*[dD]\s*(\d+)|(\d+))")


class DeterministicRNG:
    """Counter-based SHA-256 deterministic RNG with optional `roll_overrides` queue."""

    def __init__(
        self,
        seed: int = 1,
        draw_index: int = 0,
        roll_overrides: list[int] | None = None,
    ) -> None:
        self.seed: int = int(seed)
        self.draw_index: int = int(draw_index)
        self._overrides: list[int] = [int(v) for v in roll_overrides] if roll_overrides else []

    def to_dict(self) -> dict[str, int]:
        return {"seed": self.seed, "draw_index": self.draw_index}

    @classmethod
    def from_dict(
        cls,
        state: dict[str, Any] | None,
        roll_overrides: list[int] | None = None,
    ) -> DeterministicRNG:
        if not isinstance(state, dict):
            return cls(seed=1, draw_index=0, roll_overrides=roll_overrides)
        return cls(
            seed=int(state.get("seed", 1)),
            draw_index=int(state.get("draw_index", 0)),
            roll_overrides=roll_overrides,
        )

    @property
    def remaining_overrides(self) -> int:
        return len(self._overrides)

    def roll_die(self, sides: int) -> int:
        """Roll a single `1..sides` die using overrides first, else SHA-256 counter."""
        s = int(sides)
        if s < 1:
            raise ValueError(f"Die sides must be >= 1, got {sides!r}")
        if self._overrides:
            raw = self._overrides.pop(0)
            return max(1, min(s, int(raw)))
        token = f"{self.seed}:{self.draw_index}:{s}".encode("utf-8")
        digest = hashlib.sha256(token).digest()
        value = (int.from_bytes(digest[:8], "big") % s) + 1
        self.draw_index += 1
        return value

    def roll_d20(self, mode: str = "normal") -> dict[str, Any]:
        """Roll a d20 with `'normal'`, `'advantage'`, or `'disadvantage'`."""
        norm_mode = str(mode).strip().lower()
        if norm_mode == "normal":
            r1 = self.roll_die(20)
            rolls = [r1]
            chosen = r1
        elif norm_mode == "advantage":
            r1 = self.roll_die(20)
            r2 = self.roll_die(20)
            rolls = [r1, r2]
            chosen = max(r1, r2)
        elif norm_mode == "disadvantage":
            r1 = self.roll_die(20)
            r2 = self.roll_die(20)
            rolls = [r1, r2]
            chosen = min(r1, r2)
        else:
            raise ValueError(f"Invalid d20 roll mode: {mode!r}")
        return {
            "rolls": rolls,
            "chosen": chosen,
            "mode": norm_mode,
            "nat20": chosen == 20,
            "nat1": chosen == 1,
        }

    def roll_dice_expr(self, expr: str, crit: bool = False) -> dict[str, Any]:
        """Roll a dice expression like `'1d8'`, `'2d6+3'`, `'1d4+1'`, or flat `'1'`.

        On `crit=True`, doubles the dice count for every `NdM` term while
        keeping flat modifiers unchanged.
        """
        return roll_dice_expr(expr, self, crit=crit)


def parse_dice_expr(expr: str) -> tuple[list[tuple[int, int, int]], int]:
    """Parse a dice expression into `(dice_terms, flat_modifier)`.

    Each dice term is `(sign, count, sides)`.
    Supports `'1d8'`, `'2d6 + 3'`, `'1d4+1'`, `'1'`, `'1d8 - 1'`, `'2d6+1d4+2'`.
    """
    raw = str(expr).strip().replace("\u2212", "-").replace("\u2013", "-")
    if not raw:
        raise ValueError("Empty dice expression")

    pos = 0
    dice_terms: list[tuple[int, int, int]] = []
    flat_mod = 0
    length = len(raw)

    while pos < length:
        while pos < length and raw[pos].isspace():
            pos += 1
        if pos >= length:
            break
        m = _TERM_RE.match(raw, pos)
        if not m:
            raise ValueError(f"Invalid dice expression: {expr!r}")
        sign_str, count_str, sides_str, flat_str = m.groups()
        if pos > 0 and not sign_str:
            raise ValueError(f"Missing operator in dice expression: {expr!r}")
        sign = -1 if sign_str == "-" else 1
        if count_str is not None and sides_str is not None:
            dice_terms.append((sign, int(count_str), int(sides_str)))
        elif flat_str is not None:
            flat_mod += sign * int(flat_str)
        pos = m.end()

    if not dice_terms and flat_mod == 0 and raw not in ("0", "+0", "-0"):
        raise ValueError(f"Could not parse dice expression: {expr!r}")
    return dice_terms, flat_mod


def scale_dice_expr(base_expr: str, add_expr: str | None, steps: int) -> str:
    """Scale a dice expression by adding `add_expr` `steps` times (e.g. upcasting)."""
    if not add_expr or steps <= 0:
        return str(base_expr).strip()
    base_terms, base_flat = parse_dice_expr(base_expr)
    add_terms, add_flat = parse_dice_expr(add_expr)

    merged: dict[tuple[int, int], int] = {}
    order: list[tuple[int, int]] = []
    for sign, count, sides in base_terms:
        key = (sign, sides)
        if key not in merged:
            order.append(key)
            merged[key] = 0
        merged[key] += count
    for sign, count, sides in add_terms:
        key = (sign, sides)
        if key not in merged:
            order.append(key)
            merged[key] = 0
        merged[key] += count * steps

    total_flat = base_flat + add_flat * steps
    parts: list[str] = []
    for idx, (sign, sides) in enumerate(order):
        cnt = merged[(sign, sides)]
        if cnt == 0:
            continue
        prefix = "-" if sign < 0 else ("+" if idx > 0 else "")
        parts.append(f"{prefix}{cnt}d{sides}")
    if total_flat != 0 or not parts:
        if total_flat > 0 and parts:
            parts.append(f"+{total_flat}")
        else:
            parts.append(str(total_flat))
    return "".join(parts)


def roll_dice_expr(
    expr: str,
    rng: DeterministicRNG | bool | None = None,
    crit: bool = False,
) -> dict[str, Any]:
    """Roll a dice expression using `rng` (or a default seed=1 DeterministicRNG).

    Accepts both `roll_dice_expr("1d8+3", rng, crit=True)` and
    `roll_dice_expr("1d8+3", crit=True)` (or `roll_dice_expr("1d8+3", True)`).
    """
    if isinstance(rng, bool):
        crit = rng
        rng_obj = DeterministicRNG(seed=1)
    elif rng is None:
        rng_obj = DeterministicRNG(seed=1)
    else:
        rng_obj = rng

    dice_terms, flat_mod = parse_dice_expr(expr)
    rolls: list[int] = []
    dice_sum = 0

    for sign, count, sides in dice_terms:
        effective_count = count * 2 if crit else count
        for _ in range(effective_count):
            r = rng_obj.roll_die(sides)
            rolls.append(r if sign > 0 else -r)
            dice_sum += sign * r

    total = dice_sum + flat_mod
    return {
        "expr": str(expr).strip(),
        "crit": bool(crit),
        "rolls": rolls,
        "modifier": flat_mod,
        "total": total,
    }
