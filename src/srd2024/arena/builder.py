"""Custom Party & Encounter Builder engine for local `bin/srd-web` (M6.4).

Implements `docs/M6-DUNGEON-SPEC.md` §4:
- `#21` security & size caps (1-6 PCs L1-10, 1-12 enemies/room, <=18 combatants/room,
  1 or 3 rooms, 20-120 ft 5-ft aligned maps, MAX_WALLS=200, MAX_DIFFICULT=200,
  MAX_COVER=100, offline-only browser driver allowlist).
- Exact 5e 2024 (SRD 5.2) per-PC XP budget table (levels 1-10, no 2014 multiplier).
- Full 341 SRD monster catalog search/filter with pagination.
- Level-parameterized PC preset generator `build_pc_preset(archetype, level)` for
  all 12 SRD classes across levels 1-10 (every preset compiles with 0 errors & 0 warnings).
- Built-in + 3 parameterizable map presets (`open-hall`, `pillared-crypt`, `chokepoint-bridge`)
  and deterministic auto-placement skipping walls & occupied squares.

Strictly Python 3.11+ stdlib.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from srd2024.engine.character import compile_character
from srd2024.engine.combat import in_bounds, is_wall, validate_bounds, validate_terrain
from srd2024.engine.store import RulesStore, strip_prefix

from .scenarios import BUILTIN_SCENARIO_IDS, _BUILTIN_SCENARIOS

# ----------------------------------------------------------------------
# Security & Size Caps (#21)
# ----------------------------------------------------------------------

MIN_PARTY_SIZE = 1
MAX_PARTY_SIZE = 6
MIN_PC_LEVEL = 1
MAX_PC_LEVEL = 10
MIN_ENEMIES_PER_ROOM = 1
MAX_ENEMIES_PER_ROOM = 12
MAX_COMBATANTS_PER_ENCOUNTER = 18
MAX_DUNGEON_ROOMS = 3
MIN_MAP_FT = 20
MAX_MAP_FT = 120
MAX_WALLS = 200
MAX_DIFFICULT = 200
MAX_COVER = 100
MAX_BUILDER_BODY_BYTES = 64 * 1024
MAX_MONSTER_PAGE_LIMIT = 100
DEFAULT_MONSTER_PAGE_LIMIT = 50

ALLOWED_BROWSER_DRIVERS: frozenset[str] = frozenset(
    {"heuristic-tactical", "greedy", "random"}
)

LIMITS: dict[str, int] = {
    "min_party_size": MIN_PARTY_SIZE,
    "max_party_size": MAX_PARTY_SIZE,
    "min_pc_level": MIN_PC_LEVEL,
    "max_pc_level": MAX_PC_LEVEL,
    "min_enemies_per_room": MIN_ENEMIES_PER_ROOM,
    "max_enemies_per_room": MAX_ENEMIES_PER_ROOM,
    "max_combatants_per_encounter": MAX_COMBATANTS_PER_ENCOUNTER,
    "max_dungeon_rooms": MAX_DUNGEON_ROOMS,
    "min_map_ft": MIN_MAP_FT,
    "max_map_ft": MAX_MAP_FT,
    "max_walls": MAX_WALLS,
    "max_difficult": MAX_DIFFICULT,
    "max_cover": MAX_COVER,
}

# ----------------------------------------------------------------------
# Exact 5e 2024 (SRD 5.2) Per-PC XP Budget Table (Levels 1-10)
# ----------------------------------------------------------------------

XP_THRESHOLDS_2024: dict[int, dict[str, int]] = {
    1: {"low": 50, "moderate": 75, "high": 100},
    2: {"low": 100, "moderate": 150, "high": 200},
    3: {"low": 150, "moderate": 225, "high": 400},
    4: {"low": 250, "moderate": 375, "high": 500},
    5: {"low": 500, "moderate": 750, "high": 1100},
    6: {"low": 600, "moderate": 1000, "high": 1400},
    7: {"low": 750, "moderate": 1300, "high": 1700},
    8: {"low": 1000, "moderate": 1700, "high": 2100},
    9: {"low": 1300, "moderate": 2000, "high": 2600},
    10: {"low": 1600, "moderate": 2300, "high": 3100},
}

_ID_SANITIZE_RE = re.compile(r"[^a-z0-9-]+")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _slugify_id(raw: str, fallback: str) -> str:
    cleaned = _ID_SANITIZE_RE.sub("-", str(raw or "").strip().lower()).strip("-")
    return cleaned[:48] if cleaned else fallback


# ----------------------------------------------------------------------
# 2024 Encounter XP Budget Calculation
# ----------------------------------------------------------------------


def compute_encounter_budget(
    party_levels: list[int],
    monsters: list[str],
    rules: RulesStore,
) -> dict[str, Any]:
    """Compute 5e 2024 (SRD 5.2) encounter XP budget & difficulty (no 2014 multiplier)."""
    if not isinstance(party_levels, list) or not (MIN_PARTY_SIZE <= len(party_levels) <= MAX_PARTY_SIZE):
        raise ValueError(
            f"'party_levels' must contain between {MIN_PARTY_SIZE} and {MAX_PARTY_SIZE} integer levels."
        )
    levels: list[int] = []
    for lvl in party_levels:
        if not _is_int(lvl) or not (MIN_PC_LEVEL <= lvl <= MAX_PC_LEVEL):
            raise ValueError(
                f"Every PC level must be an integer between {MIN_PC_LEVEL} and {MAX_PC_LEVEL} (got {lvl!r})."
            )
        levels.append(int(lvl))

    if not isinstance(monsters, list):
        raise ValueError("'monsters' must be a list of monster slugs.")
    if len(monsters) > MAX_ENEMIES_PER_ROOM:
        raise ValueError(
            f"At most {MAX_ENEMIES_PER_ROOM} enemies are allowed per room (got {len(monsters)})."
        )

    low = sum(XP_THRESHOLDS_2024[lvl]["low"] for lvl in levels)
    moderate = sum(XP_THRESHOLDS_2024[lvl]["moderate"] for lvl in levels)
    high = sum(XP_THRESHOLDS_2024[lvl]["high"] for lvl in levels)

    encounter_xp = 0
    warnings: list[str] = []
    partial_names: list[str] = []
    for raw_slug in monsters:
        if not isinstance(raw_slug, str) or not raw_slug.strip():
            raise ValueError(f"Monster slug must be a non-empty string (got {raw_slug!r}).")
        slug = strip_prefix(raw_slug)
        rec = rules.get_monster(slug)
        if rec is None:
            raise ValueError(f"Unknown monster slug '{raw_slug}'.")
        encounter_xp += int(rec.get("xp") or 0)
        cov = str(rec.get("coverage") or "partial")
        if cov != "full":
            m_name = str(rec.get("name") or slug)
            if m_name not in partial_names:
                partial_names.append(m_name)

    if encounter_xp < low:
        difficulty = "trivial"
    elif encounter_xp < moderate:
        difficulty = "low"
    elif encounter_xp < high:
        difficulty = "moderate"
    elif encounter_xp <= high:
        difficulty = "high"
    else:
        difficulty = "deadly"

    combatant_count = len(levels) + len(monsters)
    if combatant_count > MAX_COMBATANTS_PER_ENCOUNTER:
        warnings.append(
            f"Total combatants ({combatant_count}) exceeds the {MAX_COMBATANTS_PER_ENCOUNTER}-combatant cap."
        )
    if partial_names:
        shown = ", ".join(partial_names[:5])
        extra = f" (+{len(partial_names) - 5} more)" if len(partial_names) > 5 else ""
        warnings.append(f"Partial-coverage monsters with unmodeled clauses: {shown}{extra}.")

    return {
        "party_xp_thresholds": {"low": low, "moderate": moderate, "high": high},
        "encounter_xp": encounter_xp,
        "difficulty": difficulty,
        "combatant_count": combatant_count,
        "warnings": warnings,
    }


# ----------------------------------------------------------------------
# 341 SRD Monster Catalog Search & Filter
# ----------------------------------------------------------------------


def _monster_speed_ft(rec: dict[str, Any]) -> int:
    sp = rec.get("speed")
    if isinstance(sp, dict):
        return int(sp.get("walk_ft") or sp.get("fly_ft") or sp.get("swim_ft") or 0)
    if isinstance(sp, (int, float)):
        return int(sp)
    return 0


def _monster_actions_summary(rec: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for act in rec.get("actions") or []:
        if isinstance(act, dict) and act.get("name"):
            names.append(str(act["name"]))
    return names[:6]


def _monster_catalog_row(slug: str, rec: dict[str, Any]) -> dict[str, Any]:
    prov = rec.get("provenance") if isinstance(rec.get("provenance"), dict) else {}
    unmodeled = [str(u) for u in (prov.get("unmodeled_clauses") or [])]
    cr_raw = rec.get("cr", 0)
    try:
        cr_val = float(cr_raw)
    except (TypeError, ValueError):
        cr_val = 0.0
    return {
        "slug": slug,
        "name": str(rec.get("name") or slug),
        "cr": cr_val,
        "xp": int(rec.get("xp") or 0),
        "ac": int(rec.get("ac") or 10),
        "hp_max": int(rec.get("hp_max") or 1),
        "size": str(rec.get("size") or "Medium"),
        "creature_type": str(rec.get("creature_type") or "unknown").lower(),
        "coverage": str(rec.get("coverage") or "partial"),
        "unmodeled_clauses": unmodeled,
        "speed_ft": _monster_speed_ft(rec),
        "actions_summary": _monster_actions_summary(rec),
    }


def search_monsters(
    rules: RulesStore,
    *,
    cr_min: float | None = None,
    cr_max: float | None = None,
    creature_type: str | None = None,
    coverage: str | None = None,
    q: str | None = None,
    limit: int = DEFAULT_MONSTER_PAGE_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Filter and paginate all 341 SRD monsters deterministically."""
    if not _is_int(limit) or not (1 <= limit <= MAX_MONSTER_PAGE_LIMIT):
        raise ValueError(f"'limit' must be an integer between 1 and {MAX_MONSTER_PAGE_LIMIT}.")
    if not _is_int(offset) or offset < 0:
        raise ValueError("'offset' must be a non-negative integer.")
    if cr_min is not None and cr_max is not None and cr_min > cr_max:
        raise ValueError("'cr_min' cannot be greater than 'cr_max'.")

    cov_filter = (coverage or "").strip().lower()
    if cov_filter in ("", "all"):
        cov_filter = ""
    elif cov_filter not in ("full", "partial", "narrative"):
        raise ValueError("'coverage' must be one of 'all', 'full', 'partial', or 'narrative'.")

    type_filter = (creature_type or "").strip().lower()
    q_filter = (q or "").strip().lower()

    raw_monsters = rules._data.get("monsters") or {}
    rows: list[dict[str, Any]] = []
    for slug, rec in raw_monsters.items():
        if not isinstance(rec, dict):
            continue
        row = _monster_catalog_row(slug, rec)
        if cr_min is not None and row["cr"] < cr_min:
            continue
        if cr_max is not None and row["cr"] > cr_max:
            continue
        if type_filter and row["creature_type"] != type_filter:
            continue
        if cov_filter and row["coverage"] != cov_filter:
            continue
        if q_filter and q_filter not in row["name"].lower() and q_filter not in row["slug"].lower():
            continue
        rows.append(row)

    rows.sort(key=lambda r: (r["cr"], r["name"].lower(), r["slug"]))
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": page,
    }


# ----------------------------------------------------------------------
# Level-Parameterized PC Preset Generator (Levels 1-10, All 12 SRD Classes)
# ----------------------------------------------------------------------

CORE_ARCHETYPES: tuple[str, ...] = (
    "fighter",
    "rogue",
    "paladin",
    "cleric-life",
    "wizard-evoker",
)

ALL_ARCHETYPES: tuple[str, ...] = (
    "fighter",
    "rogue",
    "paladin",
    "cleric-life",
    "wizard-evoker",
    "barbarian",
    "bard",
    "druid",
    "monk",
    "ranger",
    "sorcerer",
    "warlock",
)

_ARCHETYPE_ALIASES: dict[str, str] = {
    "cleric": "cleric-life",
    "wizard": "wizard-evoker",
}

_ARCHETYPE_DEFS: dict[str, dict[str, Any]] = {
    "fighter": {
        "label": "Fighter (Champion)",
        "name": "Aldric Vance",
        "class": "fighter",
        "subclass": "champion",
        "species": "human",
        "subspecies": None,
        "size": "Medium",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
        "asi_order": ["str", "con", "dex"],
        "fighting_style": ("defense", 1),
        "skill_proficiencies": ["athletics", "intimidation", "perception", "survival", "acrobatics"],
        "equipment": {"armor": "chain-mail", "shield": True, "weapons": ["longsword", "greatsword", "javelin", "shortbow"]},
        "mastery_pool": ["longsword", "greatsword", "javelin", "shortbow", "battleaxe", "warhammer"],
        "cantrip_pool": [],
        "spell_pool": [],
    },
    "rogue": {
        "label": "Rogue (Thief)",
        "name": "Kaelen Whisper",
        "class": "rogue",
        "subclass": "thief",
        "species": "halfling",
        "subspecies": None,
        "size": "Small",
        "background": "criminal",
        "background_asi": {"dex": 2, "con": 1},
        "origin_feat": "alert",
        "base_abilities": {"str": 8, "dex": 15, "con": 14, "int": 12, "wis": 13, "cha": 10},
        "asi_order": ["dex", "con", "wis"],
        "fighting_style": None,
        "skill_proficiencies": ["stealth", "sleight-of-hand", "acrobatics", "perception", "investigation", "deception"],
        "equipment": {"armor": "studded-leather-armor", "shield": False, "weapons": ["shortsword", "dagger", "shortbow"]},
        "mastery_pool": ["shortsword", "dagger", "shortbow"],
        "cantrip_pool": [],
        "spell_pool": [],
    },
    "paladin": {
        "label": "Paladin (Oath of Devotion)",
        "name": "Seren Dawnblade",
        "class": "paladin",
        "subclass": "oath-of-devotion",
        "species": "human",
        "subspecies": None,
        "size": "Medium",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "extra_origin_feat": "alert",
        "base_abilities": {"str": 15, "dex": 10, "con": 14, "int": 8, "wis": 10, "cha": 14},
        "asi_order": ["str", "cha", "con"],
        "fighting_style": ("defense", 2),
        "skill_proficiencies": ["athletics", "intimidation", "insight", "persuasion", "religion"],
        "equipment": {"armor": "chain-mail", "shield": True, "weapons": ["longsword", "javelin"]},
        "mastery_pool": ["longsword", "javelin"],
        "cantrip_pool": [],
        "spell_pool": [
            "divine-smite", "bless", "cure-wounds", "shield-of-faith", "command",
            "aid", "shining-smite", "lesser-restoration", "revivify", "dispel-magic",
            "aura-of-life", "death-ward", "banishment",
        ],
    },
    "cleric-life": {
        "label": "Cleric (Life Domain)",
        "name": "Dorian Dawnshield",
        "class": "cleric",
        "subclass": "life-domain",
        "species": "dwarf",
        "subspecies": None,
        "size": "Medium",
        "background": "acolyte",
        "background_asi": {"wis": 2, "cha": 1},
        "origin_feat": "magic-initiate",
        "base_abilities": {"str": 13, "dex": 10, "con": 14, "int": 8, "wis": 15, "cha": 12},
        "asi_order": ["wis", "con", "str"],
        "fighting_style": None,
        "skill_proficiencies": ["insight", "religion", "medicine", "persuasion"],
        "equipment": {"armor": "scale-mail", "shield": True, "weapons": ["mace"]},
        "mastery_pool": [],
        "cantrip_pool": ["sacred-flame", "guidance", "light", "resistance", "spare-the-dying", "thaumaturgy"],
        "spell_pool": [
            "bless", "cure-wounds", "healing-word", "guiding-bolt", "shield-of-faith",
            "command", "aid", "hold-person", "spiritual-weapon", "mass-healing-word",
            "spirit-guardians", "revivify", "dispel-magic", "banishment", "guardian-of-faith",
            "mass-cure-wounds", "flame-strike", "greater-restoration",
        ],
    },
    "wizard-evoker": {
        "label": "Wizard (Evoker)",
        "name": "Lyra Starweave",
        "class": "wizard",
        "subclass": "evoker",
        "species": "elf",
        "subspecies": "elven-lineage-high-elf",
        "size": "Medium",
        "background": "sage",
        "background_asi": {"int": 2, "con": 1},
        "origin_feat": "magic-initiate",
        "base_abilities": {"str": 8, "dex": 13, "con": 14, "int": 15, "wis": 12, "cha": 10},
        "asi_order": ["int", "con", "dex"],
        "fighting_style": None,
        "skill_proficiencies": ["arcana", "history", "investigation", "insight"],
        "equipment": {"armor": None, "shield": False, "weapons": ["quarterstaff", "dagger"]},
        "mastery_pool": [],
        "cantrip_pool": ["fire-bolt", "ray-of-frost", "acid-splash", "chill-touch", "mage-hand", "minor-illusion"],
        "spell_pool": [
            "shield", "magic-missile", "mage-armor", "burning-hands", "sleep",
            "scorching-ray", "shatter", "hold-person", "web", "invisibility",
            "fireball", "lightning-bolt", "counterspell", "fly", "haste",
            "ice-storm", "banishment", "dimension-door", "cone-of-cold", "wall-of-force",
        ],
    },
    "barbarian": {
        "label": "Barbarian (Berserker)",
        "name": "Torin Ironhide",
        "class": "barbarian",
        "subclass": "path-of-the-berserker",
        "species": "orc",
        "subspecies": None,
        "size": "Medium",
        "background": "soldier",
        "background_asi": {"str": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "base_abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 12, "cha": 8},
        "asi_order": ["str", "con", "dex"],
        "fighting_style": None,
        "skill_proficiencies": ["athletics", "intimidation", "perception", "survival"],
        "equipment": {"armor": None, "shield": False, "weapons": ["greataxe", "handaxe", "javelin"]},
        "mastery_pool": ["greataxe", "handaxe", "javelin", "greatsword"],
        "cantrip_pool": [],
        "spell_pool": [],
    },
    "bard": {
        "label": "Bard (College of Lore)",
        "name": "Vesper Lute",
        "class": "bard",
        "subclass": "college-of-lore",
        "species": "human",
        "subspecies": None,
        "size": "Medium",
        "background": "acolyte",
        "background_asi": {"cha": 2, "wis": 1},
        "origin_feat": "magic-initiate",
        "extra_origin_feat": "alert",
        "base_abilities": {"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 15},
        "asi_order": ["cha", "dex", "con"],
        "fighting_style": None,
        "skill_proficiencies": ["insight", "religion", "performance", "persuasion", "deception", "acrobatics"],
        "equipment": {"armor": "leather-armor", "shield": False, "weapons": ["dagger", "shortbow"]},
        "mastery_pool": [],
        "cantrip_pool": ["vicious-mockery", "minor-illusion", "mage-hand", "light", "message"],
        "spell_pool": [
            "healing-word", "faerie-fire", "thunderwave", "bane", "dissonant-whispers",
            "shatter", "hold-person", "invisibility", "aid", "hypnotic-pattern",
            "dispel-magic", "fear", "dimension-door", "polymorph", "synaptic-static",
            "mass-cure-wounds", "hold-monster",
        ],
    },
    "druid": {
        "label": "Druid (Circle of the Land)",
        "name": "Rowan Moss",
        "class": "druid",
        "subclass": "circle-of-the-land",
        "species": "elf",
        "subspecies": "elven-lineage-wood-elf",
        "size": "Medium",
        "background": "sage",
        "background_asi": {"wis": 2, "con": 1},
        "origin_feat": "magic-initiate",
        "base_abilities": {"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 15, "cha": 10},
        "asi_order": ["wis", "con", "dex"],
        "fighting_style": None,
        "skill_proficiencies": ["arcana", "history", "nature", "perception"],
        "equipment": {"armor": "leather-armor", "shield": True, "weapons": ["quarterstaff", "dagger"]},
        "mastery_pool": [],
        "cantrip_pool": ["produce-flame", "guidance", "shillelagh", "poison-spray", "druidcraft"],
        "spell_pool": [
            "entangle", "healing-word", "cure-wounds", "faerie-fire", "thunderwave",
            "moonbeam", "hold-person", "barkskin", "spike-growth", "call-lightning",
            "conjure-animals", "dispel-magic", "blight", "ice-storm", "polymorph",
            "cone-of-cold", "mass-cure-wounds", "wall-of-stone",
        ],
    },
    "monk": {
        "label": "Monk (Warrior of the Open Hand)",
        "name": "Kira Stillwater",
        "class": "monk",
        "subclass": "warrior-of-the-open-hand",
        "species": "human",
        "subspecies": None,
        "size": "Medium",
        "background": "criminal",
        "background_asi": {"dex": 2, "con": 1},
        "origin_feat": "alert",
        "extra_origin_feat": "savage-attacker",
        "base_abilities": {"str": 8, "dex": 15, "con": 14, "int": 8, "wis": 15, "cha": 10},
        "asi_order": ["dex", "wis", "con"],
        "fighting_style": None,
        "skill_proficiencies": ["sleight-of-hand", "stealth", "acrobatics", "insight", "perception"],
        "equipment": {"armor": None, "shield": False, "weapons": ["shortsword", "dagger", "dart"]},
        "mastery_pool": [],
        "cantrip_pool": [],
        "spell_pool": [],
    },
    "ranger": {
        "label": "Ranger (Hunter)",
        "name": "Faelen Briar",
        "class": "ranger",
        "subclass": "hunter",
        "species": "elf",
        "subspecies": "elven-lineage-wood-elf",
        "size": "Medium",
        "background": "soldier",
        "background_asi": {"dex": 2, "con": 1},
        "origin_feat": "savage-attacker",
        "base_abilities": {"str": 10, "dex": 15, "con": 14, "int": 8, "wis": 14, "cha": 10},
        "asi_order": ["dex", "wis", "con"],
        "fighting_style": ("archery", 2),
        "skill_proficiencies": ["athletics", "intimidation", "perception", "stealth", "survival"],
        "equipment": {"armor": "scale-mail", "shield": False, "weapons": ["longbow", "shortsword", "dagger"]},
        "mastery_pool": ["longbow", "shortsword"],
        "cantrip_pool": [],
        "spell_pool": [
            "hunters-mark", "cure-wounds", "entangle", "fog-cloud", "goodberry",
            "spike-growth", "barkskin", "aid", "conjure-animals",
            "lightning-arrow", "dispel-magic",
        ],
    },
    "sorcerer": {
        "label": "Sorcerer (Draconic Sorcery)",
        "name": "Cinder Vey",
        "class": "sorcerer",
        "subclass": "draconic-sorcery",
        "species": "dragonborn",
        "subspecies": "draconic-ancestor-red",
        "size": "Medium",
        "background": "acolyte",
        "background_asi": {"cha": 2, "wis": 1},
        "origin_feat": "magic-initiate",
        "base_abilities": {"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 15},
        "asi_order": ["cha", "con", "dex"],
        "fighting_style": None,
        "skill_proficiencies": ["insight", "religion", "arcana", "persuasion"],
        "equipment": {"armor": None, "shield": False, "weapons": ["dagger", "light-crossbow"]},
        "mastery_pool": [],
        "cantrip_pool": ["fire-bolt", "ray-of-frost", "acid-splash", "chill-touch", "light", "mage-hand", "minor-illusion"],
        "spell_pool": [
            "shield", "magic-missile", "burning-hands", "chromatic-orb", "sleep",
            "misty-step", "scorching-ray", "shatter", "hold-person", "web",
            "fireball", "lightning-bolt", "counterspell", "haste", "banishment",
            "ice-storm", "cone-of-cold", "hold-monster",
        ],
    },
    "warlock": {
        "label": "Warlock (Fiend Patron)",
        "name": "Malakor Ash",
        "class": "warlock",
        "subclass": "fiend-patron",
        "species": "tiefling",
        "subspecies": "fiendish-legacy-infernal",
        "size": "Medium",
        "background": "acolyte",
        "background_asi": {"cha": 2, "wis": 1},
        "origin_feat": "magic-initiate",
        "base_abilities": {"str": 8, "dex": 14, "con": 14, "int": 10, "wis": 10, "cha": 15},
        "asi_order": ["cha", "con", "dex"],
        "fighting_style": None,
        "skill_proficiencies": ["insight", "religion", "arcana", "intimidation"],
        "equipment": {"armor": "leather-armor", "shield": False, "weapons": ["dagger", "light-crossbow"]},
        "mastery_pool": [],
        "cantrip_pool": ["eldritch-blast", "chill-touch", "minor-illusion", "mage-hand", "prestidigitation"],
        "spell_pool": [
            "hex", "bane", "charm-person", "expeditious-retreat", "armor-of-agathys",
            "misty-step", "hold-person", "shatter", "invisibility", "spider-climb",
            "counterspell", "fear", "fly", "banishment", "dimension-door",
            "hold-monster",
        ],
    },
}


def _class_asi_levels(class_slug: str, rules: RulesStore) -> list[int]:
    cc = rules._data.get("character_creation") or {}
    c_rec = (cc.get("classes") or {}).get(class_slug) or {}
    raw = c_rec.get("asi_levels")
    if isinstance(raw, list) and raw:
        return sorted(int(x) for x in raw)
    if class_slug == "fighter":
        return [4, 6, 8, 12, 14, 16]
    if class_slug == "rogue":
        return [4, 8, 10, 12, 16]
    return [4, 8, 12, 16]


def _weapon_mastery_count(class_slug: str, level: int, rules: RulesStore) -> int:
    cc = rules._data.get("character_creation") or {}
    c_rec = (cc.get("classes") or {}).get(class_slug) or {}
    prog = c_rec.get("weapon_mastery_progression")
    if isinstance(prog, dict):
        val = prog.get(str(level), prog.get(level))
        if val is not None:
            return int(val)
    if class_slug == "fighter":
        return 5 if level >= 10 else (4 if level >= 4 else 3)
    if class_slug == "barbarian":
        return 4 if level >= 10 else (3 if level >= 4 else 2)
    if class_slug in ("paladin", "ranger", "rogue"):
        return 2
    return 0


def _max_slot_level_for(class_slug: str, level: int, rules: RulesStore) -> int:
    cc = rules._data.get("character_creation") or {}
    lvl_rec = (cc.get("levels") or {}).get(f"{class_slug}-{level}") or {}
    sc = lvl_rec.get("spellcasting") or {}
    if not isinstance(sc, dict):
        return 0
    max_s = 0
    if isinstance(sc.get("slots"), dict):
        for k, v in sc["slots"].items():
            if int(v or 0) > 0:
                max_s = max(max_s, int(k))
    for s_lvl in range(1, 10):
        if int(sc.get(f"spell_slots_level_{s_lvl}") or 0) > 0:
            max_s = max(max_s, s_lvl)
    if int(sc.get("slot_level") or 0) > 0:
        max_s = max(max_s, int(sc["slot_level"]))
    return max_s


def _pick_spells(
    class_slug: str,
    level: int,
    cantrip_pool: list[str],
    spell_pool: list[str],
    rules: RulesStore,
) -> tuple[list[str], list[str]]:
    cc = rules._data.get("character_creation") or {}
    lvl_rec = (cc.get("levels") or {}).get(f"{class_slug}-{level}") or {}
    sc = lvl_rec.get("spellcasting") if isinstance(lvl_rec.get("spellcasting"), dict) else {}
    n_cantrips = int(sc.get("cantrips_known") or 0)
    n_prepared = int(sc.get("prepared_spells") or 0)
    max_slot = _max_slot_level_for(class_slug, level, rules)
    spells_db = rules._data.get("spells") or {}

    def _eligible(slug: str, target_level: int | None) -> bool:
        rec = spells_db.get(slug)
        if not isinstance(rec, dict):
            return False
        classes = [strip_prefix(c) for c in (rec.get("classes") or [])]
        if class_slug not in classes:
            return False
        sl = int(rec.get("level") or 0)
        if target_level == 0:
            return sl == 0
        return 1 <= sl <= max_slot

    cantrips: list[str] = []
    if n_cantrips > 0:
        for s in cantrip_pool:
            if len(cantrips) >= n_cantrips:
                break
            if s not in cantrips and _eligible(s, 0):
                cantrips.append(s)
        if len(cantrips) < n_cantrips:
            for s in sorted(spells_db):
                if len(cantrips) >= n_cantrips:
                    break
                if s not in cantrips and _eligible(s, 0):
                    cantrips.append(s)

    prepared: list[str] = []
    if n_prepared > 0 and max_slot > 0:
        for s in spell_pool:
            if len(prepared) >= n_prepared:
                break
            if s not in prepared and _eligible(s, None):
                prepared.append(s)
        if len(prepared) < n_prepared:
            for s in sorted(spells_db):
                if len(prepared) >= n_prepared:
                    break
                if s not in prepared and _eligible(s, None):
                    prepared.append(s)

    return cantrips, prepared


def build_pc_preset(
    archetype: str,
    level: int,
    rules: RulesStore | None = None,
) -> dict[str, Any]:
    """Build a valid 5e 2024 CharacterBuildSpec for any SRD class at level 1-10."""
    if not _is_int(level) or not (MIN_PC_LEVEL <= level <= MAX_PC_LEVEL):
        raise ValueError(f"Preset level must be an integer in {MIN_PC_LEVEL}..{MAX_PC_LEVEL} (got {level!r}).")
    key = str(archetype or "").strip().lower()
    key = _ARCHETYPE_ALIASES.get(key, key)
    if key not in _ARCHETYPE_DEFS:
        raise ValueError(f"Unknown PC archetype '{archetype}'. Expected one of: {', '.join(ALL_ARCHETYPES)}.")

    store = rules if rules is not None else RulesStore()
    defn = _ARCHETYPE_DEFS[key]
    class_slug = defn["class"]

    # Compute running ability scores to keep ASI increases <= 20.
    scores = dict(defn["base_abilities"])
    for ab, inc in defn["background_asi"].items():
        scores[ab] = scores.get(ab, 10) + int(inc)

    asi_choices: list[dict[str, Any]] = []
    for asi_lvl in _class_asi_levels(class_slug, store):
        if asi_lvl > level:
            break
        remaining = 2
        increases: dict[str, int] = {}
        for ab in defn["asi_order"]:
            if remaining <= 0:
                break
            headroom = 20 - scores.get(ab, 10)
            if headroom <= 0:
                continue
            add = min(remaining, headroom)
            scores[ab] = scores.get(ab, 10) + add
            increases[ab] = add
            remaining -= add
        if increases:
            asi_choices.append({"level": asi_lvl, "type": "asi", "increases": increases})

    cantrips, prepared = _pick_spells(
        class_slug,
        level,
        defn["cantrip_pool"],
        defn["spell_pool"],
        store,
    )

    mastery_n = _weapon_mastery_count(class_slug, level, store)
    masteries = list(defn["mastery_pool"][:mastery_n]) if mastery_n > 0 else []

    spec: dict[str, Any] = {
        "name": defn["name"],
        "species": defn["species"],
        "subspecies": defn["subspecies"],
        "size": defn["size"],
        "background": defn["background"],
        "background_asi": dict(defn["background_asi"]),
        "origin_feat": defn["origin_feat"],
        "class": class_slug,
        "subclass": defn["subclass"] if level >= 3 else None,
        "level": int(level),
        "ability_method": "point_buy",
        "base_abilities": dict(defn["base_abilities"]),
        "asi_choices": asi_choices,
        "skill_proficiencies": list(defn["skill_proficiencies"]),
        "equipment": copy.deepcopy(defn["equipment"]),
        "cantrips": cantrips,
        "prepared_spells": prepared,
        "hp_method": "fixed",
    }
    if defn.get("extra_origin_feat"):
        spec["extra_origin_feat"] = defn["extra_origin_feat"]
    if masteries:
        spec["weapon_mastery_weapons"] = masteries
    fs = defn.get("fighting_style")
    if fs is not None and level >= fs[1]:
        spec["fighting_style"] = fs[0]
    return spec


def validate_pc_spec(spec: dict[str, Any], rules: RulesStore) -> dict[str, Any]:
    """Compile & validate one CharacterBuildSpec with M6.4 L1-10 bounds."""
    if not isinstance(spec, dict):
        return {
            "valid": False,
            "errors": [{"code": "invalid_spec", "path": "$", "message": "PC spec must be a JSON object."}],
            "warnings": [],
            "statblock_summary": None,
        }
    raw_lvl = spec.get("level", 1)
    lvl_err = None
    if not _is_int(raw_lvl) or not (MIN_PC_LEVEL <= raw_lvl <= MAX_PC_LEVEL):
        lvl_err = {
            "code": "level_out_of_range",
            "path": "level",
            "message": f"Builder PC level must be an integer between {MIN_PC_LEVEL} and {MAX_PC_LEVEL} (got {raw_lvl!r}).",
        }
    compiled = compile_character(spec, rules)
    errors = list(compiled.get("errors") or [])
    if lvl_err is not None and not any(e.get("path") == "level" for e in errors):
        errors.insert(0, lvl_err)
    elif lvl_err is not None:
        errors.append(lvl_err)
    warnings = list(compiled.get("warnings") or [])
    valid = bool(compiled.get("valid")) and lvl_err is None
    sb = compiled.get("statblock") if valid else None
    summary = None
    if isinstance(sb, dict):
        sc = sb.get("spellcasting") if isinstance(sb.get("spellcasting"), dict) else {}
        summary = {
            "name": str(sb.get("name") or ""),
            "class": strip_prefix(str(sb.get("class") or "")),
            "subclass": strip_prefix(str(sb.get("subclass") or "")) if sb.get("subclass") else None,
            "level": int(sb.get("level") or 1),
            "hp_max": int(sb.get("hp_max") or 1),
            "ac": int(sb.get("ac") or 10),
            "ac_breakdown": str(sb.get("ac_breakdown") or ""),
            "initiative": int(sb.get("initiative") or 0),
            "speed_ft": int(sb.get("speed_ft") or 30),
            "hit_die": int(sb.get("hit_die") or 8),
            "spell_save_dc": sc.get("spell_save_dc"),
            "slots": dict(sc.get("slots") or {}),
            "class_resources_max": copy.deepcopy(sb.get("class_resources_max") or {}),
            "weapons": [
                {
                    "name": str(w.get("name") or w.get("slug") or ""),
                    "slug": str(w.get("slug") or ""),
                    "attack_bonus": int(w.get("attack_bonus") or 0),
                    "damage_dice": str(w.get("damage_dice") or ""),
                    "damage_type": str(w.get("damage_type") or ""),
                }
                for w in (sb.get("weapons") or [])
                if isinstance(w, dict)
            ],
        }
    return {
        "valid": valid,
        "errors": errors,
        "warnings": warnings,
        "statblock_summary": summary,
    }


# ----------------------------------------------------------------------
# Map & Terrain Presets + Validation + Deterministic Auto-Placement
# ----------------------------------------------------------------------

PARAMETRIC_MAP_PRESETS: tuple[str, ...] = (
    "open-hall",
    "pillared-crypt",
    "chokepoint-bridge",
)


def _validate_map_dimensions(width_ft: int, height_ft: int) -> tuple[int, int]:
    if not (_is_int(width_ft) and _is_int(height_ft)):
        raise ValueError("'width_ft' and 'height_ft' must be integers.")
    if width_ft % 5 or height_ft % 5:
        raise ValueError("'width_ft' and 'height_ft' must be multiples of 5.")
    if not (MIN_MAP_FT <= width_ft <= MAX_MAP_FT) or not (MIN_MAP_FT <= height_ft <= MAX_MAP_FT):
        raise ValueError(
            f"Map dimensions must be between {MIN_MAP_FT} and {MAX_MAP_FT} ft (got {width_ft}x{height_ft})."
        )
    return int(width_ft), int(height_ft)


def _snap5(v: int) -> int:
    return (int(v) // 5) * 5


def build_map_preset(
    preset_id: str,
    *,
    width_ft: int = 50,
    height_ft: int = 35,
) -> dict[str, Any]:
    """Build bounds & terrain for a built-in scenario map or parameterizable room preset."""
    pid = str(preset_id or "").strip().lower()
    if pid in _BUILTIN_SCENARIOS:
        sc = _BUILTIN_SCENARIOS[pid]
        bounds = copy.deepcopy(sc.get("bounds"))
        if bounds is None:
            # Derive clean bounded arena covering all default positions for unbounded scenarios.
            pts = [p["position"] for p in sc["party"]] + [e["position"] for e in sc["enemies"]]
            min_x = min(0, min(p["x"] for p in pts))
            min_y = min(0, min(p["y"] for p in pts))
            max_x = max(min_x + 35, max(p["x"] for p in pts) + 5)
            max_y = max(min_y + 25, max(p["y"] for p in pts) + 5)
            bounds = {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y}
        terrain = copy.deepcopy(sc.get("terrain")) or {
            "walls": [],
            "difficult_terrain": [],
            "cover": [],
        }
        return {
            "id": pid,
            "name": str(sc["name"]),
            "bounds": bounds,
            "terrain": terrain,
        }

    w, h = _validate_map_dimensions(width_ft, height_ft)
    bounds = {"min_x": 0, "min_y": 0, "max_x": w - 5, "max_y": h - 5}
    walls: list[list[int]] = []
    difficult: list[list[int]] = []
    cover: list[dict[str, Any]] = []

    if pid == "open-hall":
        name = f"Open Hall ({w}x{h} ft)"
    elif pid == "pillared-crypt":
        name = f"Pillared Crypt ({w}x{h} ft)"
        cx = _snap5(w // 2)
        cy = _snap5(h // 2)
        qx1 = max(5, _snap5(w // 4))
        qx2 = min(bounds["max_x"] - 5, _snap5((3 * w) // 4))
        qy1 = max(5, _snap5(h // 4))
        qy2 = min(bounds["max_y"] - 5, _snap5((3 * h) // 4))
        seen_cov: set[tuple[int, int]] = set()
        for px in (qx1, qx2):
            for py in (qy1, qy2):
                if (px, py) not in seen_cov and in_bounds({"x": px, "y": py}, bounds):
                    seen_cov.add((px, py))
                    cover.append({"x": px, "y": py, "kind": "half"})
        seen_diff: set[tuple[int, int]] = set()
        for dx in (cx - 5, cx):
            for dy in (cy - 5, cy):
                if (dx, dy) not in seen_cov and (dx, dy) not in seen_diff and in_bounds({"x": dx, "y": dy}, bounds):
                    seen_diff.add((dx, dy))
                    difficult.append([dx, dy])
    elif pid == "chokepoint-bridge":
        name = f"Chokepoint Bridge ({w}x{h} ft)"
        mid_x = max(10, min(bounds["max_x"] - 10, _snap5(w // 2)))
        mid_y = _snap5(h // 2)
        gap_ys = {mid_y - 5, mid_y, mid_y + 5}
        wall_set: set[tuple[int, int]] = set()
        for y in range(0, bounds["max_y"] + 1, 5):
            if y not in gap_ys:
                walls.append([mid_x, y])
                wall_set.add((mid_x, y))
        cov_set: set[tuple[int, int]] = set()
        for bx in (mid_x - 5, mid_x + 5):
            for by in (mid_y - 10, mid_y + 10):
                if in_bounds({"x": bx, "y": by}, bounds) and (bx, by) not in wall_set and (bx, by) not in cov_set:
                    cov_set.add((bx, by))
                    cover.append({"x": bx, "y": by, "kind": "three_quarters"})
        diff_set: set[tuple[int, int]] = set()
        for bx in (mid_x - 5, mid_x + 5):
            for by in (mid_y - 5, mid_y + 5):
                if in_bounds({"x": bx, "y": by}, bounds) and (bx, by) not in wall_set and (bx, by) not in cov_set and (bx, by) not in diff_set:
                    diff_set.add((bx, by))
                    difficult.append([bx, by])
    else:
        raise ValueError(
            f"Unknown map preset '{preset_id}'. Expected one of {list(BUILTIN_SCENARIO_IDS) + list(PARAMETRIC_MAP_PRESETS)}."
        )

    return {
        "id": pid,
        "name": name,
        "bounds": bounds,
        "terrain": {
            "walls": walls,
            "difficult_terrain": difficult,
            "cover": cover,
        },
    }


def validate_builder_bounds_and_terrain(
    raw_bounds: Any,
    raw_terrain: Any,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Enforce M6.4 #21 map size and terrain caps (20-120 ft, 200/200/100)."""
    if raw_bounds is None:
        raise ValueError("Room 'bounds' is required.")
    bounds = validate_bounds(raw_bounds)
    assert bounds is not None
    width_ft = bounds["max_x"] - bounds["min_x"] + 5
    height_ft = bounds["max_y"] - bounds["min_y"] + 5
    if not (MIN_MAP_FT <= width_ft <= MAX_MAP_FT) or not (MIN_MAP_FT <= height_ft <= MAX_MAP_FT):
        raise ValueError(
            f"Room dimensions must be {MIN_MAP_FT}..{MAX_MAP_FT} ft (got {width_ft}x{height_ft} ft)."
        )

    terrain = validate_terrain(raw_terrain) if raw_terrain is not None else {
        "walls": [],
        "difficult_terrain": [],
        "cover": [],
    }
    assert terrain is not None
    if len(terrain["walls"]) > MAX_WALLS:
        raise ValueError(f"Too many walls ({len(terrain['walls'])} > {MAX_WALLS}).")
    if len(terrain["difficult_terrain"]) > MAX_DIFFICULT:
        raise ValueError(f"Too many difficult_terrain tiles ({len(terrain['difficult_terrain'])} > {MAX_DIFFICULT}).")
    if len(terrain["cover"]) > MAX_COVER:
        raise ValueError(f"Too many cover tiles ({len(terrain['cover'])} > {MAX_COVER}).")

    for wx, wy in terrain["walls"]:
        if not in_bounds({"x": wx, "y": wy}, bounds):
            raise ValueError(f"Wall tile ({wx}, {wy}) is outside room bounds.")
    for dx, dy in terrain["difficult_terrain"]:
        if not in_bounds({"x": dx, "y": dy}, bounds):
            raise ValueError(f"Difficult terrain tile ({dx}, {dy}) is outside room bounds.")
    for cov in terrain["cover"]:
        if not in_bounds({"x": cov["x"], "y": cov["y"]}, bounds):
            raise ValueError(f"Cover tile ({cov['x']}, {cov['y']}) is outside room bounds.")

    return bounds, terrain


def _y_order_from_center(min_y: int, max_y: int) -> list[int]:
    ys = list(range(min_y, max_y + 1, 5))
    center = (min_y + max_y) / 2.0
    ys.sort(key=lambda y: (abs(y - center), y))
    return ys


def auto_place_combatants(
    party_ids: list[str],
    enemy_ids: list[str],
    bounds: dict[str, int],
    terrain: dict[str, Any] | None,
    *,
    explicit_positions: dict[str, dict[str, int]] | None = None,
) -> dict[str, dict[str, int]]:
    """Deterministically place party on West side & enemies on East side, skipping walls & occupied tiles."""
    placed: dict[str, dict[str, int]] = {}
    occupied: set[tuple[int, int]] = set()

    # Apply and validate any explicit positions first.
    for cid, pos in (explicit_positions or {}).items():
        if not isinstance(pos, dict) or not (_is_int(pos.get("x")) and _is_int(pos.get("y"))):
            raise ValueError(f"Combatant '{cid}' position must have integer x and y.")
        p = {"x": int(pos["x"]), "y": int(pos["y"])}
        if p["x"] % 5 or p["y"] % 5:
            raise ValueError(f"Combatant '{cid}' position ({p['x']}, {p['y']}) must be 5-ft aligned.")
        if not in_bounds(p, bounds):
            raise ValueError(f"Combatant '{cid}' position ({p['x']}, {p['y']}) is outside room bounds.")
        if is_wall(p, terrain):
            raise ValueError(f"Combatant '{cid}' position ({p['x']}, {p['y']}) cannot be on a wall.")
        key = (p["x"], p["y"])
        if key in occupied:
            raise ValueError(f"Combatant '{cid}' overlaps another combatant at ({p['x']}, {p['y']}).")
        placed[cid] = p
        occupied.add(key)

    min_x, max_x = bounds["min_x"], bounds["max_x"]
    y_order = _y_order_from_center(bounds["min_y"], bounds["max_y"])

    # West columns: start at min_x + 5 (or min_x if narrow), then min_x, then expand East.
    party_start_x = min(max_x, min_x + 5)
    party_xs: list[int] = [party_start_x]
    if min_x != party_start_x:
        party_xs.append(min_x)
    for x in range(party_start_x + 5, max_x + 1, 5):
        if x not in party_xs:
            party_xs.append(x)

    # East columns: start at max_x - 10 (clamped), then max_x - 5, max_x, then wrap inward West.
    enemy_start_x = max(min_x, max_x - 10)
    enemy_xs: list[int] = [enemy_start_x]
    for x in range(enemy_start_x - 5, min_x - 1, -5):
        enemy_xs.append(x)
    for x in range(enemy_start_x + 5, max_x + 1, 5):
        enemy_xs.append(x)

    def _assign(ids: list[str], x_order: list[int]) -> None:
        for cid in ids:
            if cid in placed:
                continue
            found: dict[str, int] | None = None
            for x in x_order:
                for y in y_order:
                    p = {"x": x, "y": y}
                    if (x, y) not in occupied and in_bounds(p, bounds) and not is_wall(p, terrain):
                        found = p
                        break
                if found is not None:
                    break
            if found is None:
                raise ValueError(
                    f"No legal unoccupied spawn square available for combatant '{cid}' in room bounds."
                )
            placed[cid] = found
            occupied.add((found["x"], found["y"]))

    _assign(party_ids, party_xs)
    _assign(enemy_ids, enemy_xs)
    return placed


# ----------------------------------------------------------------------
# Builder Catalog
# ----------------------------------------------------------------------


def get_builder_catalog(rules: RulesStore) -> dict[str, Any]:
    """Return static options, PC presets, map presets, dungeon presets, limits, and XP table."""
    cc = rules._data.get("character_creation") or {}
    equipment = rules._data.get("equipment") or {}

    presets: list[dict[str, Any]] = []
    for arch in ALL_ARCHETYPES:
        defn = _ARCHETYPE_DEFS[arch]
        presets.append(
            {
                "id": arch,
                "label": defn["label"],
                "core": arch in CORE_ARCHETYPES,
                "class": defn["class"],
                "subclass": defn["subclass"],
                "default_spec": build_pc_preset(arch, 5, rules),
                "specs_by_level": {str(lvl): build_pc_preset(arch, lvl, rules) for lvl in range(MIN_PC_LEVEL, MAX_PC_LEVEL + 1)},
            }
        )

    species_list: list[dict[str, Any]] = []
    for slug in sorted(cc.get("species") or {}):
        sp = cc["species"][slug]
        sub_slugs = [
            strip_prefix(s.get("slug") or s.get("id") or str(s)) if isinstance(s, dict) else strip_prefix(str(s))
            for s in (sp.get("subspecies") or [])
        ]
        species_list.append(
            {
                "slug": slug,
                "name": str(sp.get("name") or slug),
                "subspecies": sorted(sub_slugs),
            }
        )

    backgrounds_list: list[dict[str, Any]] = []
    for slug in sorted(cc.get("backgrounds") or {}):
        bg = cc["backgrounds"][slug]
        backgrounds_list.append(
            {
                "slug": slug,
                "name": str(bg.get("name") or slug),
                "ability_scores": list(bg.get("ability_scores") or []),
                "feat": strip_prefix(str(bg.get("feat") or "")) if bg.get("feat") else None,
            }
        )

    classes_list: list[dict[str, Any]] = []
    for slug in sorted(cc.get("classes") or {}):
        cl = cc["classes"][slug]
        classes_list.append(
            {
                "slug": slug,
                "name": str(cl.get("name") or slug),
                "hit_die": int(cl.get("hit_die") or 8),
            }
        )

    subclasses_list: list[dict[str, Any]] = []
    for slug in sorted(cc.get("subclasses") or {}):
        sub = cc["subclasses"][slug]
        subclasses_list.append(
            {
                "slug": slug,
                "name": str(sub.get("name") or slug),
                "class": strip_prefix(str(sub.get("class") or "")),
                "coverage": str(sub.get("coverage") or "narrative"),
            }
        )

    armors: list[dict[str, Any]] = []
    weapons: list[dict[str, Any]] = []
    for slug in sorted(equipment):
        eq = equipment[slug]
        if not isinstance(eq, dict):
            continue
        cat = str(eq.get("category") or "").lower()
        if cat == "armor" and str(eq.get("armor_category") or "").lower() != "shield":
            armors.append({"slug": slug, "name": str(eq.get("name") or slug), "armor_category": str(eq.get("armor_category") or "")})
        elif cat == "weapon":
            weapons.append({"slug": slug, "name": str(eq.get("name") or slug), "weapon_category": str(eq.get("weapon_category") or "")})

    creature_types = sorted(
        {
            str(m.get("creature_type") or "unknown").lower()
            for m in (rules._data.get("monsters") or {}).values()
            if isinstance(m, dict)
        }
    )

    maps: list[dict[str, Any]] = []
    for sid in BUILTIN_SCENARIO_IDS:
        m = build_map_preset(sid)
        m["kind"] = "builtin"
        maps.append(m)
    for pid in PARAMETRIC_MAP_PRESETS:
        m = build_map_preset(pid, width_ft=50, height_ft=35)
        m["kind"] = "parametric"
        maps.append(m)

    from .dungeon import list_builtin_dungeons

    dungeons: list[dict[str, Any]] = list(list_builtin_dungeons())

    return {
        "presets": presets,
        "character_options": {
            "species": species_list,
            "backgrounds": backgrounds_list,
            "classes": classes_list,
            "subclasses": subclasses_list,
            "armors": armors,
            "weapons": weapons,
            "creature_types": creature_types,
            "drivers": sorted(ALLOWED_BROWSER_DRIVERS),
        },
        "maps": maps,
        "dungeons": dungeons,
        "limits": dict(LIMITS),
        "xp_table": {str(k): dict(v) for k, v in XP_THRESHOLDS_2024.items()},
    }


# ----------------------------------------------------------------------
# Launch Payload Compiler (Single Encounter or 3-Room Dungeon)
# ----------------------------------------------------------------------

_LAUNCH_KEYS: frozenset[str] = frozenset(
    {
        "mode",
        "name",
        "party",
        "rooms",
        "play_as",
        "party_agent",
        "enemy_agent",
        "seed",
        "allow_short_rest",
    }
)
_ROOM_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "name",
        "map_preset",
        "width_ft",
        "height_ft",
        "bounds",
        "terrain",
        "enemies",
        "max_rounds",
    }
)


def compile_launch_payload(
    body: dict[str, Any],
    rules: RulesStore,
) -> dict[str, Any]:
    """Validate and compile a `POST /api/builder/launch` payload.

    Raises `ValueError` for semantic/cap violations (422) or `KeyError` for
    malformed/unknown top-level fields (400) — callers map exceptions cleanly.
    """
    if not isinstance(body, dict):
        raise KeyError("Launch payload must be a JSON object.")
    extra = set(body) - _LAUNCH_KEYS
    if extra:
        raise KeyError(f"Unknown field(s) in launch payload: {', '.join(sorted(extra))}.")

    mode = body.get("mode", "encounter")
    if mode not in ("encounter", "dungeon"):
        raise ValueError("'mode' must be 'encounter' or 'dungeon'.")

    party_agent = body.get("party_agent", "heuristic-tactical")
    enemy_agent = body.get("enemy_agent", "heuristic-tactical")
    if party_agent not in ALLOWED_BROWSER_DRIVERS:
        raise ValueError(
            f"Browser-selected 'party_agent' must be one of {sorted(ALLOWED_BROWSER_DRIVERS)} (got {party_agent!r})."
        )
    if enemy_agent not in ALLOWED_BROWSER_DRIVERS:
        raise ValueError(
            f"Browser-selected 'enemy_agent' must be one of {sorted(ALLOWED_BROWSER_DRIVERS)} (got {enemy_agent!r})."
        )

    seed = body.get("seed", 42)
    if not _is_int(seed):
        raise KeyError("'seed' must be an integer.")
    allow_short_rest = body.get("allow_short_rest", True)
    if not isinstance(allow_short_rest, bool):
        raise KeyError("'allow_short_rest' must be a boolean.")

    raw_name = body.get("name") or ("Custom Dungeon" if mode == "dungeon" else "Custom Encounter")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise KeyError("'name' must be a non-empty string.")
    name = raw_name.strip()[:100]
    encounter_id = _slugify_id(name, "custom-encounter")

    raw_party = body.get("party")
    if not isinstance(raw_party, list) or not (MIN_PARTY_SIZE <= len(raw_party) <= MAX_PARTY_SIZE):
        raise ValueError(f"'party' must have between {MIN_PARTY_SIZE} and {MAX_PARTY_SIZE} PCs.")

    compiled_party: list[dict[str, Any]] = []
    party_ids: list[str] = []
    party_levels: list[int] = []
    seen_pc_ids: set[str] = set()
    for idx, entry in enumerate(raw_party):
        if not isinstance(entry, dict):
            raise ValueError(f"party[{idx}] must be an object.")
        spec = entry.get("spec")
        if not isinstance(spec, dict):
            raise ValueError(f"party[{idx}] must include a 'spec' object.")
        cid = _slugify_id(str(entry.get("id") or f"pc-{idx + 1}"), f"pc-{idx + 1}")
        if cid in seen_pc_ids:
            cid = f"{cid}-{idx + 1}"
        seen_pc_ids.add(cid)

        res = validate_pc_spec(spec, rules)
        if not res["valid"]:
            msg = "; ".join(e.get("message", "invalid") for e in res["errors"][:3])
            raise ValueError(f"Invalid PC '{cid}': {msg}")

        lvl = int(spec.get("level") or 1)
        party_levels.append(lvl)
        party_ids.append(cid)
        compiled_party.append(
            {
                "id": cid,
                "name": str(spec.get("name") or cid),
                "spec": copy.deepcopy(spec),
            }
        )

    play_as = body.get("play_as", "party")
    if play_as not in ("party", "dm") and play_as not in seen_pc_ids:
        raise ValueError(f"'play_as' must be 'party', 'dm', or a party PC id (got {play_as!r}).")

    raw_rooms = body.get("rooms")
    expected_rooms = 1 if mode == "encounter" else MAX_DUNGEON_ROOMS
    if not isinstance(raw_rooms, list) or len(raw_rooms) != expected_rooms:
        raise ValueError(
            f"Mode '{mode}' requires exactly {expected_rooms} room(s) (got {len(raw_rooms) if isinstance(raw_rooms, list) else 'non-list'})."
        )

    compiled_rooms: list[dict[str, Any]] = []
    for r_idx, room in enumerate(raw_rooms):
        if not isinstance(room, dict):
            raise ValueError(f"rooms[{r_idx}] must be an object.")
        r_extra = set(room) - _ROOM_KEYS
        if r_extra:
            raise KeyError(f"Unknown field(s) in rooms[{r_idx}]: {', '.join(sorted(r_extra))}.")

        room_name = str(room.get("name") or f"Room {r_idx + 1}").strip()[:80]
        room_id = _slugify_id(str(room.get("id") or f"room-{r_idx + 1}"), f"room-{r_idx + 1}")
        max_rounds = room.get("max_rounds", 12)
        if not _is_int(max_rounds) or not (1 <= max_rounds <= 50):
            raise ValueError(f"rooms[{r_idx}].max_rounds must be an integer between 1 and 50.")

        if room.get("bounds") is not None:
            bounds, terrain = validate_builder_bounds_and_terrain(room.get("bounds"), room.get("terrain"))
        else:
            preset_id = str(room.get("map_preset") or "open-hall")
            w_ft = room.get("width_ft", 50)
            h_ft = room.get("height_ft", 35)
            built_map = build_map_preset(preset_id, width_ft=w_ft, height_ft=h_ft)
            bounds, terrain = validate_builder_bounds_and_terrain(
                built_map["bounds"],
                room.get("terrain") if room.get("terrain") is not None else built_map["terrain"],
            )

        raw_enemies = room.get("enemies")
        if not isinstance(raw_enemies, list) or not raw_enemies:
            raise ValueError(f"rooms[{r_idx}] must define at least {MIN_ENEMIES_PER_ROOM} enemy.")

        expanded_enemies: list[dict[str, Any]] = []
        enemy_ids: list[str] = []
        explicit_pos: dict[str, dict[str, int]] = {}
        slug_counts: dict[str, int] = {}

        for e_idx, e_entry in enumerate(raw_enemies):
            if not isinstance(e_entry, dict):
                raise ValueError(f"rooms[{r_idx}].enemies[{e_idx}] must be an object.")
            raw_slug = e_entry.get("monster")
            if not isinstance(raw_slug, str) or not raw_slug.strip():
                raise ValueError(f"rooms[{r_idx}].enemies[{e_idx}] must specify a 'monster' slug.")
            m_slug = strip_prefix(raw_slug)
            m_rec = rules.get_monster(m_slug)
            if m_rec is None:
                raise ValueError(f"Unknown monster '{raw_slug}' in rooms[{r_idx}].")
            count = e_entry.get("count", 1)
            if not _is_int(count) or not (1 <= count <= MAX_ENEMIES_PER_ROOM):
                raise ValueError(f"Enemy count for '{m_slug}' must be 1..{MAX_ENEMIES_PER_ROOM}.")
            base_name = str(e_entry.get("name") or m_rec.get("name") or m_slug).strip()[:60]
            for c_i in range(count):
                slug_counts[m_slug] = slug_counts.get(m_slug, 0) + 1
                num = slug_counts[m_slug]
                eid = f"r{r_idx + 1}-{m_slug}-{num}"
                if eid in seen_pc_ids:
                    eid = f"enemy-{eid}"
                label = f"{base_name} {chr(64 + num)}" if (count > 1 or num > 1) and num <= 26 else (
                    f"{base_name} {num}" if (count > 1 or num > 1) else base_name
                )
                enemy_ids.append(eid)
                expanded_enemies.append({"id": eid, "monster": m_slug, "name": label})
                if c_i == 0 and isinstance(e_entry.get("position"), dict):
                    explicit_pos[eid] = e_entry["position"]

        if not (MIN_ENEMIES_PER_ROOM <= len(expanded_enemies) <= MAX_ENEMIES_PER_ROOM):
            raise ValueError(
                f"rooms[{r_idx}] must have between {MIN_ENEMIES_PER_ROOM} and {MAX_ENEMIES_PER_ROOM} enemies (got {len(expanded_enemies)})."
            )
        if len(party_ids) + len(expanded_enemies) > MAX_COMBATANTS_PER_ENCOUNTER:
            raise ValueError(
                f"rooms[{r_idx}] exceeds {MAX_COMBATANTS_PER_ENCOUNTER} total combatants "
                f"({len(party_ids)} PCs + {len(expanded_enemies)} enemies)."
            )

        positions = auto_place_combatants(
            party_ids,
            enemy_ids,
            bounds,
            terrain,
            explicit_positions=explicit_pos,
        )
        for enemy in expanded_enemies:
            enemy["position"] = positions[enemy["id"]]

        compiled_rooms.append(
            {
                "id": room_id,
                "name": room_name,
                "max_rounds": max_rounds,
                "bounds": bounds,
                "terrain": terrain,
                "party_spawns": {pid: positions[pid] for pid in party_ids},
                "enemies": expanded_enemies,
            }
        )

    party_level = max(party_levels)
    tier = 1 if party_level <= 4 else 2

    if mode == "encounter":
        r0 = compiled_rooms[0]
        scenario_dict: dict[str, Any] = {
            "id": encounter_id,
            "name": name,
            "description": f"Custom 5e 2024 (SRD 5.2) encounter ({len(compiled_party)} PCs vs {len(r0['enemies'])} enemies).",
            "tier": tier,
            "party_level": party_level,
            "max_rounds": r0["max_rounds"],
            "bounds": r0["bounds"],
            "terrain": r0["terrain"],
            "party": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "spec": p["spec"],
                    "position": r0["party_spawns"][p["id"]],
                }
                for p in compiled_party
            ],
            "enemies": r0["enemies"],
        }
        return {
            "mode": "encounter",
            "scenario": scenario_dict,
            "play_as": play_as,
            "party_agent": party_agent,
            "enemy_agent": enemy_agent,
            "seed": seed,
            "allow_short_rest": allow_short_rest,
        }

    dungeon_dict: dict[str, Any] = {
        "id": encounter_id,
        "name": name,
        "description": f"Custom 3-room 5e 2024 (SRD 5.2) dungeon ({len(compiled_party)} PCs).",
        "tier": tier,
        "party_level": party_level,
        "allow_short_rest_between_rooms": allow_short_rest,
        "party": compiled_party,
        "rooms": compiled_rooms,
    }
    return {
        "mode": "dungeon",
        "dungeon": dungeon_dict,
        "play_as": play_as,
        "party_agent": party_agent,
        "enemy_agent": enemy_agent,
        "seed": seed,
        "allow_short_rest": allow_short_rest,
    }


__all__ = [
    "ALL_ARCHETYPES",
    "ALLOWED_BROWSER_DRIVERS",
    "CORE_ARCHETYPES",
    "LIMITS",
    "MAX_BUILDER_BODY_BYTES",
    "MAX_COMBATANTS_PER_ENCOUNTER",
    "MAX_COVER",
    "MAX_DIFFICULT",
    "MAX_DUNGEON_ROOMS",
    "MAX_ENEMIES_PER_ROOM",
    "MAX_MAP_FT",
    "MAX_PARTY_SIZE",
    "MAX_PC_LEVEL",
    "MAX_WALLS",
    "MIN_ENEMIES_PER_ROOM",
    "MIN_MAP_FT",
    "MIN_PARTY_SIZE",
    "MIN_PC_LEVEL",
    "PARAMETRIC_MAP_PRESETS",
    "XP_THRESHOLDS_2024",
    "auto_place_combatants",
    "build_map_preset",
    "build_pc_preset",
    "compile_launch_payload",
    "compute_encounter_budget",
    "get_builder_catalog",
    "search_monsters",
    "validate_builder_bounds_and_terrain",
    "validate_pc_spec",
]
