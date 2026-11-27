"""Monsters Rules IR compiler (§3.8, all 341 monsters)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import (
    ABILITY_NAME_TO_SLUG,
    ABILITY_SLUGS,
    CONDITION_SLUGS,
    DAMAGE_TYPE_SLUGS,
    ability_mod,
    clean_dice_expr,
    load_raw_json,
    make_provenance,
    normalize_prose,
    parse_aoe,
)

SOURCE_FILE = "5e-SRD-Monsters.json"

_FEET_RE = re.compile(r"(\d+)\s*ft", re.IGNORECASE)
_REACH_RE = re.compile(r"reach\s+(\d+)\s*ft", re.IGNORECASE)
_RANGE_RE = re.compile(r"range\s+(\d+)(?:/(\d+))?\s*ft", re.IGNORECASE)
_COND_LIST = "|".join(sorted(CONDITION_SLUGS))
_MONSTER_COND_RE = re.compile(
    rf"\b(?:has|have|gains?|is|are|becomes?)\s+(?:the\s+)?({_COND_LIST})\s+condition\b",
    re.IGNORECASE,
)
_SIZE_CAP_RE = re.compile(
    r"(Tiny|Small|Medium|Large|Huge)\s+or\s+smaller",
    re.IGNORECASE,
)
_ESCAPE_DC_RE = re.compile(r"escape\s+DC\s+(\d+)", re.IGNORECASE)
_CR_STR_MAP = {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}


def _parse_speed(raw_speed: dict[str, Any]) -> dict[str, Any]:
    def _ft(key: str) -> int:
        val = raw_speed.get(key)
        if isinstance(val, int):
            return val
        if isinstance(val, str):
            m = _FEET_RE.search(val)
            if m:
                return int(m.group(1))
        return 0

    return {
        "walk_ft": _ft("walk"),
        "fly_ft": _ft("fly"),
        "swim_ft": _ft("swim"),
        "climb_ft": _ft("climb"),
        "burrow_ft": _ft("burrow"),
        "hover": bool(raw_speed.get("hover", False)),
    }


def _split_damage_traits(values: list[str]) -> tuple[list[str], list[str]]:
    standard: list[str] = []
    special: list[str] = []
    for raw in values:
        low = raw.strip().lower()
        if low in DAMAGE_TYPE_SLUGS:
            if low not in standard:
                standard.append(low)
        else:
            special.append(raw.strip())
    return sorted(standard), special


def _parse_damage_parts(raw_damage: list[dict[str, Any]], desc_norm: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    low = desc_norm.lower()
    has_adv_bonus = bool(
        re.search(r"if\s+(?:the\s+attack\s+roll\s+had|the\s+\w+\s+had)\s+advantage", low)
    )
    has_bloodied_alt = bool(re.search(r"if\s+the\s+\w+\s+is\s+bloodied", low))

    for idx, item in enumerate(raw_damage or []):
        dice_raw = item.get("damage_dice")
        dtype_obj = item.get("damage_type") or {}
        dtype = dtype_obj.get("index")
        if not dice_raw or not dtype:
            continue
        dice_clean = clean_dice_expr(str(dice_raw))
        conditional: str | None = None
        if idx > 0:
            if has_adv_bonus:
                conditional = "advantage"
            elif has_bloodied_alt:
                conditional = "bloodied"
        parts.append(
            {
                "dice": dice_clean,
                "type": dtype,
                "conditional": conditional,
            }
        )
    return parts


def _parse_action_conditions(
    desc_norm: str,
    save_obj: dict[str, Any] | None,
    has_attack: bool,
) -> list[dict[str, Any]]:
    conds: list[dict[str, Any]] = []
    seen: set[str] = set()
    low_full = desc_norm.lower()

    for sentence in re.split(r"(?<=[.!?])\s+", desc_norm):
        s_low = sentence.lower()
        if any(w in s_low for w in ("ends the", "end the", "immune to", "no longer", "isn't grappled")):
            continue
        for cm in _MONSTER_COND_RE.finditer(sentence):
            cond = cm.group(1).lower()
            if cond in seen:
                continue
            seen.add(cond)
            if save_obj and ("failure:" in s_low or "failed save" in s_low or not has_attack):
                trigger = "on_fail"
            elif save_obj and not ("hit:" in s_low and "failure:" not in low_full):
                trigger = "on_fail"
            elif has_attack:
                trigger = "on_hit"
            else:
                trigger = "auto"

            repeat_ab: str | None = None
            if "repeats the save" in low_full or "repeat the save" in low_full:
                duration = "save_ends"
                repeat_ab = save_obj["ability"] if save_obj else None
            elif "until the start of" in s_low and "next turn" in s_low:
                duration = "start_of_next_turn"
            elif "until the end of" in s_low and "next turn" in s_low:
                duration = "end_of_next_turn"
            else:
                duration = "indefinite"

            size_m = _SIZE_CAP_RE.search(sentence)
            escape_m = _ESCAPE_DC_RE.search(sentence)
            entry: dict[str, Any] = {
                "condition": cond,
                "trigger": trigger,
                "duration": duration,
                "repeat_save_ability": repeat_ab,
            }
            if size_m:
                entry["max_target_size"] = size_m.group(1).capitalize()
            if escape_m and cond == "grappled":
                entry["escape_dc"] = int(escape_m.group(1))
            conds.append(entry)
    return conds


def _parse_dc_obj(raw_dc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw_dc, dict):
        return None
    ab = (raw_dc.get("dc_type") or {}).get("index")
    dc_val = raw_dc.get("dc_value")
    if not ab or dc_val is None:
        return None
    on_success = raw_dc.get("success_type") or "none"
    return {
        "ability": ab,
        "dc": int(dc_val),
        "on_success": on_success,
    }


def _parse_multiattack(act: dict[str, Any]) -> dict[str, Any]:
    desc_norm = normalize_prose(act.get("desc", ""))
    sequence: list[dict[str, Any]] = []
    for item in act.get("actions") or []:
        cnt_raw = item.get("count", 1)
        cnt = int(cnt_raw) if str(cnt_raw).isdigit() else 1
        sequence.append(
            {
                "action_name": item["action_name"],
                "count": cnt,
            }
        )

    options_out: list[dict[str, Any]] = []
    raw_opts = act.get("action_options")
    choose_n: int | None = None
    if isinstance(raw_opts, dict):
        choose_n = int(raw_opts.get("choose", 1))
        for opt in raw_opts.get("from", {}).get("options", []):
            otype = opt.get("option_type")
            if otype == "action":
                cnt_raw = opt.get("count", 1)
                cnt = int(cnt_raw) if str(cnt_raw).isdigit() else 1
                options_out.append(
                    {
                        "choose": choose_n,
                        "actions": [{"action_name": opt["action_name"], "count": cnt}],
                    }
                )
            elif otype == "multiple":
                sub_actions = []
                for sub in opt.get("items", []):
                    cnt_raw = sub.get("count", 1)
                    cnt = int(cnt_raw) if str(cnt_raw).isdigit() else 1
                    sub_actions.append({"action_name": sub["action_name"], "count": cnt})
                options_out.append({"choose": choose_n, "actions": sub_actions})

    # Compute total_attacks deterministically
    seq_total = sum(s["count"] for s in sequence)
    if sequence and not options_out:
        total_attacks: int | None = seq_total
    elif sequence and options_out:
        # e.g., Rend x2 + choose 1 of (Rend x1 or Spell) -> seq_total + choose_n
        total_attacks = seq_total + (choose_n or 0)
    elif options_out:
        # Check if "Any combination" (choose N) vs choosing 1 of alternatives
        if raw_opts and "any combination" in str(raw_opts.get("desc", "")).lower():
            total_attacks = choose_n
        else:
            first_counts = [sum(a["count"] for a in o["actions"]) for o in options_out]
            total_attacks = max(first_counts) if first_counts else choose_n
    else:
        total_attacks = None

    return {
        "kind": "multiattack",
        "name": act["name"],
        "multiattack": {
            "total_attacks": total_attacks,
            "sequence": sequence,
            "options": options_out,
            "desc": desc_norm,
        },
        "desc": desc_norm,
    }


def _parse_spellcasting_action(act: dict[str, Any]) -> dict[str, Any]:
    desc_norm = normalize_prose(act.get("desc", ""))
    sc = act["spellcasting"]
    ab = (sc.get("ability") or {}).get("index")
    spells_out = []
    for sp in sc.get("spells", []):
        spells_out.append(
            {
                "slug": sp["index"],
                "name": sp["name"],
                "level": int(sp.get("level", 0)),
                "usage": sp.get("usage"),
            }
        )
    return {
        "kind": "spellcasting",
        "name": act["name"],
        "spellcasting": {
            "ability": ab,
            "save_dc": sc.get("dc"),
            "attack_bonus": sc.get("modifier"),
            "components_required": list(sc.get("components_required", [])),
            "spells": spells_out,
        },
        "desc": desc_norm,
    }


def _parse_monster_action(act: dict[str, Any]) -> dict[str, Any]:
    if act.get("multiattack_type") or act["name"] == "Multiattack":
        return _parse_multiattack(act)
    if act.get("spellcasting"):
        return _parse_spellcasting_action(act)

    desc_norm = normalize_prose(act.get("desc", ""))
    low = desc_norm.lower()
    save_obj = _parse_dc_obj(act.get("dc"))
    damage_parts = _parse_damage_parts(act.get("damage") or [], desc_norm)
    usage = act.get("usage")

    if act.get("attack_bonus") is not None:
        if "melee or ranged attack" in low:
            attack_kind = "melee_or_ranged"
        elif "ranged attack" in low or "ranged weapon attack" in low or "ranged spell attack" in low:
            attack_kind = "ranged"
        else:
            attack_kind = "melee"

        reach_m = _REACH_RE.search(desc_norm)
        range_m = _RANGE_RE.search(desc_norm)
        reach_ft = int(reach_m.group(1)) if reach_m else (5 if attack_kind in ("melee", "melee_or_ranged") else None)
        range_normal_ft = int(range_m.group(1)) if range_m else None
        range_long_ft = int(range_m.group(2)) if (range_m and range_m.group(2)) else None

        conditions_applied = _parse_action_conditions(desc_norm, save_obj, has_attack=True)
        return {
            "kind": "attack",
            "name": act["name"],
            "attack_kind": attack_kind,
            "attack_bonus": int(act["attack_bonus"]),
            "reach_ft": reach_ft,
            "range_normal_ft": range_normal_ft,
            "range_long_ft": range_long_ft,
            "damage_parts": damage_parts,
            "save": save_obj,
            "conditions_applied": conditions_applied,
            "usage": usage,
            "desc": desc_norm,
        }

    if save_obj is not None:
        aoe = parse_aoe(desc_norm)
        conditions_applied = _parse_action_conditions(desc_norm, save_obj, has_attack=False)
        return {
            "kind": "save",
            "name": act["name"],
            "save": save_obj,
            "aoe": aoe,
            "damage_parts": damage_parts,
            "conditions_applied": conditions_applied,
            "usage": usage,
            "desc": desc_norm,
        }

    # Remaining non-attack, non-save, non-multiattack, non-spellcasting special action (14 total)
    return {
        "kind": "special",
        "name": act["name"],
        "usage": usage,
        "desc": desc_norm,
    }


# Traits with no effect in the engine's model: a 2D combat grid with no water, vertical terrain, vision rules,
# objects or social interaction. Everything else in `special_abilities` is graded as unmodeled (#39). Keep this
# list conservative: when in doubt, leave a trait out so it's reported.
_DESCRIPTIVE_TRAITS = frozenset({
    "amphibious", "limited amphibiousness", "water breathing", "hold breath",
    "spider climb", "web walker", "ice walk", "earth glide", "tunneler",
    "standing leap", "running leap", "jumper", "beast of burden",
    "illumination", "sense magic", "treasure sense", "iron scent", "ethereal sight", "divine awareness",
    "inscrutable", "mimicry", "speak with beasts and plants", "shark telepathy", "telepathic bond", "probing telepathy",
})


def _trait_key(name: str) -> str:
    """'Legendary Resistance (3/Day, or 4/Day in Lair)' -> 'legendary resistance'."""
    return name.split(" (")[0].strip().lower()


def _clause_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _trait_key(name)).strip("-")


def _compile_monster(rec: dict[str, Any]) -> dict[str, Any]:
    slug = rec["index"]
    eid = f"monsters:{slug}"

    abilities = {
        "str": int(rec["strength"]),
        "dex": int(rec["dexterity"]),
        "con": int(rec["constitution"]),
        "int": int(rec["intelligence"]),
        "wis": int(rec["wisdom"]),
        "cha": int(rec["charisma"]),
    }
    ability_mods = {ab: ability_mod(val) for ab, val in abilities.items()}

    saves = dict(ability_mods)
    skills: dict[str, int] = {}
    for prof in rec.get("proficiencies", []):
        p_idx = prof["proficiency"]["index"]
        p_val = int(prof["value"])
        if p_idx.startswith("saving-throw-"):
            ab = p_idx.removeprefix("saving-throw-")
            if ab in ABILITY_SLUGS:
                saves[ab] = p_val
        elif p_idx.startswith("skill-"):
            skills[p_idx.removeprefix("skill-")] = p_val

    ac_entries = rec.get("armor_class") or [{"value": 10}]
    ac_value = int(ac_entries[0]["value"])
    ac_armor = sorted(
        a["index"]
        for entry in ac_entries
        for a in entry.get("armor", [])
        if isinstance(a, dict) and "index" in a
    )

    dmg_res, spec_res = _split_damage_traits(rec.get("damage_resistances", []))
    dmg_imm, spec_imm = _split_damage_traits(rec.get("damage_immunities", []))
    dmg_vul, spec_vul = _split_damage_traits(rec.get("damage_vulnerabilities", []))
    special_damage_traits = sorted(set(spec_res + spec_imm + spec_vul))

    cond_imm = sorted(c["index"] for c in rec.get("condition_immunities", []))

    parsed_actions = [_parse_monster_action(a) for a in rec.get("actions", [])]

    special_abilities = [
        {"name": sa["name"], "desc": normalize_prose(sa.get("desc", ""))}
        for sa in rec.get("special_abilities", [])
    ]
    reactions = [
        {"name": r["name"], "desc": normalize_prose(r.get("desc", ""))}
        for r in rec.get("reactions", [])
    ]
    bonus_actions = [
        {"name": b["name"], "desc": normalize_prose(b.get("desc", ""))}
        for b in rec.get("bonus_actions", [])
    ]
    legendary_actions = [
        {"name": la["name"], "desc": normalize_prose(la.get("desc", ""))}
        for la in rec.get("legendary_actions", [])
    ]

    # Honest monster coverage grading (§3.8):
    # - narrative is always 0 (every monster has a full deterministic statblock)
    # - full: has >=1 executable primary action and NO unmodeled spellcasting,
    #   legendary_actions, unparsed special actions, or special_damage_traits.
    # - partial: full statblock + core attacks executable, with unmodeled clauses listed explicitly.
    unmodeled: list[str] = []
    if not parsed_actions:
        unmodeled.append("no_standard_actions")
    if any(a["kind"] == "spellcasting" for a in parsed_actions):
        unmodeled.append("innate_spellcasting_prose")
    if any(a["kind"] == "special" for a in parsed_actions):
        unmodeled.append("special_non_attack_actions")
    if legendary_actions:
        unmodeled.append("legendary_actions")
    if special_damage_traits:
        unmodeled.append("conditional_damage_traits")
    # #39: the engine never executes stat-block traits, bonus actions or reactions (no reaction system yet: #33/#38).
    # Each one is listed by name so agents can see exactly what isn't automated. The exception is a short set of
    # traits that can't affect this engine's 2D, combat-only model (water, climbing, senses, communication, lore).
    for sa in special_abilities:
        if _trait_key(sa["name"]) not in _DESCRIPTIVE_TRAITS:
            unmodeled.append(f"special_ability:{_clause_slug(sa['name'])}")
    for b in bonus_actions:
        unmodeled.append(f"bonus_action:{_clause_slug(b['name'])}")
    for r in reactions:
        unmodeled.append(f"reaction:{_clause_slug(r['name'])}")

    coverage = "full" if not unmodeled else "partial"

    return {
        "id": eid,
        "slug": slug,
        "name": rec["name"],
        "size": rec["size"],
        "creature_type": rec["type"],
        "alignment": rec.get("alignment", ""),
        "cr": rec["challenge_rating"],
        "xp": int(rec.get("xp", 0)),
        "proficiency_bonus": int(rec["proficiency_bonus"]),
        "ac": ac_value,
        "ac_armor": ac_armor,
        "hp_max": int(rec["hit_points"]),
        "hit_dice": rec.get("hit_dice", ""),
        "hit_points_roll": rec.get("hit_points_roll", ""),
        "speed": _parse_speed(rec.get("speed") or {}),
        "abilities": abilities,
        "ability_mods": ability_mods,
        "saves": saves,
        "skills": skills,
        "senses": rec.get("senses") or {},
        "languages": rec.get("languages", ""),
        "damage_resistances": dmg_res,
        "damage_immunities": dmg_imm,
        "damage_vulnerabilities": dmg_vul,
        "special_damage_traits": special_damage_traits,
        "condition_immunities": cond_imm,
        "actions": parsed_actions,
        "bonus_actions": bonus_actions,
        "reactions": reactions,
        "legendary_actions": legendary_actions,
        "special_abilities": special_abilities,
        "coverage": coverage,
        "provenance": make_provenance(eid, SOURCE_FILE, "regex+structured", unmodeled),
    }


def compile_monsters(en_dir: Path) -> dict[str, dict[str, Any]]:
    """Compile all 341 SRD 2024 monsters into typed IR."""
    records = load_raw_json(en_dir, SOURCE_FILE)
    compiled: dict[str, dict[str, Any]] = {}
    for rec in sorted(records, key=lambda r: r["index"]):
        compiled[rec["index"]] = _compile_monster(rec)
    return compiled
