"""Equipment Rules IR compiler (§3.6, all 182 items)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import clean_dice_expr, load_raw_json, make_provenance

SOURCE_FILE = "5e-SRD-Equipment.json"


def _compile_weapon(rec: dict[str, Any], cats: list[str]) -> dict[str, Any]:
    slug = rec["index"]
    eid = f"equipment:{slug}"
    cat_set = set(cats)
    weapon_category = "martial" if "martial-weapons" in cat_set else "simple"
    weapon_range = "ranged" if "ranged-weapons" in cat_set else "melee"

    dmg = rec["damage"]
    damage_dice = clean_dice_expr(dmg["damage_dice"])
    damage_type = dmg["damage_type"]["index"]

    two_handed = rec.get("two_handed_damage")
    versatile_dice = clean_dice_expr(two_handed["damage_dice"]) if two_handed else None

    properties = sorted(p["index"] for p in rec.get("properties", []))
    prop_set = set(properties)
    mastery = rec["mastery"]["index"] if rec.get("mastery") else None

    if "finesse" in prop_set:
        attack_ability = "finesse"
    elif weapon_range == "ranged":
        attack_ability = "dex"
    else:
        attack_ability = "str"

    if "reach" in prop_set:
        reach_ft: int | None = 10
    elif weapon_range == "melee":
        reach_ft = 5
    else:
        reach_ft = None

    raw_range = rec.get("range")
    if weapon_range == "ranged" and isinstance(raw_range, dict):
        range_normal_ft: int | None = raw_range.get("normal")
        range_long_ft: int | None = raw_range.get("long")
    else:
        range_normal_ft = None
        range_long_ft = None

    raw_throw = rec.get("throw_range")
    throw_range = (
        {"normal_ft": raw_throw["normal"], "long_ft": raw_throw.get("long")}
        if isinstance(raw_throw, dict)
        else None
    )

    ammo = rec.get("ammunition")
    ammunition_slug = ammo["index"] if isinstance(ammo, dict) else None

    return {
        "id": eid,
        "slug": slug,
        "name": rec["name"],
        "kind": "weapon",
        "categories": cats,
        "weapon_category": weapon_category,
        "weapon_range": weapon_range,
        "damage_dice": damage_dice,
        "damage_type": damage_type,
        "versatile_dice": versatile_dice,
        "properties": properties,
        "mastery": mastery,
        "attack_ability": attack_ability,
        "reach_ft": reach_ft,
        "range_normal_ft": range_normal_ft,
        "range_long_ft": range_long_ft,
        "throw_range": throw_range,
        "ammunition_slug": ammunition_slug,
        "cost": rec.get("cost"),
        "weight": rec.get("weight"),
        "coverage": "full",
        "provenance": make_provenance(eid, SOURCE_FILE, "structured", []),
    }


def _compile_armor(rec: dict[str, Any], cats: list[str]) -> dict[str, Any]:
    slug = rec["index"]
    eid = f"equipment:{slug}"
    cat_set = set(cats)
    raw_ac = rec["armor_class"]
    dex_bonus = bool(raw_ac.get("dex_bonus", False))
    max_bonus = raw_ac.get("max_bonus")
    # For heavy armor raw JSON has dex_bonus=False, max_bonus=0; keep int or None cleanly.
    if not dex_bonus:
        max_bonus = None if "shields" in cat_set else 0

    if "shields" in cat_set or slug == "shield":
        kind = "shield"
        armor_category = "shield"
    else:
        kind = "armor"
        if "heavy-armor" in cat_set:
            armor_category = "heavy"
        elif "medium-armor" in cat_set or (dex_bonus and max_bonus == 2):
            # Upstream quirk: hide-armor is tagged light-armor despite max_bonus: 2
            armor_category = "medium"
        else:
            armor_category = "light"

    return {
        "id": eid,
        "slug": slug,
        "name": rec["name"],
        "kind": kind,
        "categories": cats,
        "armor_category": armor_category,
        "armor_class": {
            "base": int(raw_ac["base"]),
            "dex_bonus": dex_bonus,
            "max_bonus": max_bonus,
        },
        "str_minimum": int(rec.get("str_minimum") or 0),
        "stealth_disadvantage": bool(rec.get("stealth_disadvantage", False)),
        "don_time": rec.get("don_time"),
        "doff_time": rec.get("doff_time"),
        "cost": rec.get("cost"),
        "weight": rec.get("weight"),
        "coverage": "full",
        "provenance": make_provenance(eid, SOURCE_FILE, "structured", []),
    }


def _compile_other(rec: dict[str, Any], cats: list[str]) -> dict[str, Any]:
    slug = rec["index"]
    eid = f"equipment:{slug}"
    cat_set = set(cats)

    raw_utilize = rec.get("utilize") or []
    raw_contents = rec.get("contents") or []
    raw_craft = rec.get("craft") or []
    raw_ability = rec.get("ability")

    if raw_utilize:
        kind = "tool"
        coverage = "partial"
        unmodeled = ["tool_utilize_situational_adjudication"]
    elif raw_contents:
        kind = "pack" if "equipment-packs" in cat_set else "container"
        coverage = "partial"
        unmodeled = ["pack_inventory_narrative_use"]
    else:
        kind = "tool" if "tools" in cat_set else "gear"
        coverage = "narrative"
        unmodeled = ["non_combat_adventuring_gear"]

    utilize = [
        {
            "action": u.get("name") or u.get("action") or u.get("desc") or "",
            "dc": u.get("dc"),
        }
        for u in raw_utilize
    ]
    contents = [
        {
            "item": c["item"]["index"],
            "quantity": int(c.get("quantity", 1)),
        }
        for c in raw_contents
        if isinstance(c, dict) and isinstance(c.get("item"), dict)
    ]
    craft = sorted(c["index"] for c in raw_craft if isinstance(c, dict) and "index" in c)

    item: dict[str, Any] = {
        "id": eid,
        "slug": slug,
        "name": rec["name"],
        "kind": kind,
        "categories": cats,
        "ability": raw_ability["index"] if isinstance(raw_ability, dict) else None,
        "utilize": utilize,
        "craft": craft,
        "contents": contents,
        "cost": rec.get("cost"),
        "weight": rec.get("weight"),
        "description": rec.get("description", ""),
        "coverage": coverage,
        "provenance": make_provenance(eid, SOURCE_FILE, "structured", unmodeled),
    }
    return item


def compile_equipment(en_dir: Path) -> dict[str, dict[str, Any]]:
    """Compile all 182 SRD 2024 equipment items into typed IR."""
    records = load_raw_json(en_dir, SOURCE_FILE)
    compiled: dict[str, dict[str, Any]] = {}

    for rec in sorted(records, key=lambda r: r["index"]):
        slug = rec["index"]
        cats = sorted(c["index"] for c in rec.get("equipment_categories", []))
        cat_set = set(cats)

        if "weapons" in cat_set:
            compiled[slug] = _compile_weapon(rec, cats)
        elif "armor" in cat_set or "shields" in cat_set:
            compiled[slug] = _compile_armor(rec, cats)
        else:
            compiled[slug] = _compile_other(rec, cats)

    return compiled
