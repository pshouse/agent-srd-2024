"""Spells Rules IR compiler (§3.7, all 339 spells)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import (
    ABILITY_NAME_TO_SLUG,
    CONDITION_SLUGS,
    DAMAGE_TYPE_SLUGS,
    clean_dice_expr,
    load_raw_json,
    make_provenance,
    normalize_prose,
    parse_aoe,
)

SOURCE_FILE = "5e-SRD-Spells.json"

_RANGE_COMPONENT_QUIRK_RE = re.compile(r"\s+Component:.*$", re.IGNORECASE)
_RANGE_FEET_RE = re.compile(r"^(\d+)\s+feet$", re.IGNORECASE)
_RANGE_MILES_RE = re.compile(r"^(\d+)\s+miles?$", re.IGNORECASE)

_SAVE_RE = re.compile(
    r"\b(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+saving\s+throw\b",
    re.IGNORECASE,
)
_ATTACK_RE = re.compile(r"\b(melee|ranged)\s+spell\s+attack\b", re.IGNORECASE)
_DAMAGE_RE = re.compile(
    r"\b(\d+d\d+(?:\s*\+\s*\d+)?)\s+"
    r"(?:plus\s+your\s+spellcasting\s+ability\s+modifier\s+)?"
    r"(Acid|Bludgeoning|Cold|Fire|Force|Lightning|Necrotic|Piercing|Poison|Psychic|Radiant|Slashing|Thunder)\s+damage\b",
    re.IGNORECASE,
)
_DAMAGE_MOD_RE = re.compile(
    r"\b(\d+d\d+)\s+plus\s+your\s+spellcasting\s+ability\s+modifier\s+"
    r"(Acid|Bludgeoning|Cold|Fire|Force|Lightning|Necrotic|Piercing|Poison|Psychic|Radiant|Slashing|Thunder)\s+damage\b",
    re.IGNORECASE,
)
_HEAL_DICE_RE = re.compile(
    r"(?:regains?|restores?|restoring)\s+(?:Hit\s+Points\s+equal\s+to\s+)?(\d+d\d+)(?:\s*\+\s*(\d+))?",
    re.IGNORECASE,
)
_HEAL_FLAT_RE = re.compile(
    r"(?:regains?|restores?|restoring)\s+(\d+)\s+Hit\s+Points\b",
    re.IGNORECASE,
)
_TEMP_HP_DICE_RE = re.compile(
    r"(\d+d\d+)(?:\s*\+\s*(\d+))?\s+Temporary\s+Hit\s+Points\b",
    re.IGNORECASE,
)
_TEMP_HP_FLAT_RE = re.compile(
    r"\b(\d+)\s+Temporary\s+Hit\s+Points\b",
    re.IGNORECASE,
)
_COND_LIST = "|".join(sorted(CONDITION_SLUGS))
_COND_APPLIED_RE = re.compile(
    rf"\b(?:has|have|gains?|is|are|becomes?|inflicts?)\s+(?:the\s+)?({_COND_LIST})(?:\s+condition)?\b",
    re.IGNORECASE,
)
_COND_ENDED_RE = re.compile(
    rf"\bends?\s+(?:the\s+|one\s+condition\s+on\s+it:\s*)?([^.]*?\b(?:{_COND_LIST})\b[^.]*?)(?:\s+condition|\.)",
    re.IGNORECASE,
)
_CANTRIP_TIERS_RE = re.compile(
    r"levels?\s+5\s*\((\d+d\d+)\),\s*11\s*\((\d+d\d+)\),\s*and\s*17\s*\((\d+d\d+)\)",
    re.IGNORECASE,
)
_UPCAST_DMG_RE = re.compile(
    r"damage\s+increases\s+by\s+(\d+d\d+|\d+)\s+for\s+each\s+spell\s+slot\s+level\s+above",
    re.IGNORECASE,
)
_UPCAST_HEAL_RE = re.compile(
    r"healing\s+increases\s+by\s+(\d+d\d+|\d+)\s+for\s+each\s+spell\s+slot\s+level\s+above",
    re.IGNORECASE,
)
_UPCAST_TARGETS_RE = re.compile(
    r"target\s+(one|two|three|\d+)\s+additional\s+(?:creature|creatures|humanoid|humanoids|target|targets)\s+for\s+each\s+spell\s+slot\s+level\s+above",
    re.IGNORECASE,
)
_UPCAST_PROJ_RE = re.compile(
    r"(?:creates?|create)\s+(one|two|\d+)\s+(?:more|additional)\s+(?:dart|ray|missile|beam|bolt|star|meteor)s?\s+for\s+each\s+spell\s+slot\s+level\s+above",
    re.IGNORECASE,
)

_WORD_TO_INT = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}

# Genuine non-combat / divination / illusion / exploration / social / world-shaping spells:
# ALWAYS graded `narrative` so we never fake executable combat mechanics.
_TRUE_NARRATIVE_SPELLS: frozenset[str] = frozenset(
    {
        "alarm",
        "animal-messenger",
        "animal-shapes",
        "antilife-shell",
        "antimagic-field",
        "arcane-eye",
        "arcane-lock",
        "arcanists-magic-aura",
        "astral-projection",
        "augury",
        "awaken",
        "beast-sense",
        "clairvoyance",
        "clone",
        "commune",
        "commune-with-nature",
        "comprehend-languages",
        "contact-other-plane",
        "continual-flame",
        "control-weather",
        "create-food-and-water",
        "create-or-destroy-water",
        "create-undead",
        "creation",
        "dancing-lights",
        "darkness",
        "darkvision",
        "daylight",
        "demiplane",
        "detect-evil-and-good",
        "detect-magic",
        "detect-poison-and-disease",
        "disguise-self",
        "divination",
        "dream",
        "druidcraft",
        "etherealness",
        "fabricate",
        "find-familiar",
        "find-the-path",
        "find-traps",
        "floating-disk",
        "fog-cloud",
        "gentle-repose",
        "glibness",
        "guards-and-wards",
        "hallucinatory-terrain",
        "identify",
        "illusory-script",
        "instant-summons",
        "knock",
        "legend-lore",
        "light",
        "locate-animals-or-plants",
        "locate-creature",
        "locate-object",
        "mage-hand",
        "magic-mouth",
        "magnificent-mansion",
        "major-image",
        "meld-into-stone",
        "mending",
        "message",
        "minor-illusion",
        "mirage-arcane",
        "mislead",
        "move-earth",
        "nondetection",
        "pass-without-trace",
        "passwall",
        "phantom-steed",
        "planar-ally",
        "plane-shift",
        "plant-growth",
        "prestidigitation",
        "private-sanctum",
        "programmed-illusion",
        "project-image",
        "purify-food-and-drink",
        "raise-dead",
        "reincarnate",
        "remove-curse",
        "resurrection",
        "rope-trick",
        "scrying",
        "secret-chest",
        "see-invisibility",
        "sending",
        "sequester",
        "silent-image",
        "simulacrum",
        "speak-with-animals",
        "speak-with-dead",
        "speak-with-plants",
        "stone-shape",
        "telepathic-bond",
        "teleport",
        "teleportation-circle",
        "thaumaturgy",
        "time-stop",
        "tiny-hut",
        "tongues",
        "transport-via-plants",
        "tree-stride",
        "true-resurrection",
        "true-seeing",
        "unseen-servant",
        "wall-of-force",
        "water-breathing",
        "water-walk",
        "wind-walk",
        "wish",
        "word-of-recall",
    }
)

# Spells that have real combat/save/condition mechanics but also significant non-numeric
# behavioral/social/world riders, so they should honestly be `partial` (never `full`).
_FORCE_PARTIAL_REASON: dict[str, str] = {
    "banishment": "extradimensional_exile_and_native_plane_rules",
    "befuddlement": "spellcasting_block_and_30_day_repeat_save",
    "charm-monster": "social_friendly_attitude_rules",
    "charm-person": "social_friendly_attitude_rules",
    "command": "one_word_behavioral_command_adjudication",
    "compulsion": "forced_directional_movement_each_turn",
    "fire-shield": "retaliatory_melee_damage_and_warm_chill_choice",
    "holy-aura": "aura_advantage_and_retaliatory_blindness",
    "irresistible-dance": "forced_dance_movement_and_action_spend",
    "power-word-heal": "full_hp_restore_and_stand_up_reaction",
    "power-word-kill": "hp_threshold_100_instant_death",
    "power-word-stun": "hp_threshold_150_stun_or_speed_zero",
}

# Complex prose markers that indicate secondary mechanics not fully modeled by basic IR
_COMPLEX_CLAUSE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("summoning_or_minion_statblock", re.compile(r"\b(?:summon|conjure|stat\s+block|spirit\s+manifests|manifests\s+in\s+an\s+unoccupied)\b", re.IGNORECASE)),
    ("persistent_zone_or_wall", re.compile(r"\b(?:wall\s+of|enters\s+the\s+(?:area|sphere|cube|cylinder|cloud|emanation|zone)\s+for\s+the\s+first\s+time|ends\s+its\s+turn\s+(?:there|in\s+the)|difficult\s+terrain|heavily\s+obscured)\b", re.IGNORECASE)),
    ("ongoing_turn_trigger", re.compile(r"\b(?:at\s+the\s+start\s+of\s+each\s+of\s+its\s+turns|on\s+later\s+turns|as\s+a\s+Bonus\s+Action\s+on\s+your\s+later\s+turns|until\s+the\s+spell\s+ends,\s+you\s+can\s+use)\b", re.IGNORECASE)),
    ("transformation_or_polymorph", re.compile(r"\b(?:transforms?\s+into|new\s+form|shape-?shift|petrified\s+for\s+the\s+duration)\b", re.IGNORECASE)),
    ("forced_movement_or_terrain", re.compile(r"\b(?:pushed\s+\d+\s+feet|pulled\s+\d+\s+feet|teleports?\s+to|banished|exiled|extradimensional)\b", re.IGNORECASE)),
    ("instant_death_or_disintegration", re.compile(r"\b(?:turns?\s+to\s+dust|disintegrated|dies\s+instantly|drops?\s+to\s+0\s+Hit\s+Points\s+and\s+dies|reduces?\s+its\s+Hit\s+Point\s+maximum)\b", re.IGNORECASE)),
    ("special_narrative_rider", re.compile(r"\b(?:chooses?\s+one\s+of\s+the\s+following\s+effects|roll\s+on\s+the|table\s+below|material\s+plane|ethereal\s+plane)\b", re.IGNORECASE)),
]


def _parse_action_type(casting_time: str) -> tuple[str, str | None]:
    ct = normalize_prose(casting_time).strip()
    low = ct.lower()
    if low.startswith("bonus action"):
        return "bonus_action", None
    if low.startswith("reaction"):
        trigger = ct[len("Reaction"):].lstrip(", ").strip() or None
        return "reaction", trigger
    if low.startswith("action"):
        return "action", None
    return "long", None


def _parse_range(raw_range: str) -> tuple[str, str, int | None]:
    cleaned = _RANGE_COMPONENT_QUIRK_RE.sub("", normalize_prose(raw_range)).strip()
    low = cleaned.lower()
    if low.startswith("self"):
        return cleaned, "self", 0
    if low == "touch":
        return cleaned, "touch", 5
    if low == "sight":
        return cleaned, "sight", None
    if low == "unlimited":
        return cleaned, "unlimited", None
    if low == "special":
        return cleaned, "special", None
    m = _RANGE_FEET_RE.match(cleaned)
    if m:
        return cleaned, "ranged", int(m.group(1))
    m = _RANGE_MILES_RE.match(cleaned)
    if m:
        return cleaned, "ranged", int(m.group(1)) * 5280
    return cleaned, "special", None


def _parse_duration_rounds(raw_duration: str) -> int | None:
    low = normalize_prose(raw_duration).strip().lower()
    if low == "instantaneous":
        return 0
    low = re.sub(r"^(?:concentration,?\s*)?(?:up\s+to\s+)?", "", low)
    m = re.match(r"^(\d+)\s+rounds?$", low)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)\s+minutes?$", low)
    if m:
        return int(m.group(1)) * 10
    m = re.match(r"^(\d+)\s+hours?$", low)
    if m:
        return int(m.group(1)) * 600
    return None


def _extract_scaling(
    level: int,
    desc_norm: str,
    higher_level_norm: str,
    base_damage_dice: str | None,
) -> dict[str, Any]:
    cantrip_tiers: dict[str, str] | None = None
    slot_damage_per_level: str | None = None
    slot_healing_per_level: str | None = None
    slot_targets_per_level = 0
    slot_projectiles_per_level = 0

    if level == 0:
        cm = _CANTRIP_TIERS_RE.search(desc_norm)
        if cm and base_damage_dice:
            cantrip_tiers = {
                "1": base_damage_dice,
                "5": cm.group(1).lower(),
                "11": cm.group(2).lower(),
                "17": cm.group(3).lower(),
            }
    else:
        hl_section = higher_level_norm
        if not hl_section:
            idx = desc_norm.lower().find("using a higher-level spell slot")
            if idx != -1:
                hl_section = desc_norm[idx:]
        if hl_section:
            dm = _UPCAST_DMG_RE.search(hl_section)
            if dm:
                slot_damage_per_level = clean_dice_expr(dm.group(1))
            hm = _UPCAST_HEAL_RE.search(hl_section)
            if hm:
                slot_healing_per_level = clean_dice_expr(hm.group(1))
            tm = _UPCAST_TARGETS_RE.search(hl_section)
            if tm:
                raw_n = tm.group(1).lower()
                slot_targets_per_level = _WORD_TO_INT.get(raw_n, int(raw_n) if raw_n.isdigit() else 1)
            pm = _UPCAST_PROJ_RE.search(hl_section)
            if pm:
                raw_n = pm.group(1).lower()
                slot_projectiles_per_level = _WORD_TO_INT.get(raw_n, int(raw_n) if raw_n.isdigit() else 1)

    return {
        "cantrip_tiers": cantrip_tiers,
        "slot_damage_per_level": slot_damage_per_level,
        "slot_healing_per_level": slot_healing_per_level,
        "slot_targets_per_level": slot_targets_per_level,
        "slot_projectiles_per_level": slot_projectiles_per_level,
    }


def _extract_conditions(
    desc_norm: str,
    save_ability: str | None,
    has_attack: bool,
    concentration: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    ended: list[str] = []
    for em in _COND_ENDED_RE.finditer(desc_norm):
        chunk = em.group(1).lower()
        for cond in sorted(CONDITION_SLUGS):
            if re.search(rf"\b{cond}\b", chunk) and cond not in ended:
                ended.append(cond)

    applied: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", desc_norm):
        low = sentence.lower()
        if any(w in low for w in ("ends the", "end the", "end one condition", "immune to", "immunity to", "can't be", "cannot be", "doesn't have", "no longer")):
            continue
        for cm in _COND_APPLIED_RE.finditer(sentence):
            cond = cm.group(1).lower()
            if cond in seen or cond in ended:
                continue
            seen.add(cond)
            if save_ability:
                trigger = "on_fail"
            elif has_attack:
                trigger = "on_hit"
            else:
                trigger = "auto"

            repeat_ab: str | None = None
            if "repeats the save" in desc_norm.lower() or "repeat the saving throw" in desc_norm.lower() or "makes another" in desc_norm.lower():
                duration = "save_ends"
                repeat_ab = save_ability
            elif "until the start of your next turn" in low:
                duration = "start_of_next_turn"
            elif "until the end of your next turn" in low or "until the end of its next turn" in low:
                duration = "end_of_next_turn"
            elif concentration:
                duration = "concentration"
            else:
                duration = "spell_duration"

            applied.append(
                {
                    "condition": cond,
                    "trigger": trigger,
                    "duration": duration,
                    "repeat_save_ability": repeat_ab,
                }
            )
    return applied, sorted(ended)


# Curated template+structured definitions for iconic combat, healing, buff, and cantrip spells (§3.7)
_ICONIC_TEMPLATES: dict[str, dict[str, Any]] = {
    "fireball": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "sphere", "size_ft": 20, "width_ft": None}},
        "resolution": "save",
        "save": {"ability": "dex", "on_success": "half"},
        "damage": [{"dice": "8d6", "type": "fire", "add_spell_mod": False}],
        "scaling": {"slot_damage_per_level": "1d6"},
        "coverage": "full",
    },
    "lightning-bolt": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "line", "size_ft": 100, "width_ft": 5}},
        "resolution": "save",
        "save": {"ability": "dex", "on_success": "half"},
        "damage": [{"dice": "8d6", "type": "lightning", "add_spell_mod": False}],
        "scaling": {"slot_damage_per_level": "1d6"},
        "coverage": "full",
    },
    "burning-hands": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "cone", "size_ft": 15, "width_ft": None}},
        "resolution": "save",
        "save": {"ability": "dex", "on_success": "half"},
        "damage": [{"dice": "3d6", "type": "fire", "add_spell_mod": False}],
        "scaling": {"slot_damage_per_level": "1d6"},
        "coverage": "full",
    },
    "shatter": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "sphere", "size_ft": 10, "width_ft": None}},
        "resolution": "save",
        "save": {"ability": "con", "on_success": "half"},
        "damage": [{"dice": "3d8", "type": "thunder", "add_spell_mod": False}],
        "scaling": {"slot_damage_per_level": "1d8"},
        "coverage": "full",
    },
    "thunderwave": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "cube", "size_ft": 15, "width_ft": None}},
        "resolution": "save",
        "save": {"ability": "con", "on_success": "half"},
        "damage": [{"dice": "2d8", "type": "thunder", "add_spell_mod": False}],
        "buffs": [{"kind": "forced_push_ft", "value": 10, "trigger": "on_fail"}],
        "scaling": {"slot_damage_per_level": "1d8"},
        "coverage": "full",
    },
    "magic-missile": {
        "targeting": {"mode": "multi_target", "max_targets": 3, "aoe": None},
        "resolution": "auto_hit",
        "damage": [{"dice": "1d4+1", "type": "force", "add_spell_mod": False}],
        "special_effect": {"kind": "multi_projectile", "projectiles_base": 3, "slot_projectiles_per_level": 1, "blocked_by_spell": "shield"},
        "scaling": {"slot_projectiles_per_level": 1},
        "coverage": "full",
    },
    "scorching-ray": {
        "targeting": {"mode": "multi_target", "max_targets": 3, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "2d6", "type": "fire", "add_spell_mod": False}],
        "special_effect": {"kind": "multi_projectile", "projectiles_base": 3, "slot_projectiles_per_level": 1},
        "scaling": {"slot_projectiles_per_level": 1},
        "coverage": "full",
    },
    "guiding-bolt": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "4d6", "type": "radiant", "add_spell_mod": False}],
        "buffs": [{"kind": "next_attack_advantage", "duration": "end_of_next_turn"}],
        "scaling": {"slot_damage_per_level": "1d6"},
        "coverage": "full",
    },
    "fire-bolt": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "1d10", "type": "fire", "add_spell_mod": False}],
        "scaling": {"cantrip_tiers": {"1": "1d10", "5": "2d10", "11": "3d10", "17": "4d10"}},
        "coverage": "full",
    },
    "sacred-flame": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "save",
        "save": {"ability": "dex", "on_success": "none"},
        "damage": [{"dice": "1d8", "type": "radiant", "add_spell_mod": False}],
        "scaling": {"cantrip_tiers": {"1": "1d8", "5": "2d8", "11": "3d8", "17": "4d8"}},
        "coverage": "full",
    },
    "eldritch-blast": {
        "targeting": {"mode": "multi_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "1d10", "type": "force", "add_spell_mod": False}],
        "special_effect": {"kind": "cantrip_beams", "beams_by_level": {"1": 1, "5": 2, "11": 3, "17": 4}},
        "scaling": {"cantrip_tiers": {"1": "1d10", "5": "2d10", "11": "3d10", "17": "4d10"}},
        "coverage": "full",
    },
    "acid-splash": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "sphere", "size_ft": 5, "width_ft": None}},
        "resolution": "save",
        "save": {"ability": "dex", "on_success": "none"},
        "damage": [{"dice": "1d6", "type": "acid", "add_spell_mod": False}],
        "scaling": {"cantrip_tiers": {"1": "1d6", "5": "2d6", "11": "3d6", "17": "4d6"}},
        "coverage": "full",
    },
    "ray-of-frost": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "1d8", "type": "cold", "add_spell_mod": False}],
        "buffs": [{"kind": "speed_penalty_ft", "value": 10, "duration": "start_of_next_turn"}],
        "scaling": {"cantrip_tiers": {"1": "1d8", "5": "2d8", "11": "3d8", "17": "4d8"}},
        "coverage": "full",
    },
    "chill-touch": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "melee",
        "damage": [{"dice": "1d10", "type": "necrotic", "add_spell_mod": False}],
        "special_effect": {"kind": "prevent_healing", "duration": "end_of_next_turn"},
        "scaling": {"cantrip_tiers": {"1": "1d10", "5": "2d10", "11": "3d10", "17": "4d10"}},
        "coverage": "full",
    },
    "vicious-mockery": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "save",
        "save": {"ability": "wis", "on_success": "none"},
        "damage": [{"dice": "1d6", "type": "psychic", "add_spell_mod": False}],
        "buffs": [{"kind": "target_next_attack_disadvantage", "duration": "end_of_target_next_turn"}],
        "scaling": {"cantrip_tiers": {"1": "1d6", "5": "2d6", "11": "3d6", "17": "4d6"}},
        "coverage": "full",
    },
    "poison-spray": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "1d12", "type": "poison", "add_spell_mod": False}],
        "scaling": {"cantrip_tiers": {"1": "1d12", "5": "2d12", "11": "3d12", "17": "4d12"}},
        "coverage": "full",
    },
    "shocking-grasp": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "melee",
        "damage": [{"dice": "1d8", "type": "lightning", "add_spell_mod": False}],
        "special_effect": {"kind": "no_opportunity_attacks", "duration": "start_of_target_next_turn"},
        "scaling": {"cantrip_tiers": {"1": "1d8", "5": "2d8", "11": "3d8", "17": "4d8"}},
        "coverage": "full",
    },
    "true-strike": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "melee",
        "damage": [{"dice": "1d6", "type": "radiant", "add_spell_mod": True}],
        "special_effect": {"kind": "weapon_attack_with_spell_mod", "extra_radiant_by_level": {"1": "0", "5": "1d6", "11": "2d6", "17": "3d6"}},
        "scaling": {"cantrip_tiers": {"1": "0", "5": "1d6", "11": "2d6", "17": "3d6"}},
        "coverage": "full",
    },
    "chromatic-orb": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "3d8", "type": "acid", "add_spell_mod": False}],
        "special_effect": {"kind": "variable_damage_type", "choices": ["acid", "cold", "fire", "lightning", "poison", "thunder"]},
        "scaling": {"slot_damage_per_level": "1d8"},
        "coverage": "partial",
        "unmodeled_clauses": ["leap_on_matching_damage_dice"],
    },
    "sorcerous-burst": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "attack",
        "attack_kind": "ranged",
        "damage": [{"dice": "1d8", "type": "acid", "add_spell_mod": False}],
        "special_effect": {"kind": "variable_damage_type", "choices": ["acid", "cold", "fire", "lightning", "poison", "psychic", "thunder"]},
        "scaling": {"cantrip_tiers": {"1": "1d8", "5": "2d8", "11": "3d8", "17": "4d8"}},
        "coverage": "partial",
        "unmodeled_clauses": ["exploding_d8_bonus_dice"],
    },
    "cure-wounds": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": "2d8", "flat": 0, "add_spell_mod": True},
        "scaling": {"slot_healing_per_level": "2d8"},
        "coverage": "full",
    },
    "healing-word": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": "2d4", "flat": 0, "add_spell_mod": True},
        "scaling": {"slot_healing_per_level": "2d4"},
        "coverage": "full",
    },
    "heal": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": None, "flat": 70, "add_spell_mod": False},
        "conditions_ended": ["blinded", "deafened", "poisoned"],
        "scaling": {"slot_healing_per_level": "10"},
        "coverage": "full",
    },
    "lesser-restoration": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "conditions_ended": ["blinded", "deafened", "paralyzed", "poisoned"],
        "coverage": "full",
    },
    "greater-restoration": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "conditions_ended": ["charmed", "petrified"],
        "coverage": "partial",
        "unmodeled_clauses": ["exhaustion_reduction_and_curse_removal"],
    },
    "spare-the-dying": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "special_effect": {"kind": "stabilize_zero_hp"},
        "coverage": "full",
    },
    "shield": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "ac_bonus", "value": 5, "duration": "start_of_next_turn", "blocks_spell": "magic-missile"}],
        "coverage": "full",
    },
    "mage-armor": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "ac_formula", "base": 13, "add_dex": True, "requires_unarmored": True}],
        "coverage": "full",
    },
    "shield-of-faith": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "ac_bonus", "value": 2, "duration": "concentration"}],
        "coverage": "full",
    },
    "barkskin": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "ac_min", "value": 17}],
        "coverage": "full",
    },
    "blur": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "attacks_against_disadvantage", "duration": "concentration"}],
        "coverage": "full",
    },
    "stoneskin": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "damage_resistance", "types": ["bludgeoning", "piercing", "slashing"], "duration": "concentration"}],
        "coverage": "full",
    },
    "protection-from-energy": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "damage_resistance", "choices": ["acid", "cold", "fire", "lightning", "thunder"], "duration": "concentration"}],
        "coverage": "full",
    },
    "beacon-of-hope": {
        "targeting": {"mode": "multi_target", "max_targets": None, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "save_advantage", "abilities": ["wis"], "duration": "concentration"}, {"kind": "max_healing", "duration": "concentration"}],
        "coverage": "full",
    },
    "death-ward": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "prevent_drop_to_zero_hp_once", "duration": "spell_duration"}],
        "coverage": "full",
    },
    "bless": {
        "targeting": {"mode": "multi_target", "max_targets": 3, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "roll_bonus_dice", "dice": "1d4", "applies_to": ["attack", "save"], "sign": 1}],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "full",
    },
    "bane": {
        "targeting": {"mode": "multi_target", "max_targets": 3, "aoe": None},
        "resolution": "save",
        "save": {"ability": "cha", "on_success": "none"},
        "buffs": [{"kind": "roll_bonus_dice", "dice": "1d4", "applies_to": ["attack", "save"], "sign": -1}],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "full",
    },
    "guidance": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "roll_bonus_dice", "dice": "1d4", "applies_to": ["ability_check"], "sign": 1}],
        "coverage": "full",
    },
    "resistance": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "damage_reduction_dice", "dice": "1d4", "duration": "concentration"}],
        "coverage": "full",
    },
    "hold-person": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "save",
        "save": {"ability": "wis", "on_success": "none"},
        "conditions_applied": [
            {"condition": "paralyzed", "trigger": "on_fail", "duration": "save_ends", "repeat_save_ability": "wis"}
        ],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "full",
    },
    "hold-monster": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "save",
        "save": {"ability": "wis", "on_success": "none"},
        "conditions_applied": [
            {"condition": "paralyzed", "trigger": "on_fail", "duration": "save_ends", "repeat_save_ability": "wis"}
        ],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "full",
    },
    "misty-step": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "teleport", "max_distance_ft": 30}],
        "coverage": "full",
    },
    "false-life": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "temp_hp": {"dice": "2d4", "flat": 4, "add_spell_mod": False},
        "special_effect": {"kind": "temp_hp_upcast_flat", "per_level": 5},
        "coverage": "full",
    },
    "longstrider": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "speed_bonus_ft", "value": 10}],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "full",
    },
    "expeditious-retreat": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "bonus_action_dash", "duration": "concentration"}],
        "coverage": "full",
    },
    "jump": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "jump_bonus_ft", "value": 30, "duration": "spell_duration"}],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "full",
    },
    "fly": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "fly_speed_ft", "value": 60, "duration": "concentration"}],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "full",
    },
    "spider-climb": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "climb_speed_eq_walk", "duration": "concentration"}],
        "coverage": "full",
    },
    "feather-fall": {
        "targeting": {"mode": "multi_target", "max_targets": 5, "aoe": None},
        "resolution": "auto_apply",
        "special_effect": {"kind": "negate_falling_damage"},
        "coverage": "full",
    },
    "foresight": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [
            {"kind": "all_d20_advantage", "duration": "spell_duration"},
            {"kind": "attacks_against_disadvantage", "duration": "spell_duration"},
        ],
        "coverage": "full",
    },
    "magic-weapon": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "weapon_attack_damage_bonus", "value": 1, "duration": "spell_duration"}],
        "coverage": "full",
    },
    "haste": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [
            {"kind": "ac_bonus", "value": 2, "duration": "concentration"},
            {"kind": "speed_multiplier", "value": 2},
        ],
        "coverage": "partial",
        "unmodeled_clauses": ["extra_haste_action_and_lethargy_on_end"],
    },
    "warding-bond": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [
            {"kind": "ac_bonus", "value": 1, "duration": "spell_duration"},
            {"kind": "save_bonus", "value": 1, "duration": "spell_duration"},
        ],
        "coverage": "partial",
        "unmodeled_clauses": ["shared_damage_transfer_within_60ft"],
    },
    "aura-of-life": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "emanation", "size_ft": 30, "width_ft": None}},
        "resolution": "auto_apply",
        "buffs": [{"kind": "damage_resistance", "types": ["necrotic"], "duration": "concentration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["zero_hp_start_of_turn_revive"],
    },
    "mind-blank": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [
            {"kind": "damage_immunity", "types": ["psychic"], "duration": "spell_duration"},
            {"kind": "condition_immunity", "conditions": ["charmed"], "duration": "spell_duration"},
        ],
        "coverage": "partial",
        "unmodeled_clauses": ["divination_and_mind_reading_immunity"],
    },
    "protection-from-evil-and-good": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "attacks_against_disadvantage_by_creature_type", "duration": "concentration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["creature_type_filtered_protection_and_possession_immunity"],
    },
    "freedom-of-movement": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "ignore_difficult_terrain_and_paralyzed_restrained", "duration": "spell_duration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["escape_grapple_cost_and_underwater_rules"],
    },
    "enhance-ability": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "ability_check_advantage", "duration": "concentration"}],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "partial",
        "unmodeled_clauses": ["chosen_ability_selection"],
    },
    "shillelagh": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "weapon_spell_ability_and_die", "damage_dice": "1d8", "duration": "spell_duration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["club_or_quarterstaff_cantrip_die_scaling"],
    },
    "goodberry": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": None, "flat": 1, "add_spell_mod": False},
        "coverage": "partial",
        "unmodeled_clauses": ["creates_10_consumable_berries"],
    },
    "heroes-feast": {
        "targeting": {"mode": "multi_target", "max_targets": 12, "aoe": None},
        "resolution": "auto_apply",
        "temp_hp": {"dice": "2d10", "flat": 0, "add_spell_mod": False},
        "buffs": [{"kind": "condition_immunity", "conditions": ["poisoned", "frightened"], "duration": "spell_duration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["feast_consumption_and_max_hp_boost"],
    },
    "blink": {
        "targeting": {"mode": "self", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "special_effect": {"kind": "ethereal_blink_d20_11"},
        "coverage": "partial",
        "unmodeled_clauses": ["ethereal_plane_vanish_and_return"],
    },
    "dispel-magic": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "special_effect": {"kind": "dispel_magic_dc"},
        "coverage": "partial",
        "unmodeled_clauses": ["spellcasting_ability_check_for_higher_slot_spells"],
    },
    "globe-of-invulnerability": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "emanation", "size_ft": 10, "width_ft": None}},
        "resolution": "auto_apply",
        "buffs": [{"kind": "block_spells_up_to_level", "value": 5, "duration": "concentration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["barrier_excludes_external_spells_level_5_or_lower"],
    },
    "revivify": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": None, "flat": 1, "add_spell_mod": False},
        "coverage": "partial",
        "unmodeled_clauses": ["revives_creature_dead_within_1_minute"],
    },
    "gaseous-form": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "buffs": [{"kind": "damage_resistance", "types": ["bludgeoning", "piercing", "slashing"], "duration": "concentration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["misty_cloud_movement_and_action_restrictions"],
    },
    "silence": {
        "targeting": {"mode": "aoe", "max_targets": None, "aoe": {"shape": "sphere", "size_ft": 20, "width_ft": None}},
        "resolution": "auto_apply",
        "buffs": [{"kind": "damage_immunity", "types": ["thunder"], "duration": "concentration"}],
        "coverage": "partial",
        "unmodeled_clauses": ["blocks_verbal_components_and_sound_in_sphere"],
    },
    "mass-cure-wounds": {
        "targeting": {"mode": "aoe", "max_targets": 6, "aoe": {"shape": "sphere", "size_ft": 30, "width_ft": None}},
        "resolution": "auto_apply",
        "healing": {"dice": "5d8", "flat": 0, "add_spell_mod": True},
        "scaling": {"slot_healing_per_level": "1d8"},
        "coverage": "full",
    },
    "mass-healing-word": {
        "targeting": {"mode": "multi_target", "max_targets": 6, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": "2d4", "flat": 0, "add_spell_mod": True},
        "scaling": {"slot_healing_per_level": "1d4"},
        "coverage": "full",
    },
    "prayer-of-healing": {
        "targeting": {"mode": "multi_target", "max_targets": 5, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": "2d8", "flat": 0, "add_spell_mod": False},
        "coverage": "partial",
        "unmodeled_clauses": ["short_rest_benefits_once_per_long_rest"],
    },
    "blindness-deafness": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "save",
        "save": {"ability": "con", "on_success": "none"},
        "conditions_applied": [
            {"condition": "blinded", "trigger": "on_fail", "duration": "save_ends", "repeat_save_ability": "con"}
        ],
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "partial",
        "unmodeled_clauses": ["caster_choice_blinded_or_deafened"],
    },
    "aid": {
        "targeting": {"mode": "multi_target", "max_targets": 3, "aoe": None},
        "resolution": "auto_apply",
        "healing": {"dice": None, "flat": 5, "add_spell_mod": False},
        "special_effect": {"kind": "max_hp_bonus", "value": 5, "per_slot_level": 5},
        "coverage": "full",
    },
    "heroism": {
        "targeting": {"mode": "single_target", "max_targets": 1, "aoe": None},
        "resolution": "auto_apply",
        "temp_hp": {"dice": None, "flat": 0, "add_spell_mod": True},
        "scaling": {"slot_targets_per_level": 1},
        "coverage": "partial",
        "unmodeled_clauses": ["frightened_immunity_and_recurring_temp_hp"],
    },
}


# How a later damage clause relates to the one before it (#51).
_ADDITIVE_JOIN_RE = re.compile(r"^\s*,?\s*(?:and|plus)\s*$", re.IGNORECASE)
_DELAYED_AFTER_RE = re.compile(
    r"^\s*(?:immediately\s+)?(?:at\s+the\s+(?:end|start)\s+of|on\s+(?:its|their|your)\s+next\s+turn)",
    re.IGNORECASE,
)
_CHOICE_JOIN_RE = re.compile(r"\bor\b", re.IGNORECASE)


def _resolve_damage_clauses(
    damage_list: list[dict[str, Any]], main_desc: str
) -> tuple[list[dict[str, Any]], bool]:
    """Keep only damage that lands together on the primary effect (#51).

    Returns `(kept, dropped_secondary)`. Each later clause is classified by the prose that joins it to the
    previous kept clause:
    - "X damage and Y damage" (Flame Strike, Ice Storm, Meteor Swarm) is additive, unless Y is delayed
      ("... at the end of its next turn", Acid Arrow), and stays in `damage`.
    - "X damage (if ...) or X' damage" with the same dice (Spirit Guardians, Fire Shield) is a choice of
      damage type. The first type stays; the alternatives go in `type_choices`.
    - Anything else (a delayed tick, a separate save or explosion, another turn's effect) is dropped from
      `damage` and the spell is graded partial with a `secondary_or_delayed_damage_clause`.
    """
    if len(damage_list) <= 1:
        return damage_list, False
    spans: list[tuple[int, int, str, str]] = []
    for rx in (_DAMAGE_MOD_RE, _DAMAGE_RE):
        for m in rx.finditer(main_desc):
            spans.append((m.start(), m.end(), clean_dice_expr(m.group(1)), m.group(2).lower()))
    spans.sort()

    def span_of(d: dict[str, Any]) -> tuple[int, int, str, str] | None:
        return next((sp for sp in spans if (sp[2], sp[3]) == (d["dice"], d["type"])), None)

    kept = [damage_list[0]]
    prev = span_of(damage_list[0])
    dropped = False
    for d in damage_list[1:]:
        sp = span_of(d)
        if prev is None or sp is None or sp[0] < prev[1]:
            dropped = True
            continue
        between = main_desc[prev[1]:sp[0]]
        after = main_desc[sp[1]:sp[1] + 40]
        if _ADDITIVE_JOIN_RE.match(between) and not _DELAYED_AFTER_RE.match(after):
            kept.append(d)
            prev = sp
        elif (
            "." not in between
            and len(between) <= 60
            and _CHOICE_JOIN_RE.search(between)
            and d["dice"] == kept[-1]["dice"]
        ):
            kept[-1].setdefault("type_choices", [kept[-1]["type"]]).append(d["type"])
            prev = sp
        else:
            dropped = True
    return kept, dropped


def _compile_spell(rec: dict[str, Any]) -> dict[str, Any]:
    slug = rec["index"]
    eid = f"spells:{slug}"
    level = int(rec["level"])
    desc = rec.get("description", "")
    desc_norm = normalize_prose(desc)
    higher_level_norm = normalize_prose(rec.get("higher_level") or "")

    action_type, reaction_trigger = _parse_action_type(rec.get("casting_time", "Action"))
    range_raw, range_kind, range_ft = _parse_range(rec.get("range", "Self"))
    duration_raw = rec.get("duration", "Instantaneous")
    duration_rounds = _parse_duration_rounds(duration_raw)
    concentration = bool(rec.get("concentration", False))
    ritual = bool(rec.get("ritual", False))
    classes = sorted(c["index"] for c in rec.get("classes", []))
    school = rec["school"]["index"] if isinstance(rec.get("school"), dict) else ""

    base: dict[str, Any] = {
        "id": eid,
        "slug": slug,
        "name": rec["name"],
        "level": level,
        "school": school,
        "classes": classes,
        "casting_time": rec.get("casting_time", "Action"),
        "action_type": action_type,
        "reaction_trigger": reaction_trigger,
        "range_raw": range_raw,
        "range_kind": range_kind,
        "range_ft": range_ft,
        "components": list(rec.get("components", [])),
        "material": rec.get("material"),
        "duration_raw": duration_raw,
        "duration_rounds": duration_rounds,
        "concentration": concentration,
        "ritual": ritual,
        "targeting": {"mode": "self" if range_kind == "self" else "single_target", "max_targets": 1, "aoe": None},
        "resolution": "utility",
        "attack_kind": None,
        "save": None,
        "damage": [],
        "healing": None,
        "temp_hp": None,
        "conditions_applied": [],
        "conditions_ended": [],
        "buffs": [],
        "special_effect": None,
        "scaling": {
            "cantrip_tiers": None,
            "slot_damage_per_level": None,
            "slot_healing_per_level": None,
            "slot_targets_per_level": 0,
            "slot_projectiles_per_level": 0,
        },
        "coverage": "narrative",
        "provenance": make_provenance(eid, SOURCE_FILE, "regex+structured", []),
    }

    # 1. Curated iconic template path (§3.7)
    if slug in _ICONIC_TEMPLATES:
        tpl = _ICONIC_TEMPLATES[slug]
        for k, v in tpl.items():
            if k == "scaling":
                base["scaling"].update(v)
            elif k not in ("coverage", "unmodeled_clauses"):
                base[k] = v
        cov = tpl["coverage"]
        unmodeled = list(tpl.get("unmodeled_clauses", []))
        base["coverage"] = cov
        base["provenance"] = make_provenance(eid, SOURCE_FILE, "template+structured", unmodeled)
        return base

    # 2. Honest narrative-only spells (divination, illusion, exploration, world-shaping)
    if slug in _TRUE_NARRATIVE_SPELLS:
        base["coverage"] = "narrative"
        base["provenance"] = make_provenance(
            eid,
            SOURCE_FILE,
            "structured",
            ["narrative_or_non_combat_spell_effect"],
        )
        return base

    # 3. General regex+structured extraction
    aoe = parse_aoe(desc_norm)
    if aoe:
        base["targeting"] = {"mode": "aoe", "max_targets": None, "aoe": aoe}
    elif range_kind == "self":
        base["targeting"] = {"mode": "self", "max_targets": 1, "aoe": None}
    else:
        base["targeting"] = {"mode": "single_target", "max_targets": 1, "aoe": None}

    attack_kind = rec.get("attack_type")
    if not attack_kind:
        am = _ATTACK_RE.search(desc_norm)
        if am:
            attack_kind = am.group(1).lower()
    base["attack_kind"] = attack_kind

    save_obj: dict[str, Any] | None = None
    sm = _SAVE_RE.search(desc_norm)
    if sm:
        ab = ABILITY_NAME_TO_SLUG[sm.group(1).lower()]
        on_success = (
            "half"
            if re.search(r"half\s+as\s+much\s+damage|half\s+damage\s+on\s+a\s+successful", desc_norm, re.IGNORECASE)
            else "none"
        )
        save_obj = {"ability": ab, "on_success": on_success}
    base["save"] = save_obj

    # Damage extraction (structured + prose + spellcasting-mod damage)
    damage_list: list[dict[str, Any]] = []
    raw_dmg = rec.get("damage")
    if isinstance(raw_dmg, dict):
        dtype_obj = raw_dmg.get("damage_type") or {}
        dtype = dtype_obj.get("index")
        slot_map = raw_dmg.get("damage_at_slot_level") or {}
        base_dice = slot_map.get(str(level))
        if not base_dice and slot_map:
            first_key = sorted(slot_map.keys(), key=lambda x: int(x) if x.isdigit() else 99)[0]
            base_dice = slot_map[first_key]
        if dtype in DAMAGE_TYPE_SLUGS and base_dice:
            damage_list.append(
                {
                    "dice": clean_dice_expr(str(base_dice)),
                    "type": dtype,
                    "add_spell_mod": False,
                }
            )

    seen_dmg = {(d["dice"], d["type"]) for d in damage_list}
    main_desc = re.split(r"(?:Using a Higher-Level Spell Slot|Cantrip Upgrade)\.", desc_norm, maxsplit=1)[0]

    for dm in _DAMAGE_MOD_RE.finditer(main_desc):
        dice_s = clean_dice_expr(dm.group(1))
        dtype = dm.group(2).lower()
        if dtype in DAMAGE_TYPE_SLUGS and (dice_s, dtype) not in seen_dmg:
            seen_dmg.add((dice_s, dtype))
            damage_list.append({"dice": dice_s, "type": dtype, "add_spell_mod": True})

    for dm in _DAMAGE_RE.finditer(main_desc):
        dice_s = clean_dice_expr(dm.group(1))
        dtype = dm.group(2).lower()
        if dtype in DAMAGE_TYPE_SLUGS and (dice_s, dtype) not in seen_dmg:
            prefix = main_desc[max(0, dm.start() - 35):dm.start()].lower()
            if "increases by" in prefix or ("additional" in prefix and "slot" in prefix):
                continue
            seen_dmg.add((dice_s, dtype))
            damage_list.append({"dice": dice_s, "type": dtype, "add_spell_mod": False})
    damage_list, secondary_damage_dropped = _resolve_damage_clauses(damage_list, main_desc)
    base["damage"] = damage_list

    # Healing
    healing: dict[str, Any] | None = None
    add_mod = "spellcasting ability modifier" in main_desc.lower()
    hm = _HEAL_DICE_RE.search(main_desc)
    if hm:
        healing = {
            "dice": clean_dice_expr(hm.group(1)),
            "flat": int(hm.group(2)) if hm.group(2) else 0,
            "add_spell_mod": add_mod,
        }
    else:
        hfm = _HEAL_FLAT_RE.search(main_desc)
        if hfm:
            healing = {
                "dice": None,
                "flat": int(hfm.group(1)),
                "add_spell_mod": add_mod,
            }
    base["healing"] = healing

    # Temp HP
    temp_hp: dict[str, Any] | None = None
    tm = _TEMP_HP_DICE_RE.search(main_desc)
    if tm:
        temp_hp = {
            "dice": clean_dice_expr(tm.group(1)),
            "flat": int(tm.group(2)) if tm.group(2) else 0,
            "add_spell_mod": add_mod,
        }
    else:
        tfm = _TEMP_HP_FLAT_RE.search(main_desc)
        if tfm:
            temp_hp = {
                "dice": None,
                "flat": int(tfm.group(1)),
                "add_spell_mod": add_mod,
            }
    base["temp_hp"] = temp_hp

    # Conditions
    conds_applied, conds_ended = _extract_conditions(
        main_desc,
        save_obj["ability"] if save_obj else None,
        bool(attack_kind),
        concentration,
    )
    base["conditions_applied"] = conds_applied
    base["conditions_ended"] = conds_ended

    # Scaling
    base_dice_for_cantrip = damage_list[0]["dice"] if damage_list else None
    base["scaling"] = _extract_scaling(level, desc_norm, higher_level_norm, base_dice_for_cantrip)

    # Resolution
    has_mech = bool(damage_list or healing or temp_hp or conds_applied or conds_ended)
    if attack_kind:
        base["resolution"] = "attack"
    elif save_obj:
        base["resolution"] = "save"
    elif healing or temp_hp or conds_ended or conds_applied or damage_list:
        base["resolution"] = "auto_apply"
    else:
        base["resolution"] = "utility"

    if not has_mech and not attack_kind and not save_obj:
        base["coverage"] = "narrative"
        base["provenance"] = make_provenance(
            eid,
            SOURCE_FILE,
            "regex+structured",
            ["narrative_or_non_combat_spell_effect"],
        )
        return base

    unmodeled: list[str] = []
    if slug in _FORCE_PARTIAL_REASON:
        unmodeled.append(_FORCE_PARTIAL_REASON[slug])
    for label, pat in _COMPLEX_CLAUSE_PATTERNS:
        if pat.search(main_desc):
            unmodeled.append(label)

    if secondary_damage_dropped:
        unmodeled.append("secondary_or_delayed_damage_clause")
    if action_type == "long" and not healing:
        unmodeled.append("non_combat_casting_time")
    if save_obj and not damage_list and not conds_applied and not healing:
        unmodeled.append("non_standard_save_effect")
    if concentration and damage_list and not unmodeled:
        unmodeled.append("concentration_sustained_damage_prose")
    if len(main_desc) > 650 and not unmodeled:
        unmodeled.append("extended_secondary_prose_mechanics")

    if not unmodeled and has_mech:
        base["coverage"] = "full"
    elif has_mech or save_obj or attack_kind:
        if not unmodeled:
            unmodeled.append("secondary_prose_mechanics")
        base["coverage"] = "partial"
    else:
        base["coverage"] = "narrative"
        unmodeled.append("narrative_or_non_combat_spell_effect")

    base["provenance"] = make_provenance(eid, SOURCE_FILE, "regex+structured", unmodeled)
    return base


def compile_spells(en_dir: Path) -> dict[str, dict[str, Any]]:
    """Compile all 339 SRD 2024 spells into typed IR."""
    records = load_raw_json(en_dir, SOURCE_FILE)
    compiled: dict[str, dict[str, Any]] = {}
    for rec in sorted(records, key=lambda r: r["index"]):
        compiled[rec["index"]] = _compile_spell(rec)
    return compiled
