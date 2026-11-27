"""Character Creation Rules IR compiler (§3.9, 100% deterministic structured SRD)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import DAMAGE_TYPE_SLUGS, load_raw_json, normalize_prose


def _extract_primary_abilities(raw_primary: Any) -> list[str]:
    if not isinstance(raw_primary, dict):
        return []
    if raw_primary.get("ability_scores"):
        return [a["index"] for a in raw_primary["ability_scores"]]
    opts = (
        raw_primary.get("ability_score_options", {})
        .get("from", {})
        .get("options", [])
    )
    return [o["item"]["index"] for o in opts if isinstance(o, dict) and isinstance(o.get("item"), dict)]


def _extract_proficiency_choices(raw_choices: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pc in raw_choices or []:
        options = pc.get("from", {}).get("options", [])
        from_items: list[str] = []
        from_skills: list[str] = []
        for opt in options:
            if opt.get("option_type") == "reference" and isinstance(opt.get("item"), dict):
                idx = opt["item"]["index"]
                from_items.append(idx)
                if idx.startswith("skill-"):
                    from_skills.append(idx.removeprefix("skill-"))
        out.append(
            {
                "choose": int(pc.get("choose", 0)),
                "desc": pc.get("desc", ""),
                "from": sorted(from_items),
                "from_skills": sorted(from_skills),
            }
        )
    return out


def _compile_classes(en_dir: Path, raw_levels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    records = load_raw_json(en_dir, "5e-SRD-Classes.json")
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        class_levels = sorted(
            [lv for lv in raw_levels if lv["class"]["index"] == slug and not lv.get("subclass")],
            key=lambda lv: lv["level"],
        )

        # Weapon mastery progression across levels 1..20 (§3.9)
        has_wm_feature = any(
            any("weapon-mastery" in f["index"] for f in lv.get("features", []))
            for lv in class_levels
        )
        wm_prog: dict[str, int] = {}
        for lv in class_levels:
            lvl_num = int(lv["level"])
            cs = lv.get("class_specific") or {}
            if "weapon_mastery" in cs and cs["weapon_mastery"] is not None:
                wm_prog[str(lvl_num)] = int(cs["weapon_mastery"])
            elif has_wm_feature:
                wm_prog[str(lvl_num)] = 2
            else:
                wm_prog[str(lvl_num)] = 0

        asi_levels = [
            int(lv["level"])
            for lv in class_levels
            if any("ability-score-improvement" in f["index"] for f in lv.get("features", []))
        ]
        epic_boon_levels = [
            int(lv["level"])
            for lv in class_levels
            if any("epic-boon" in f["index"] for f in lv.get("features", []))
        ]

        sc = rec.get("spellcasting") or {}
        sc_ability = sc.get("spellcasting_ability", {}).get("index") if isinstance(sc, dict) else None

        compiled[slug] = {
            "id": f"classes:{slug}",
            "slug": slug,
            "name": rec["name"],
            "hit_die": int(rec["hit_die"]),
            "saving_throws": [s["index"] for s in rec.get("saving_throws", [])],
            "primary_ability": _extract_primary_abilities(rec.get("primary_ability")),
            "proficiencies": sorted(p["index"] for p in rec.get("proficiencies", [])),
            "proficiency_choices": _extract_proficiency_choices(rec.get("proficiency_choices")),
            "spellcasting_ability": sc_ability,
            "subclasses": sorted(sc_ref["index"] for sc_ref in rec.get("subclasses", [])),
            "weapon_mastery_progression": wm_prog,
            "asi_levels": asi_levels,
            "epic_boon_levels": epic_boon_levels,
        }
    return compiled


def _compile_subclasses(en_dir: Path, raw_levels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    records = load_raw_json(en_dir, "5e-SRD-Subclasses.json")
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        sc_levels = sorted(
            [lv for lv in raw_levels if (lv.get("subclass") or {}).get("index") == slug],
            key=lambda lv: lv["level"],
        )
        feature_levels = [int(lv["level"]) for lv in sc_levels]
        features = [
            f["index"]
            for lv in sc_levels
            for f in lv.get("features", [])
        ]
        compiled[slug] = {
            "id": f"subclasses:{slug}",
            "slug": slug,
            "name": rec["name"],
            "class": rec["class"]["index"],
            "feature_levels": feature_levels,
            "features": features,
        }
    return compiled


def _compile_species(en_dir: Path) -> dict[str, dict[str, Any]]:
    records = load_raw_json(en_dir, "5e-SRD-Species.json")
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        raw_size_opts = rec.get("size_options")
        size_options: list[str] | None = None
        if isinstance(raw_size_opts, dict):
            opts = raw_size_opts.get("from", {}).get("options", [])
            parsed_opts = [o["size"] for o in opts if isinstance(o, dict) and o.get("size")]
            if parsed_opts:
                # Prefer Medium first for deterministic readability when present
                size_options = sorted(parsed_opts, key=lambda s: (0 if s == "Medium" else 1, s))

        compiled[slug] = {
            "id": f"species:{slug}",
            "slug": slug,
            "name": rec["name"],
            "creature_type": rec.get("creature_type") or "Humanoid",
            "speed_ft": int(rec["speed"]),
            "size": rec.get("size"),
            "size_options": size_options,
            "subspecies": sorted(s["index"] for s in rec.get("subspecies", [])),
            "traits": sorted(t["index"] for t in rec.get("traits", [])),
        }
    return compiled


def _compile_subspecies(en_dir: Path) -> dict[str, dict[str, Any]]:
    records = load_raw_json(en_dir, "5e-SRD-Subspecies.json")
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        dt = rec.get("damage_type")
        traits = [
            {
                "trait": t["index"],
                "level": int(t.get("level", 1)),
            }
            for t in rec.get("traits", [])
        ]
        compiled[slug] = {
            "id": f"subspecies:{slug}",
            "slug": slug,
            "name": rec["name"],
            "species": rec["species"]["index"],
            "damage_type": dt["index"] if isinstance(dt, dict) else None,
            "traits": traits,
        }
    return compiled


def _compile_traits(en_dir: Path) -> dict[str, dict[str, Any]]:
    records = load_raw_json(en_dir, "5e-SRD-Traits.json")
    compiled: dict[str, dict[str, Any]] = {}

    darkvision_re = re.compile(r"Darkvision with a range of (\d+) feet", re.IGNORECASE)
    resist_re = re.compile(r"Resistance to (\w+) damage", re.IGNORECASE)

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        desc = normalize_prose(rec.get("description", ""))

        speed_override_ft: int | None = None
        if rec.get("speed") is not None:
            speed_override_ft = int(rec["speed"])
        elif slug == "wood-elf-speed-increase":
            speed_override_ft = 35

        darkvision_ft: int | None = None
        dm = darkvision_re.search(desc)
        if dm:
            darkvision_ft = int(dm.group(1))

        damage_resistances: list[str] = []
        for rm in resist_re.finditer(desc):
            dtype = rm.group(1).lower()
            if dtype in DAMAGE_TYPE_SLUGS and dtype not in damage_resistances:
                damage_resistances.append(dtype)
        damage_resistances.sort()

        hp_per_level_bonus = 1 if slug == "dwarven-toughness" else 0
        extra_origin_feat = slug == "versatile"
        extra_skill_choice = slug == "skillful"

        spells: list[str] = []
        if slug.startswith("lineage-spell-"):
            spells.append(slug.removeprefix("lineage-spell-"))
        elif slug == "otherworldly-presence":
            spells.append("thaumaturgy")
        elif slug == "gnomish-lineage-forest-gnome":
            spells.append("speak-with-animals")
        elif slug == "gnomish-lineage-rock-gnome":
            spells.append("prestidigitation")

        compiled[slug] = {
            "id": f"traits:{slug}",
            "slug": slug,
            "name": rec["name"],
            "species": sorted(s["index"] for s in rec.get("species", [])),
            "subspecies": sorted(s["index"] for s in rec.get("subspecies", [])),
            "speed_override_ft": speed_override_ft,
            "darkvision_ft": darkvision_ft,
            "damage_resistances": damage_resistances,
            "hp_per_level_bonus": hp_per_level_bonus,
            "extra_origin_feat": extra_origin_feat,
            "extra_skill_choice": extra_skill_choice,
            "spells": sorted(set(spells)),
        }
    return compiled


def _compile_backgrounds(en_dir: Path) -> dict[str, dict[str, Any]]:
    records = load_raw_json(en_dir, "5e-SRD-Backgrounds.json")
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        feat_obj = rec.get("feat") or {}
        compiled[slug] = {
            "id": f"backgrounds:{slug}",
            "slug": slug,
            "name": rec["name"],
            "ability_scores": sorted(a["index"] for a in rec.get("ability_scores", [])),
            "feat": feat_obj.get("index"),
            "feat_note": feat_obj.get("note"),
            "proficiencies": sorted(p["index"] for p in rec.get("proficiencies", [])),
            "proficiency_choices": _extract_proficiency_choices(rec.get("proficiency_choices")),
        }
    return compiled


_FEAT_MECHANICAL_EFFECTS: dict[str, list[dict[str, Any]]] = {
    "alert": [
        {"kind": "initiative_bonus", "formula": "proficiency_bonus"},
    ],
    "archery": [
        {"kind": "ranged_weapon_attack_bonus", "value": 2},
    ],
    "defense": [
        {"kind": "ac_bonus_in_armor", "value": 1},
    ],
    "great-weapon-fighting": [
        {"kind": "min_damage_die_two_handed_melee", "min_roll": 3},
    ],
    "two-weapon-fighting": [
        {"kind": "offhand_light_add_ability_mod", "value": True},
    ],
    "savage-attacker": [
        {"kind": "reroll_weapon_damage_once_per_turn", "value": True},
    ],
    "ability-score-improvement": [
        {
            "kind": "ability_score_increase",
            "points": 2,
            "max_per_ability": 2,
            "cap": 20,
            "allowed_abilities": ["str", "dex", "con", "int", "wis", "cha"],
        }
    ],
    "grappler": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 20,
            "allowed_abilities": ["str", "dex"],
        }
    ],
    "boon-of-combat-prowess": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 30,
            "allowed_abilities": ["str", "dex", "con", "int", "wis", "cha"],
        }
    ],
    "boon-of-dimensional-travel": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 30,
            "allowed_abilities": ["str", "dex", "con", "int", "wis", "cha"],
        }
    ],
    "boon-of-fate": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 30,
            "allowed_abilities": ["str", "dex", "con", "int", "wis", "cha"],
        }
    ],
    "boon-of-irresistible-offense": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 30,
            "allowed_abilities": ["str", "dex"],
        }
    ],
    "boon-of-spell-recall": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 30,
            "allowed_abilities": ["int", "wis", "cha"],
        }
    ],
    "boon-of-the-night-spirit": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 30,
            "allowed_abilities": ["str", "dex", "con", "int", "wis", "cha"],
        }
    ],
    "boon-of-truesight": [
        {
            "kind": "ability_score_increase",
            "points": 1,
            "max_per_ability": 1,
            "cap": 30,
            "allowed_abilities": ["str", "dex", "con", "int", "wis", "cha"],
        }
    ],
}


def _compile_feats(en_dir: Path) -> dict[str, dict[str, Any]]:
    records = load_raw_json(en_dir, "5e-SRD-Feats.json")
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        raw_prereq = rec.get("prerequisites") or {}
        prerequisites = {
            "minimum_level": raw_prereq.get("minimum_level"),
            "feature_named": raw_prereq.get("feature_named"),
        }

        raw_opt = rec.get("prerequisite_options")
        prerequisite_options: list[dict[str, Any]] = []
        if isinstance(raw_opt, dict):
            for opt in raw_opt.get("from", {}).get("options", []):
                if opt.get("option_type") == "score_prerequisite":
                    prerequisite_options.append(
                        {
                            "ability": opt["ability_score"]["index"],
                            "minimum_score": int(opt["minimum_score"]),
                        }
                    )

        rep_raw = rec.get("repeatable")
        compiled[slug] = {
            "id": f"feats:{slug}",
            "slug": slug,
            "name": rec["name"],
            "feat_type": rec["type"],
            "repeatable": bool(rep_raw),
            "repeatable_note": rep_raw if isinstance(rep_raw, str) else None,
            "prerequisites": prerequisites,
            "prerequisite_options": prerequisite_options,
            "mechanical_effects": list(_FEAT_MECHANICAL_EFFECTS.get(slug, [])),
        }
    return compiled


def _compile_levels(raw_levels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    compiled: dict[str, dict[str, Any]] = {}
    for rec in sorted(raw_levels, key=lambda r: r["index"]):
        slug = rec["index"]
        compiled[slug] = {
            "id": f"levels:{slug}",
            "slug": slug,
            "level": int(rec["level"]),
            "class": rec["class"]["index"],
            "subclass": rec["subclass"]["index"] if rec.get("subclass") else None,
            "prof_bonus": rec.get("prof_bonus"),
            "features": [f["index"] for f in rec.get("features", [])],
            "class_specific": rec.get("class_specific"),
            "spellcasting": rec.get("spellcasting"),
        }
    return compiled


def compile_character_creation(en_dir: Path) -> dict[str, Any]:
    """Compile all structured Character Creation rules (§3.9)."""
    raw_abilities = load_raw_json(en_dir, "5e-SRD-Ability-Scores.json")
    raw_skills = load_raw_json(en_dir, "5e-SRD-Skills.json")
    raw_damage_types = load_raw_json(en_dir, "5e-SRD-Damage-Types.json")
    raw_levels = load_raw_json(en_dir, "5e-SRD-Levels.json")

    ability_scores = {
        rec["index"]: {
            "id": f"ability-scores:{rec['index']}",
            "slug": rec["index"],
            "name": rec["name"],
            "full_name": rec.get("full_name", rec["name"]),
            "skills": sorted(s["index"] for s in rec.get("skills", [])),
        }
        for rec in sorted(raw_abilities, key=lambda r: r["index"])
    }

    skills = {
        rec["index"]: {
            "id": f"skills:{rec['index']}",
            "slug": rec["index"],
            "name": rec["name"],
            "ability": rec["ability_score"]["index"],
        }
        for rec in sorted(raw_skills, key=lambda r: r["index"])
    }

    damage_types = sorted(rec["index"] for rec in raw_damage_types)

    return {
        "ability_scores": ability_scores,
        "skills": skills,
        "damage_types": damage_types,
        "classes": _compile_classes(en_dir, raw_levels),
        "subclasses": _compile_subclasses(en_dir, raw_levels),
        "species": _compile_species(en_dir),
        "subspecies": _compile_subspecies(en_dir),
        "backgrounds": _compile_backgrounds(en_dir),
        "feats": _compile_feats(en_dir),
        "levels": _compile_levels(raw_levels),
        "traits": _compile_traits(en_dir),
    }
