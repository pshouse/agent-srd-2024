"""Conditions Rules IR compiler (§3.5, 15/15 = 100% full, 2024 rules)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import load_raw_json, make_provenance

SOURCE_FILE = "5e-SRD-Conditions.json"


def _base_modifiers(**overrides: Any) -> dict[str, Any]:
    mods: dict[str, Any] = {
        "speed_zero": False,
        "cannot_act": False,
        "breaks_concentration": False,
        "attacks_made": None,
        "attacks_against": None,
        "attacks_against_within_5ft": None,
        "attacks_against_beyond_5ft": None,
        "auto_crit_within_5ft": False,
        "auto_fail_saves": [],
        "save_disadvantage": [],
        "ability_checks": None,
        "initiative": None,
        "resist_all_damage": False,
        "immune_conditions": [],
        "d20_test_penalty_per_level": 0,
        "speed_penalty_ft_per_level": 0,
        "death_at_level": None,
        "special_rules": [],
    }
    mods.update(overrides)
    return mods


_CONDITION_SPECS: dict[str, tuple[list[str], dict[str, Any]]] = {
    "blinded": (
        [],
        _base_modifiers(
            attacks_made="disadvantage",
            attacks_against="advantage",
            special_rules=["auto_fail_sight_checks"],
        ),
    ),
    "charmed": (
        [],
        _base_modifiers(
            special_rules=["cannot_target_charmer", "charmer_advantage_social_checks"],
        ),
    ),
    "deafened": (
        [],
        _base_modifiers(
            special_rules=["auto_fail_hearing_checks"],
        ),
    ),
    "exhaustion": (
        [],
        _base_modifiers(
            d20_test_penalty_per_level=-2,
            speed_penalty_ft_per_level=-5,
            death_at_level=6,
        ),
    ),
    "frightened": (
        [],
        _base_modifiers(
            attacks_made="disadvantage",
            ability_checks="disadvantage",
            special_rules=["cannot_move_closer_to_source"],
        ),
    ),
    "grappled": (
        [],
        _base_modifiers(
            speed_zero=True,
            special_rules=["disadvantage_attacks_vs_non_grappler"],
        ),
    ),
    "incapacitated": (
        [],
        _base_modifiers(
            cannot_act=True,
            breaks_concentration=True,
            initiative="disadvantage",
        ),
    ),
    "invisible": (
        [],
        _base_modifiers(
            initiative="advantage",
            attacks_made="advantage",
            attacks_against="disadvantage",
        ),
    ),
    "paralyzed": (
        ["incapacitated"],
        _base_modifiers(
            speed_zero=True,
            cannot_act=True,
            breaks_concentration=True,
            auto_fail_saves=["str", "dex"],
            attacks_against="advantage",
            auto_crit_within_5ft=True,
        ),
    ),
    "petrified": (
        ["incapacitated"],
        _base_modifiers(
            speed_zero=True,
            cannot_act=True,
            breaks_concentration=True,
            auto_fail_saves=["str", "dex"],
            attacks_against="advantage",
            resist_all_damage=True,
            immune_conditions=["poisoned"],
        ),
    ),
    "poisoned": (
        [],
        _base_modifiers(
            attacks_made="disadvantage",
            ability_checks="disadvantage",
        ),
    ),
    "prone": (
        [],
        _base_modifiers(
            attacks_made="disadvantage",
            attacks_against_within_5ft="advantage",
            attacks_against_beyond_5ft="disadvantage",
            special_rules=["crawl_double_cost", "stand_up_half_speed"],
        ),
    ),
    "restrained": (
        [],
        _base_modifiers(
            speed_zero=True,
            attacks_made="disadvantage",
            attacks_against="advantage",
            save_disadvantage=["dex"],
        ),
    ),
    "stunned": (
        ["incapacitated"],
        _base_modifiers(
            cannot_act=True,
            breaks_concentration=True,
            auto_fail_saves=["str", "dex"],
            attacks_against="advantage",
        ),
    ),
    "unconscious": (
        ["incapacitated", "prone"],
        _base_modifiers(
            speed_zero=True,
            cannot_act=True,
            breaks_concentration=True,
            auto_fail_saves=["str", "dex"],
            attacks_against="advantage",
            auto_crit_within_5ft=True,
            special_rules=["remains_prone_when_ended"],
        ),
    ),
}


def compile_conditions(en_dir: Path) -> dict[str, dict[str, Any]]:
    """Compile all 15 SRD 2024 conditions into typed executable IR."""
    records = load_raw_json(en_dir, SOURCE_FILE)
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        if slug not in _CONDITION_SPECS:
            raise ValueError(f"Unexpected condition slug: {slug!r}")
        implies, modifiers = _CONDITION_SPECS[slug]
        eid = f"conditions:{slug}"
        compiled[slug] = {
            "id": eid,
            "slug": slug,
            "name": rec["name"],
            "implies": list(implies),
            "modifiers": dict(modifiers),
            "description": rec.get("description", ""),
            "coverage": "full",
            "provenance": make_provenance(eid, SOURCE_FILE, "template+structured", []),
        }

    return compiled
