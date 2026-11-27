"""Cross-reference graph edge extraction & deterministic rel normalization (SPEC §6)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

_REF_CORE_KEYS = frozenset({"index", "name", "url"})
_SKIP_SEGMENTS = frozenset({"from", "options", "option", "item", "items", "of", "choice"})

# Explicit semantic relation normalization map for cleaned path tuples.
# Matches SPEC §6.3 and §10.1 (e.g., weapon_mastery, weapon_property, actions_spell, etc.).
_EXACT_REL_MAP: dict[tuple[str, ...], str] = {
    ("skills",): "skill",
    ("ability_scores",): "ability_score",
    ("ability_score",): "ability_score",
    ("ability",): "ability_score",
    ("feat",): "feat",
    ("proficiencies",): "proficiency",
    ("proficiencies", "proficiency"): "proficiency",
    ("proficiency_choices",): "proficiency_choices",
    ("equipment_options",): "equipment_options",
    ("equipment_options", "equipment_category"): "equipment_options",
    ("starting_equipment_options",): "starting_equipment",
    ("starting_equipment_options", "equipment_category"): "starting_equipment",
    ("multi_classing", "prerequisite_options", "ability_score"): "multiclass_prerequisite",
    ("multi_classing", "prerequisites", "ability_score"): "multiclass_prerequisite",
    ("multi_classing", "proficiencies"): "multiclass_proficiency",
    ("multi_classing", "proficiency_choices"): "multiclass_proficiency_choice",
    ("primary_ability", "ability_score_options"): "primary_ability",
    ("primary_ability", "ability_scores"): "primary_ability",
    ("saving_throws",): "saving_throw",
    ("spellcasting", "spellcasting_ability"): "spellcasting_ability",
    ("subclasses",): "subclass",
    ("subclass",): "subclass",
    ("class",): "class",
    ("classes",): "class",
    ("level",): "level",
    ("features",): "feature",
    ("ammunition",): "ammunition",
    ("contents",): "contents",
    ("craft",): "craft",
    ("damage", "damage_type"): "damage_type",
    ("two_handed_damage", "damage_type"): "two_handed_damage_type",
    ("damage_type",): "damage_type",
    ("equipment_categories",): "equipment_categories",
    ("equipment_category",): "equipment_category",
    ("equipment",): "equipment",
    ("mastery",): "weapon_mastery",
    ("properties",): "weapon_property",
    ("storage",): "storage",
    ("utilize", "dc", "dc_type"): "utilize_dc_type",
    ("prerequisite_options", "ability_score"): "prerequisite_ability",
    ("variants",): "variant",
    ("armor_class", "armor"): "armor",
    ("condition_immunities",): "condition_immunity",
    ("forms",): "form",
    ("backgrounds",): "background",
    ("reference",): "reference",
    ("species",): "species",
    ("subspecies",): "subspecies",
    ("traits",): "trait",
    ("school",): "school",
    ("spells", "spell"): "spell",
    # Monster action/ability nested refs
    ("actions", "damage", "damage_type"): "actions_damage_type",
    ("actions", "dc", "dc_type"): "actions_dc_type",
    ("actions", "spellcasting", "ability"): "actions_spellcasting_ability",
    ("actions", "spellcasting", "spells"): "actions_spell",
    ("bonus_actions", "damage", "damage_type"): "bonus_actions_damage_type",
    ("bonus_actions", "dc", "dc_type"): "bonus_actions_dc_type",
    ("bonus_actions", "spellcasting", "ability"): "bonus_actions_spellcasting_ability",
    ("bonus_actions", "spellcasting", "spells"): "bonus_actions_spell",
    ("legendary_actions", "damage", "damage_type"): "legendary_actions_damage_type",
    ("legendary_actions", "dc", "dc_type"): "legendary_actions_dc_type",
    ("legendary_actions", "spellcasting", "ability"): "legendary_actions_spellcasting_ability",
    ("legendary_actions", "spellcasting", "spells"): "legendary_actions_spell",
    ("reactions", "damage", "damage_type"): "reactions_damage_type",
    ("reactions", "dc", "dc_type"): "reactions_dc_type",
    ("reactions", "spellcasting", "ability"): "reactions_spellcasting_ability",
    ("reactions", "spellcasting", "spells"): "reactions_spell",
    ("special_abilities", "damage", "damage_type"): "special_abilities_damage_type",
    ("special_abilities", "dc", "dc_type"): "special_abilities_dc_type",
    ("special_abilities", "spellcasting", "ability"): "special_abilities_spellcasting_ability",
    ("special_abilities", "spellcasting", "spells"): "special_abilities_spell",
}


def normalize_rel(path_parts: list[str]) -> str:
    """Derive a concise, deterministic snake_case relation name from ancestor dict keys."""
    cleaned = tuple(
        seg.lower().replace("-", "_")
        for seg in path_parts
        if seg.lower().replace("-", "_") not in _SKIP_SEGMENTS
    )
    if cleaned in _EXACT_REL_MAP:
        return _EXACT_REL_MAP[cleaned]
    if not cleaned:
        return "reference"
    return "_".join(cleaned)


@dataclass(frozen=True)
class EdgeRow:
    src_id: str
    dst_id: str
    rel: str
    json_path: str
    meta_obj: Any  # None, dict, or list[dict]
    meta_json: str | None


@dataclass(frozen=True)
class DanglingRefRow:
    src_id: str
    target_raw: str
    rel: str
    json_path: str


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def extract_graph(
    records: list[tuple[str, str, dict[str, Any]]],
    url_to_id: dict[str, str],
) -> tuple[list[EdgeRow], list[DanglingRefRow]]:
    """Extract all resolved directed edges and dangling references (SPEC §6)."""
    # Keyed by (src_id, dst_id, rel) -> {"paths": set[str], "metas": dict[str, Any]}
    edge_acc: dict[tuple[str, str, str], dict[str, Any]] = {}
    dangling_set: set[DanglingRefRow] = set()

    def walk(obj: Any, path_parts: list[str], src_id: str) -> None:
        if isinstance(obj, dict):
            url_val = obj.get("url")
            if isinstance(url_val, str) and url_val.startswith("/api/2024/"):
                rel = normalize_rel(path_parts)
                json_path = ".".join(path_parts)
                dst_id = url_to_id.get(url_val)
                if dst_id is None:
                    dangling_set.add(
                        DanglingRefRow(
                            src_id=src_id,
                            target_raw=url_val,
                            rel=rel,
                            json_path=json_path,
                        )
                    )
                elif dst_id != src_id:
                    key = (src_id, dst_id, rel)
                    bucket = edge_acc.get(key)
                    if bucket is None:
                        bucket = {"paths": set(), "metas": {}}
                        edge_acc[key] = bucket
                    bucket["paths"].add(json_path)
                    extras = {k: v for k, v in obj.items() if k not in _REF_CORE_KEYS}
                    if extras:
                        canon = _canonical_json(extras)
                        bucket["metas"][canon] = extras
                # Still walk any extra nested fields just in case a ref embeds another ref
                for k, v in obj.items():
                    if k not in _REF_CORE_KEYS:
                        walk(v, path_parts + [k], src_id)
                return

            for k, v in obj.items():
                walk(v, path_parts + [k], src_id)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, path_parts, src_id)

    for _rtype, src_id, rec in records:
        # Strictly below top-level object (SPEC §6.1)
        for k, v in rec.items():
            walk(v, [k], src_id)

    edges: list[EdgeRow] = []
    for (src_id, dst_id, rel), bucket in sorted(edge_acc.items()):
        first_path = min(bucket["paths"])
        metas_map: dict[str, Any] = bucket["metas"]
        if not metas_map:
            meta_obj = None
            meta_json = None
        elif len(metas_map) == 1:
            canon, meta_obj = next(iter(metas_map.items()))
            meta_json = canon
        else:
            sorted_objs = [metas_map[c] for c in sorted(metas_map.keys())]
            meta_obj = sorted_objs
            meta_json = _canonical_json(sorted_objs)
        edges.append(
            EdgeRow(
                src_id=src_id,
                dst_id=dst_id,
                rel=rel,
                json_path=first_path,
                meta_obj=meta_obj,
                meta_json=meta_json,
            )
        )

    dangling = sorted(
        dangling_set,
        key=lambda d: (d.src_id, d.target_raw, d.rel, d.json_path),
    )
    return edges, dangling
