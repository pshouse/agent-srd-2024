"""Weapon Masteries Rules IR compiler (§3.4, 8/8 = 100% full)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import load_raw_json, make_provenance

SOURCE_FILE = "5e-SRD-Weapon-Mastery-Properties.json"

_MASTERY_SPECS: dict[str, dict[str, Any]] = {
    "cleave": {
        "trigger": "on_hit",
        "requires_damage": False,
        "requires_melee": True,
        "once_per_turn": True,
        "max_target_size": None,
        "effect": {
            "kind": "extra_attack",
            "adjacent_distance_ft": 5,
            "within_reach": True,
            "add_positive_ability_mod": False,
        },
    },
    "graze": {
        "trigger": "on_miss",
        "requires_damage": False,
        "requires_melee": False,
        "once_per_turn": False,
        "max_target_size": None,
        "effect": {
            "kind": "guaranteed_damage",
            "amount": "attack_ability_mod",
            "min_damage": 0,
            "same_weapon_damage_type": True,
        },
    },
    "nick": {
        "trigger": "on_light_extra_attack",
        "requires_damage": False,
        "requires_melee": False,
        "once_per_turn": True,
        "max_target_size": None,
        "effect": {
            "kind": "move_light_extra_to_attack_action",
            "add_positive_ability_mod": False,
        },
    },
    "push": {
        "trigger": "on_hit",
        "requires_damage": False,
        "requires_melee": False,
        "once_per_turn": False,
        "max_target_size": "Large",
        "effect": {
            "kind": "forced_move",
            "distance_ft": 10,
            "direction": "away",
        },
    },
    "sap": {
        "trigger": "on_hit",
        "requires_damage": False,
        "requires_melee": False,
        "once_per_turn": False,
        "max_target_size": None,
        "effect": {
            "kind": "apply_timed_effect",
            "effect_kind": "sap",
            "target_next_attack_disadvantage": True,
            "expires": "start_of_attacker_next_turn",
        },
    },
    "slow": {
        "trigger": "on_hit",
        "requires_damage": True,
        "requires_melee": False,
        "once_per_turn": False,
        "max_target_size": None,
        "effect": {
            "kind": "apply_timed_effect",
            "effect_kind": "slow",
            "speed_penalty_ft": 10,
            "stacking": "max",
            "expires": "start_of_attacker_next_turn",
        },
    },
    "topple": {
        "trigger": "on_hit",
        "requires_damage": False,
        "requires_melee": False,
        "once_per_turn": False,
        "max_target_size": None,
        "effect": {
            "kind": "save_condition",
            "save_ability": "con",
            "dc_formula": "8 + attack_ability_mod + proficiency_bonus",
            "on_fail_condition": "prone",
        },
    },
    "vex": {
        "trigger": "on_hit",
        "requires_damage": True,
        "requires_melee": False,
        "once_per_turn": False,
        "max_target_size": None,
        "effect": {
            "kind": "apply_timed_effect",
            "effect_kind": "vex",
            "attacker_next_attack_advantage_vs_target": True,
            "expires": "end_of_attacker_next_turn",
        },
    },
}


def compile_weapon_masteries(en_dir: Path) -> dict[str, dict[str, Any]]:
    """Compile all 8 SRD 2024 weapon masteries into typed executable IR."""
    records = load_raw_json(en_dir, SOURCE_FILE)
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        if slug not in _MASTERY_SPECS:
            raise ValueError(f"Unexpected weapon mastery slug: {slug!r}")
        spec = _MASTERY_SPECS[slug]
        eid = f"weapon-mastery-properties:{slug}"
        compiled[slug] = {
            "id": eid,
            "slug": slug,
            "name": rec["name"],
            "trigger": spec["trigger"],
            "requires_damage": spec["requires_damage"],
            "requires_melee": spec["requires_melee"],
            "once_per_turn": spec["once_per_turn"],
            "max_target_size": spec["max_target_size"],
            "effect": spec["effect"],
            "description": rec.get("description", ""),
            "coverage": "full",
            "provenance": make_provenance(eid, SOURCE_FILE, "template+structured", []),
        }

    return compiled
