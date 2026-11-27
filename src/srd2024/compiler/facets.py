"""Resource tiers and facet extraction contract for all 25 SRD 2024 types (SPEC §4.3)."""

from __future__ import annotations

import re
from typing import Any

from .text import canonical_scalar

# Bundle tier mapping per SPEC §4.3
TYPE_TIERS: dict[str, int] = {
    "ability-scores": 1,
    "alignments": 2,
    "backgrounds": 2,
    "classes": 2,
    "conditions": 1,
    "damage-types": 1,
    "equipment": 2,
    "equipment-categories": 3,
    "feats": 1,
    "features": 2,
    "languages": 2,
    "levels": 3,
    "magic-items": 2,
    "magic-schools": 1,
    "monsters": 2,
    "poisons": 1,
    "proficiencies": 3,
    "skills": 1,
    "species": 2,
    "spells": 1,
    "subclasses": 2,
    "subspecies": 2,
    "traits": 1,
    "weapon-mastery-properties": 1,
    "weapon-properties": 1,
}

_TRAILING_INT_RE = re.compile(r"(\d+)$")


def _add(facets: dict[str, set[str]], key: str, value: Any) -> None:
    if value is None:
        return
    if isinstance(value, str) and not value.strip():
        return
    facets.setdefault(key, set()).add(canonical_scalar(value))


def _add_ref_index(facets: dict[str, set[str]], key: str, ref: Any) -> None:
    if isinstance(ref, dict) and ref.get("index"):
        _add(facets, key, ref["index"])


def _add_ref_list(facets: dict[str, set[str]], key: str, refs: Any) -> None:
    if isinstance(refs, list):
        for ref in refs:
            _add_ref_index(facets, key, ref)


def extract_facets(
    rtype: str,
    rec: dict[str, Any],
    levels_by_url: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    """Extract sorted lowercased facets strictly following SPEC §4.3."""
    raw: dict[str, set[str]] = {}

    if rtype == "ability-scores":
        _add_ref_list(raw, "skill", rec.get("skills"))

    elif rtype == "alignments":
        _add(raw, "abbreviation", rec.get("abbreviation"))

    elif rtype == "backgrounds":
        _add_ref_list(raw, "ability", rec.get("ability_scores"))
        _add_ref_index(raw, "feat", rec.get("feat"))
        _add_ref_list(raw, "proficiency", rec.get("proficiencies"))

    elif rtype == "classes":
        _add(raw, "hit_die", rec.get("hit_die"))
        _add_ref_list(raw, "saving_throw", rec.get("saving_throws"))
        _add(raw, "spellcaster", bool(rec.get("spellcasting")))
        _add_ref_list(raw, "subclass", rec.get("subclasses"))

    elif rtype == "equipment":
        _add_ref_list(raw, "category", rec.get("equipment_categories"))
        _add_ref_index(raw, "mastery", rec.get("mastery"))
        _add_ref_list(raw, "property", rec.get("properties"))
        dmg = rec.get("damage")
        if isinstance(dmg, dict):
            _add_ref_index(raw, "damage_type", dmg.get("damage_type"))
        _add_ref_index(raw, "ability", rec.get("ability"))
        cost = rec.get("cost")
        if isinstance(cost, dict):
            _add(raw, "cost_unit", cost.get("unit"))

    elif rtype == "feats":
        _add(raw, "feat_type", rec.get("type"))
        _add(raw, "repeatable", "repeatable" in rec and bool(rec.get("repeatable")))
        has_prereq = bool(rec.get("prerequisites") or rec.get("prerequisite_options"))
        _add(raw, "has_prerequisite", has_prereq)

    elif rtype == "features":
        _add_ref_index(raw, "class", rec.get("class"))
        _add_ref_index(raw, "subclass", rec.get("subclass"))
        lvl_ref = rec.get("level")
        lvl_num: int | None = None
        if isinstance(lvl_ref, dict):
            if levels_by_url and lvl_ref.get("url") in levels_by_url:
                lvl_num = levels_by_url[lvl_ref["url"]].get("level")
            if lvl_num is None and isinstance(lvl_ref.get("index"), str):
                m = _TRAILING_INT_RE.search(lvl_ref["index"])
                if m:
                    lvl_num = int(m.group(1))
        elif isinstance(lvl_ref, int):
            lvl_num = lvl_ref
        if lvl_num is not None:
            _add(raw, "level", lvl_num)

    elif rtype == "languages":
        _add(raw, "is_rare", bool(rec.get("is_rare")))

    elif rtype == "levels":
        _add_ref_index(raw, "class", rec.get("class"))
        _add_ref_index(raw, "subclass", rec.get("subclass"))
        _add(raw, "level", rec.get("level"))

    elif rtype == "magic-items":
        _add_ref_index(raw, "category", rec.get("equipment_category"))
        rarity = rec.get("rarity")
        if isinstance(rarity, dict):
            _add(raw, "rarity", rarity.get("name"))
        elif isinstance(rarity, str):
            _add(raw, "rarity", rarity)
        _add(raw, "attunement", bool(rec.get("attunement")))
        _add(raw, "variant", bool(rec.get("variant")))
        if rec.get("limited-to"):
            _add(raw, "limited_to", rec.get("limited-to"))

    elif rtype == "monsters":
        _add(raw, "cr", rec.get("challenge_rating"))
        _add(raw, "creature_type", rec.get("type"))
        _add(raw, "size", rec.get("size"))
        _add(raw, "alignment", rec.get("alignment"))
        _add_ref_list(raw, "condition_immunity", rec.get("condition_immunities"))

    elif rtype == "poisons":
        _add(raw, "poison_type", rec.get("type"))
        _add(raw, "cost", rec.get("cost"))

    elif rtype == "proficiencies":
        _add(raw, "proficiency_type", rec.get("type"))
        _add_ref_list(raw, "class", rec.get("classes"))
        _add_ref_list(raw, "background", rec.get("backgrounds"))

    elif rtype == "skills":
        _add_ref_index(raw, "ability", rec.get("ability_score"))

    elif rtype == "species":
        _add(raw, "creature_type", rec.get("type"))
        _add(raw, "size", rec.get("size"))
        _add(raw, "speed", rec.get("speed"))
        _add(raw, "has_subspecies", bool(rec.get("subspecies")))

    elif rtype == "spells":
        _add(raw, "level", rec.get("level"))
        _add_ref_index(raw, "school", rec.get("school"))
        _add_ref_list(raw, "class", rec.get("classes"))
        _add(raw, "ritual", bool(rec.get("ritual")))
        _add(raw, "concentration", bool(rec.get("concentration")))
        dmg = rec.get("damage")
        if isinstance(dmg, dict):
            _add_ref_index(raw, "damage_type", dmg.get("damage_type"))
        _add(raw, "attack_type", rec.get("attack_type"))

    elif rtype == "subclasses":
        _add_ref_index(raw, "class", rec.get("class"))

    elif rtype == "subspecies":
        _add_ref_index(raw, "species", rec.get("species"))
        _add_ref_index(raw, "damage_type", rec.get("damage_type"))

    elif rtype == "traits":
        _add_ref_list(raw, "species", rec.get("species"))
        _add_ref_list(raw, "subspecies", rec.get("subspecies"))

    # Types with no facets per §4.3:
    # conditions, damage-types, equipment-categories, magic-schools,
    # weapon-mastery-properties, weapon-properties

    return {k: sorted(v) for k, v in sorted(raw.items()) if v}
