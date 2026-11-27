"""Token-tight Markdown card compiler & 1-line summary generator (SPEC §7)."""

from __future__ import annotations

import re
from typing import Any

from .text import (
    canonical_scalar,
    collapse_ws,
    first_sentence,
    truncate_summary,
    yaml_quote,
)


def _escape_link_text(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _rel_link(ref: dict[str, Any], url_to_id: dict[str, str]) -> str:
    """Render a resolved reference dict as [Name](../<type>/<slug>.md)."""
    name = str(ref.get("name") or ref.get("index") or "")
    url = ref.get("url")
    if isinstance(url, str) and url in url_to_id:
        dst_id = url_to_id[url]
        dst_type, dst_slug = dst_id.split(":", 1)
        return f"[{_escape_link_text(name)}](../{dst_type}/{dst_slug}.md)"
    return name


def _link_list(refs: Any, url_to_id: dict[str, str]) -> str:
    if not isinstance(refs, list) or not refs:
        return ""
    return ", ".join(_rel_link(r, url_to_id) for r in refs if isinstance(r, dict))


def _format_mod(score: int) -> str:
    mod = (score - 10) // 2
    return f"+{mod}" if mod >= 0 else str(mod)


def _format_cr(cr: int | float) -> str:
    if cr == 0.125:
        return "1/8"
    if cr == 0.25:
        return "1/4"
    if cr == 0.5:
        return "1/2"
    if isinstance(cr, float) and cr.is_integer():
        return str(int(cr))
    return str(cr)


def _format_spell_level(lvl: int) -> str:
    if lvl == 0:
        return "Cantrip"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(lvl, "th")
    return f"Level {lvl}" if lvl >= 1 else f"{lvl}{suffix}-level"


def _format_choice_refs(choice: Any, url_to_id: dict[str, str]) -> str:
    """Render a choice dict concisely, linking any embedded entity references."""
    if not isinstance(choice, dict):
        return ""
    desc = choice.get("desc")
    choose = choice.get("choose")
    from_obj = choice.get("from")
    links: list[str] = []

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            url = node.get("url")
            if isinstance(url, str) and url in url_to_id:
                links.append(_rel_link(node, url_to_id))
                return
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    if from_obj is not None:
        collect(from_obj)

    # Deduplicate preserving order
    seen: set[str] = set()
    uniq_links = []
    for lnk in links:
        if lnk not in seen:
            seen.add(lnk)
            uniq_links.append(lnk)

    if isinstance(desc, str) and desc.strip():
        base = desc.strip()
        if uniq_links:
            return f"{base} ({', '.join(uniq_links)})"
        return base
    if uniq_links:
        prefix = f"Choose {choose}: " if choose else ""
        return prefix + ", ".join(uniq_links)
    return ""


def _join_magic_item_desc(desc_list: list[str]) -> str:
    """Join magic-items.desc preserving Markdown pipe tables intact (SPEC §7.2)."""
    if not desc_list:
        return ""
    out: list[str] = []
    prev_is_table = False
    for raw_item in desc_list:
        item = str(raw_item).strip("\r\n")
        if not item.strip():
            continue
        is_table = item.lstrip().startswith("|")
        if out:
            if is_table and prev_is_table:
                out.append("\n")
            else:
                out.append("\n\n")
        out.append(item)
        prev_is_table = is_table
    return "".join(out)


def _format_monster_usage(usage: Any) -> str:
    if not isinstance(usage, dict):
        return ""
    utype = str(usage.get("type", ""))
    times = usage.get("times")
    if utype == "per day" and times is not None:
        return f"{times}/day"
    if utype == "at will":
        return "At will"
    if utype == "recharge on roll":
        min_val = usage.get("min_value")
        dice = usage.get("dice", "1d6")
        return f"Recharge {min_val}+ ({dice})" if min_val else "Recharge"
    if utype == "recharge after rest":
        rests = usage.get("rest_types") or []
        return f"1/{' or '.join(rests)} rest" if rests else utype
    if times is not None and utype:
        return f"{times} {utype}"
    return utype


def _render_monster_action_list(
    heading: str,
    actions: list[dict[str, Any]],
    url_to_id: dict[str, str],
) -> list[str]:
    if not actions:
        return []
    lines = [f"## {heading}"]
    for act in actions:
        name = str(act.get("name", "Action"))
        usage = _format_monster_usage(act.get("usage"))
        label = f"**{name} ({usage}).**" if usage else f"**{name}.**"
        desc = str(act.get("desc", "")).strip()
        line = f"- {label} {desc}" if desc else f"- {label}"
        sc = act.get("spellcasting")
        if isinstance(sc, dict):
            spells = sc.get("spells") or []
            if spells:
                grouped: dict[str, list[str]] = {}
                for sp in spells:
                    if not isinstance(sp, dict):
                        continue
                    u = _format_monster_usage(sp.get("usage")) or "Other"
                    entry = _rel_link(sp, url_to_id)
                    notes = sp.get("notes")
                    lvl = sp.get("level")
                    details: list[str] = []
                    if lvl is not None and lvl > 0:
                        details.append(f"level {lvl}")
                    if isinstance(notes, str) and notes.strip():
                        details.append(notes.strip())
                    if details:
                        entry = f"{entry} ({'; '.join(details)})"
                    grouped.setdefault(u, []).append(entry)
                spell_parts = [f"{k}: {', '.join(v)}" for k, v in grouped.items()]
                line += "\n  - " + "\n  - ".join(spell_parts)
        lines.append(line)
    return lines


def _format_prereqs(rec: dict[str, Any], url_to_id: dict[str, str]) -> str:
    parts: list[str] = []
    pre = rec.get("prerequisites")
    if isinstance(pre, dict):
        if pre.get("minimum_level") is not None:
            parts.append(f"Level {pre['minimum_level']}+")
        if pre.get("feature_named"):
            parts.append(f"{pre['feature_named']} feature")
        for k, v in sorted(pre.items()):
            if k in ("minimum_level", "feature_named"):
                continue
            parts.append(f"{k.replace('_', ' ').title()}: {v}")
    pre_opt = rec.get("prerequisite_options")
    if isinstance(pre_opt, dict):
        opt_txt = _format_choice_refs(pre_opt, url_to_id)
        if opt_txt:
            parts.append(opt_txt)
    return "; ".join(parts)


def build_summary(rtype: str, rec: dict[str, Any]) -> str:
    """Build a crisp, non-empty <=200-char single-line mechanical summary (SPEC §7.2)."""
    name = str(rec.get("name", rec.get("index", "")))

    if rtype == "spells":
        lvl = rec.get("level", 0)
        school = rec.get("school", {}).get("name", "")
        lvl_str = f"{school} cantrip" if lvl == 0 else f"Level {lvl} {school}"
        tags: list[str] = []
        if rec.get("ritual"):
            tags.append("Ritual")
        if rec.get("concentration"):
            tags.append("Conc.")
        if tags:
            lvl_str += f" ({', '.join(tags)})"
        classes = ", ".join(c["name"] for c in rec.get("classes", []) if isinstance(c, dict))
        parts = [
            lvl_str,
            str(rec.get("casting_time", "")),
            str(rec.get("range", "")),
            str(rec.get("duration", "")),
        ]
        if classes:
            parts.append(classes)
        return truncate_summary("; ".join(p for p in parts if p))

    if rtype == "monsters":
        cr = _format_cr(rec.get("challenge_rating", 0))
        size = rec.get("size", "")
        ctype = rec.get("type", "")
        ac_list = rec.get("armor_class") or []
        ac_val = ac_list[0].get("value", "") if ac_list and isinstance(ac_list[0], dict) else ""
        hp = rec.get("hit_points", "")
        align = rec.get("alignment", "")
        return truncate_summary(
            f"CR {cr} {size} {ctype} ({align}); AC {ac_val}, HP {hp}, XP {rec.get('xp', 0)}"
        )

    if rtype == "equipment":
        cats = [c["name"] for c in rec.get("equipment_categories", []) if isinstance(c, dict)]
        cat_label = cats[0] if cats else "Equipment"
        details: list[str] = []
        if "damage" in rec and isinstance(rec["damage"], dict):
            dmg = rec["damage"]
            dt = dmg.get("damage_type", {}).get("name", "").lower()
            dmg_s = f"{dmg.get('damage_dice', '')} {dt}".strip()
            if "two_handed_damage" in rec and isinstance(rec["two_handed_damage"], dict):
                dmg_s += f" (Versatile {rec['two_handed_damage'].get('damage_dice', '')})"
            details.append(dmg_s)
        if "mastery" in rec and isinstance(rec["mastery"], dict):
            details.append(f"Mastery: {rec['mastery'].get('name', '')}")
        if "armor_class" in rec and isinstance(rec["armor_class"], dict):
            ac = rec["armor_class"]
            ac_s = f"AC {ac.get('base', '')}"
            if ac.get("dex_bonus"):
                if ac.get("max_bonus") is not None:
                    ac_s += f" + Dex (max {ac['max_bonus']})"
                else:
                    ac_s += " + Dex"
            details.append(ac_s)
        if "utilize" in rec and isinstance(rec["utilize"], list) and rec["utilize"]:
            ab = rec.get("ability", {}).get("name")
            details.append(f"Tool ({ab})" if ab else "Tool")
        cost = rec.get("cost")
        if isinstance(cost, dict):
            details.append(f"{cost.get('quantity', '')} {cost.get('unit', '')}".strip())
        if "weight" in rec and rec["weight"] is not None:
            details.append(f"{rec['weight']} lb.")
        return truncate_summary(f"{cat_label}; {', '.join(details)}" if details else cat_label)

    if rtype == "magic-items":
        cat = rec.get("equipment_category", {}).get("name", "Magic Item")
        rarity = rec.get("rarity", {}).get("name", "")
        att = "requires attunement" if rec.get("attunement") else "no attunement"
        lim = rec.get("limited-to")
        if lim:
            att += f" ({lim})"
        return truncate_summary(f"{cat}, {rarity} ({att})")

    if rtype == "classes":
        hd = rec.get("hit_die", "")
        saves = ", ".join(s["name"] for s in rec.get("saving_throws", []) if isinstance(s, dict))
        sc = "Spellcaster" if rec.get("spellcasting") else "Non-spellcaster"
        subs = ", ".join(s["name"] for s in rec.get("subclasses", []) if isinstance(s, dict))
        return truncate_summary(
            f"Class; d{hd} Hit Die, Saves: {saves}, {sc}" + (f", Subclass: {subs}" if subs else "")
        )

    if rtype == "subclasses":
        cls = rec.get("class", {}).get("name", "")
        s = rec.get("summary") or first_sentence(rec.get("description", ""))
        return truncate_summary(f"{cls} subclass; {s}" if cls else s)

    if rtype == "levels":
        cls = rec.get("class", {}).get("name", "")
        sub = rec.get("subclass", {}).get("name") if isinstance(rec.get("subclass"), dict) else None
        lvl = rec.get("level", "")
        feats = [f["name"] for f in rec.get("features", []) if isinstance(f, dict)]
        prefix = f"{cls} ({sub}) Level {lvl}" if sub else f"{cls} Level {lvl}"
        if feats:
            return truncate_summary(f"{prefix}: {', '.join(feats)}")
        pb = rec.get("prof_bonus")
        return truncate_summary(f"{prefix}" + (f" (PB +{pb})" if pb is not None else ""))

    if rtype == "features":
        cls = rec.get("class", {}).get("name", "")
        sub = rec.get("subclass", {}).get("name") if isinstance(rec.get("subclass"), dict) else None
        lvl_idx = rec.get("level", {}).get("index", "") if isinstance(rec.get("level"), dict) else ""
        m = re.search(r"(\d+)$", lvl_idx)
        lvl_s = f" L{m.group(1)}" if m else ""
        owner = f"{cls}/{sub}" if sub else cls
        lead = first_sentence(rec.get("description", ""), 140)
        return truncate_summary(f"{owner}{lvl_s} feature: {lead}")

    if rtype == "feats":
        ftype = str(rec.get("type", "")).replace("-", " ").title()
        lead = first_sentence(rec.get("description", ""), 150)
        return truncate_summary(f"{ftype} Feat: {lead}")

    if rtype == "backgrounds":
        abs_s = ", ".join(a["name"] for a in rec.get("ability_scores", []) if isinstance(a, dict))
        feat = rec.get("feat", {}).get("name", "")
        profs = ", ".join(p["name"] for p in rec.get("proficiencies", []) if isinstance(p, dict))
        return truncate_summary(f"Background; Abilities: {abs_s}; Feat: {feat}; Proficiencies: {profs}")

    if rtype == "species":
        size = rec.get("size") or "Small or Medium"
        speed = rec.get("speed", "")
        traits = ", ".join(t["name"] for t in rec.get("traits", []) if isinstance(t, dict))
        return truncate_summary(
            f"Species ({rec.get('type', '')}, {size}, Speed {speed} ft.); Traits: {traits}"
        )

    if rtype == "subspecies":
        sp = rec.get("species", {}).get("name", "")
        traits = ", ".join(t["name"] for t in rec.get("traits", []) if isinstance(t, dict))
        dt = rec.get("damage_type", {}).get("name") if isinstance(rec.get("damage_type"), dict) else None
        extra = f" ({dt})" if dt else ""
        return truncate_summary(f"{sp} subspecies{extra}; Traits: {traits}")

    if rtype == "traits":
        sps = ", ".join(s["name"] for s in rec.get("species", []) if isinstance(s, dict))
        lead = first_sentence(rec.get("description", ""), 150)
        return truncate_summary(f"{sps} trait: {lead}" if sps else lead)

    if rtype == "poisons":
        return truncate_summary(
            f"{rec.get('type', '')} poison ({rec.get('cost', '')} gp): {first_sentence(rec.get('description', ''), 140)}"
        )

    if rtype == "languages":
        kind = "Rare language" if rec.get("is_rare") else "Standard language"
        note = str(rec.get("note", "")).strip()
        return truncate_summary(f"{kind}. {note}" if note else f"{kind}.")

    if rtype == "alignments":
        abbr = rec.get("abbreviation", "")
        return truncate_summary(f"Alignment ({abbr}): {first_sentence(rec.get('description', ''), 160)}")

    if rtype == "ability-scores":
        full = rec.get("full_name", name)
        return truncate_summary(f"{full}: {first_sentence(rec.get('description', ''), 165)}")

    if rtype == "skills":
        ab = rec.get("ability_score", {}).get("name", "")
        return truncate_summary(f"{ab} skill: {first_sentence(rec.get('description', ''), 165)}")

    if rtype == "equipment-categories":
        items = rec.get("equipment") or []
        return truncate_summary(f"Equipment category containing {len(items)} items.")

    if rtype == "proficiencies":
        ptype = rec.get("type", "Proficiency")
        ref_name = rec.get("reference", {}).get("name", name)
        cls_cnt = len(rec.get("classes") or [])
        bg_cnt = len(rec.get("backgrounds") or [])
        return truncate_summary(
            f"{ptype} proficiency ({ref_name}); {cls_cnt} classes, {bg_cnt} backgrounds."
        )

    # Simple reference types: conditions, damage-types, magic-schools,
    # weapon-mastery-properties, weapon-properties
    desc = rec.get("description")
    if isinstance(desc, str) and desc.strip():
        return truncate_summary(first_sentence(desc, 195))
    return truncate_summary(f"{name} ({rtype}).")


def render_card_body(
    rtype: str,
    eid: str,
    rec: dict[str, Any],
    url_to_id: dict[str, str],
) -> str:
    """Render token-tight Markdown card body (without YAML frontmatter) per SPEC §7.2."""
    name = str(rec.get("name", rec.get("index", "")))
    lines: list[str] = [f"# {name} (`{eid}`)"]

    if rtype == "spells":
        lvl = rec.get("level", 0)
        school_lnk = _rel_link(rec["school"], url_to_id)
        lvl_text = f"{school_lnk} Cantrip" if lvl == 0 else f"Level {lvl} {school_lnk}"
        tags: list[str] = []
        if rec.get("ritual"):
            tags.append("Ritual")
        if rec.get("concentration"):
            tags.append("Concentration")
        if tags:
            lvl_text += f" ({', '.join(tags)})"
        classes_lnk = _link_list(rec.get("classes"), url_to_id)
        comp = ", ".join(rec.get("components") or [])
        if rec.get("material"):
            comp += f" ({rec['material']})"
        lines.append(
            f"**{lvl_text}** | **Casting Time:** {rec.get('casting_time', '')} | **Range:** {rec.get('range', '')}"
        )
        lines.append(
            f"**Components:** {comp} | **Duration:** {rec.get('duration', '')} | **Classes:** {classes_lnk}"
        )
        extra_meta: list[str] = []
        if rec.get("attack_type"):
            extra_meta.append(f"**Attack:** {rec['attack_type']}")
        dmg = rec.get("damage")
        if isinstance(dmg, dict) and isinstance(dmg.get("damage_type"), dict):
            extra_meta.append(f"**Damage Type:** {_rel_link(dmg['damage_type'], url_to_id)}")
        if extra_meta:
            lines.append(" | ".join(extra_meta))
        lines.append("")
        lines.append(str(rec.get("description", "")).strip())
        if rec.get("higher_level"):
            lines.append("")
            lines.append(f"**Using a Higher-Level Spell Slot.** {str(rec['higher_level']).strip()}")
        if isinstance(dmg, dict) and isinstance(dmg.get("damage_at_slot_level"), dict):
            slots = dmg["damage_at_slot_level"]
            if slots:
                sorted_slots = sorted(slots.items(), key=lambda kv: int(kv[0]))
                lines.append("")
                lines.append("| Slot Level | Damage |")
                lines.append("|---|---|")
                for sl, dice in sorted_slots:
                    lines.append(f"| {sl} | {dice} |")

    elif rtype == "monsters":
        cr = _format_cr(rec.get("challenge_rating", 0))
        xp = rec.get("xp", 0)
        xp_str = f"{xp:,}" if isinstance(xp, int) else str(xp)
        if rec.get("xp_in_lair") is not None:
            lair_xp = rec["xp_in_lair"]
            xp_str += f" ({lair_xp:,} in lair)"
        pb = rec.get("proficiency_bonus", 0)

        ac_parts: list[str] = []
        for ac in rec.get("armor_class") or []:
            if not isinstance(ac, dict):
                continue
            val = str(ac.get("value", ""))
            armors = _link_list(ac.get("armor"), url_to_id)
            ac_parts.append(f"{val} ({armors})" if armors else val)
        ac_str = ", ".join(ac_parts)

        hp_roll = rec.get("hit_points_roll") or rec.get("hit_dice") or ""
        hp_str = f"{rec.get('hit_points', '')} ({hp_roll})" if hp_roll else str(rec.get("hit_points", ""))

        spd = rec.get("speed") or {}
        spd_parts = [f"{k} {v}" if k != "walk" else str(v) for k, v in spd.items()]
        spd_str = ", ".join(spd_parts)

        lines.append(
            f"*{rec.get('size', '')} {rec.get('type', '')}, {rec.get('alignment', '')}* | **CR** {cr} (XP {xp_str}; PB +{pb})"
        )
        lines.append(f"**AC** {ac_str} | **HP** {hp_str} | **Speed** {spd_str}")

        abilities = [
            ("STR", rec.get("strength", 10)),
            ("DEX", rec.get("dexterity", 10)),
            ("CON", rec.get("constitution", 10)),
            ("INT", rec.get("intelligence", 10)),
            ("WIS", rec.get("wisdom", 10)),
            ("CHA", rec.get("charisma", 10)),
        ]
        lines.append(
            " | ".join(f"**{ab}** {val} ({_format_mod(int(val))})" for ab, val in abilities)
        )

        # Saves & skills from proficiencies
        saves: list[str] = []
        skills: list[str] = []
        for p in rec.get("proficiencies") or []:
            if not isinstance(p, dict) or not isinstance(p.get("proficiency"), dict):
                continue
            pref = p["proficiency"]
            val = p.get("value", 0)
            sign = f"+{val}" if isinstance(val, int) and val >= 0 else str(val)
            idx = str(pref.get("index", ""))
            lnk = _rel_link(pref, url_to_id)
            if idx.startswith("saving-throw-"):
                saves.append(f"{lnk} {sign}")
            else:
                skills.append(f"{lnk} {sign}")
        if saves:
            lines.append(f"**Saving Throws:** {', '.join(saves)}")
        if skills:
            lines.append(f"**Skills:** {', '.join(skills)}")

        if rec.get("damage_vulnerabilities"):
            lines.append(f"**Damage Vulnerabilities:** {', '.join(rec['damage_vulnerabilities'])}")
        if rec.get("damage_resistances"):
            lines.append(f"**Damage Resistances:** {', '.join(rec['damage_resistances'])}")
        if rec.get("damage_immunities"):
            lines.append(f"**Damage Immunities:** {', '.join(rec['damage_immunities'])}")
        if rec.get("condition_immunities"):
            ci_parts: list[str] = []
            for ci in rec["condition_immunities"]:
                if isinstance(ci, dict):
                    s = _rel_link(ci, url_to_id)
                    if ci.get("note"):
                        s += f" ({ci['note']})"
                    ci_parts.append(s)
            lines.append(f"**Condition Immunities:** {', '.join(ci_parts)}")

        senses = rec.get("senses") or {}
        if senses:
            sense_parts = [
                f"{k.replace('_', ' ').title()} {v}" for k, v in senses.items()
            ]
            lines.append(f"**Senses:** {', '.join(sense_parts)}")
        langs = str(rec.get("languages", "")).strip() or "None"
        lines.append(f"**Languages:** {langs}")
        if rec.get("gear"):
            lines.append(f"**Gear:** {rec['gear']}")

        for heading, key in (
            ("Traits", "special_abilities"),
            ("Actions", "actions"),
            ("Bonus Actions", "bonus_actions"),
            ("Reactions", "reactions"),
            ("Legendary Actions", "legendary_actions"),
        ):
            block = _render_monster_action_list(heading, rec.get(key) or [], url_to_id)
            if block:
                lines.append("")
                lines.extend(block)

        if rec.get("forms"):
            lines.append("")
            lines.append(f"**Forms:** {_link_list(rec['forms'], url_to_id)}")

    elif rtype == "equipment":
        header: list[str] = []
        cats = _link_list(rec.get("equipment_categories"), url_to_id)
        if cats:
            header.append(f"**Categories:** {cats}")
        cost = rec.get("cost")
        if isinstance(cost, dict):
            header.append(f"**Cost:** {cost.get('quantity', '')} {cost.get('unit', '')}".strip())
        if "weight" in rec and rec["weight"] is not None:
            header.append(f"**Weight:** {rec['weight']} lb.")
        if rec.get("quantity") is not None:
            header.append(f"**Quantity:** {rec['quantity']}")
        if isinstance(rec.get("storage"), dict):
            header.append(f"**Storage:** {_rel_link(rec['storage'], url_to_id)}")
        if header:
            lines.append(" | ".join(header))

        # Weapon stats
        w_parts: list[str] = []
        if isinstance(rec.get("damage"), dict):
            dmg = rec["damage"]
            dt = _rel_link(dmg["damage_type"], url_to_id) if isinstance(dmg.get("damage_type"), dict) else ""
            dmg_s = f"{dmg.get('damage_dice', '')} {dt}".strip()
            if isinstance(rec.get("two_handed_damage"), dict):
                th = rec["two_handed_damage"]
                dmg_s += f" (Two-Handed: {th.get('damage_dice', '')})"
            w_parts.append(f"**Damage:** {dmg_s}")
        if isinstance(rec.get("range"), dict):
            rng = rec["range"]
            r_s = f"{rng.get('normal', '')}/{rng['long']} ft." if rng.get("long") else f"{rng.get('normal', '')} ft."
            w_parts.append(f"**Range:** {r_s}")
        if isinstance(rec.get("throw_range"), dict):
            tr = rec["throw_range"]
            tr_s = f"{tr.get('normal', '')}/{tr['long']} ft." if tr.get("long") else f"{tr.get('normal', '')} ft."
            w_parts.append(f"**Thrown Range:** {tr_s}")
        if isinstance(rec.get("mastery"), dict):
            w_parts.append(f"**Mastery:** {_rel_link(rec['mastery'], url_to_id)}")
        if rec.get("properties"):
            w_parts.append(f"**Properties:** {_link_list(rec['properties'], url_to_id)}")
        if isinstance(rec.get("ammunition"), dict):
            w_parts.append(f"**Ammunition:** {_rel_link(rec['ammunition'], url_to_id)}")
        if isinstance(rec.get("ability"), dict):
            w_parts.append(f"**Ability:** {_rel_link(rec['ability'], url_to_id)}")
        if w_parts:
            lines.append(" | ".join(w_parts))

        # Armor stats
        if isinstance(rec.get("armor_class"), dict):
            ac = rec["armor_class"]
            ac_s = str(ac.get("base", ""))
            if ac.get("dex_bonus"):
                if ac.get("max_bonus") is not None:
                    ac_s += f" + Dex modifier (max {ac['max_bonus']})"
                else:
                    ac_s += " + Dex modifier"
            a_parts = [f"**AC:** {ac_s}"]
            if rec.get("str_minimum"):
                a_parts.append(f"**Strength Min:** {rec['str_minimum']}")
            if rec.get("stealth_disadvantage"):
                a_parts.append("**Stealth:** Disadvantage")
            if rec.get("don_time") or rec.get("doff_time"):
                a_parts.append(f"**Don/Doff:** {rec.get('don_time', '-')}/{rec.get('doff_time', '-')}")
            lines.append(" | ".join(a_parts))

        # Tool stats
        if rec.get("utilize"):
            lines.append("")
            lines.append("## Utilize")
            for u in rec["utilize"]:
                if not isinstance(u, dict):
                    continue
                dc = u.get("dc")
                dc_s = ""
                if isinstance(dc, dict):
                    dc_t = _rel_link(dc["dc_type"], url_to_id) if isinstance(dc.get("dc_type"), dict) else ""
                    dc_s = f" (DC {dc.get('dc_value', '')} {dc_t})".strip()
                    dc_s = f" {dc_s}"
                lines.append(f"- {u.get('name', '')}{dc_s}")
        if rec.get("craft"):
            lines.append(f"**Craft:** {_link_list(rec['craft'], url_to_id)}")

        # Pack contents
        if rec.get("contents"):
            c_parts = []
            for item in rec["contents"]:
                if isinstance(item, dict) and isinstance(item.get("item"), dict):
                    qty = item.get("quantity", 1)
                    lnk = _rel_link(item["item"], url_to_id)
                    c_parts.append(f"{qty}x {lnk}" if qty != 1 else lnk)
            if c_parts:
                lines.append(f"**Contents:** {', '.join(c_parts)}")

        if rec.get("notes"):
            notes = rec["notes"]
            note_s = "; ".join(notes) if isinstance(notes, list) else str(notes)
            lines.append(f"**Notes:** {note_s}")

        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "magic-items":
        cat = _rel_link(rec["equipment_category"], url_to_id)
        rarity = rec.get("rarity", {}).get("name", "")
        att = "Yes" if rec.get("attunement") else "No"
        hdr = [f"**Category:** {cat}", f"**Rarity:** {rarity}", f"**Attunement:** {att}"]
        if rec.get("limited-to"):
            hdr.append(f"**Limited to:** {rec['limited-to']}")
        if rec.get("variant"):
            hdr.append("**Variant:** Yes")
        lines.append(" | ".join(hdr))
        if rec.get("variants"):
            lines.append(f"**Variants:** {_link_list(rec['variants'], url_to_id)}")
        body_desc = _join_magic_item_desc(rec.get("desc") or [])
        if body_desc:
            lines.append("")
            lines.append(body_desc)

    elif rtype == "classes":
        lines.append(
            f"**Hit Die:** d{rec.get('hit_die', '')} | **Saving Throws:** {_link_list(rec.get('saving_throws'), url_to_id)}"
        )
        pa = rec.get("primary_ability")
        if isinstance(pa, dict):
            pa_parts: list[str] = []
            if pa.get("ability_scores"):
                pa_parts.append(_link_list(pa["ability_scores"], url_to_id))
            if pa.get("ability_score_options"):
                opt_s = _format_choice_refs(pa["ability_score_options"], url_to_id)
                if opt_s:
                    pa_parts.append(opt_s)
            if pa_parts:
                lines.append(f"**Primary Ability:** {'; '.join(pa_parts)}")
        if rec.get("subclasses"):
            lines.append(f"**Subclasses:** {_link_list(rec['subclasses'], url_to_id)}")
        if rec.get("proficiencies"):
            lines.append(f"**Proficiencies:** {_link_list(rec['proficiencies'], url_to_id)}")
        if rec.get("proficiency_choices"):
            lines.append("")
            lines.append("## Proficiency Choices")
            for ch in rec["proficiency_choices"]:
                s = _format_choice_refs(ch, url_to_id)
                if s:
                    lines.append(f"- {s}")
        if rec.get("starting_equipment_options"):
            lines.append("")
            lines.append("## Starting Equipment")
            for ch in rec["starting_equipment_options"]:
                s = _format_choice_refs(ch, url_to_id)
                if s:
                    lines.append(f"- {s}")
        mc = rec.get("multi_classing")
        if isinstance(mc, dict):
            mc_parts: list[str] = []
            for p in mc.get("prerequisites") or []:
                if isinstance(p, dict) and isinstance(p.get("ability_score"), dict):
                    mc_parts.append(
                        f"{_rel_link(p['ability_score'], url_to_id)} {p.get('minimum_score', '')}+"
                    )
            if mc.get("prerequisite_options"):
                s = _format_choice_refs(mc["prerequisite_options"], url_to_id)
                if s:
                    mc_parts.append(s)
            mc_profs = _link_list(mc.get("proficiencies"), url_to_id)
            mc_line = []
            if mc_parts:
                mc_line.append(f"Prerequisites: {', '.join(mc_parts)}")
            if mc_profs:
                mc_line.append(f"Proficiencies: {mc_profs}")
            for ch in mc.get("proficiency_choices") or []:
                s = _format_choice_refs(ch, url_to_id)
                if s:
                    mc_line.append(f"Choice: {s}")
            if mc_line:
                lines.append("")
                lines.append("## Multiclassing")
                for item in mc_line:
                    lines.append(f"- {item}")
        sc = rec.get("spellcasting")
        if isinstance(sc, dict):
            ab = _rel_link(sc["spellcasting_ability"], url_to_id) if isinstance(sc.get("spellcasting_ability"), dict) else ""
            lines.append("")
            lines.append(f"## Spellcasting ({ab})" if ab else "## Spellcasting")
            for info in sc.get("info") or []:
                if isinstance(info, dict):
                    iname = info.get("name", "")
                    idesc = info.get("desc", "")
                    if isinstance(idesc, list):
                        idesc = " ".join(str(x) for x in idesc)
                    lines.append(f"- **{iname}:** {str(idesc).strip()}")

    elif rtype == "subclasses":
        cls_lnk = _rel_link(rec["class"], url_to_id)
        lines.append(f"**Class:** {cls_lnk}")
        if rec.get("summary"):
            lines.append(f"*{str(rec['summary']).strip()}*")
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())
        feats = rec.get("features") or []
        if feats:
            lines.append("")
            lines.append("## Subclass Features")
            by_lvl: dict[int, list[dict[str, Any]]] = {}
            for ft in feats:
                if isinstance(ft, dict):
                    by_lvl.setdefault(int(ft.get("level", 0)), []).append(ft)
            for lvl in sorted(by_lvl):
                for ft in by_lvl[lvl]:
                    lines.append(f"### Level {lvl}: {ft.get('name', '')}")
                    lines.append(str(ft.get("description", "")).strip())

    elif rtype == "levels":
        hdr = [f"**Class:** {_rel_link(rec['class'], url_to_id)}", f"**Level:** {rec.get('level', '')}"]
        if isinstance(rec.get("subclass"), dict):
            hdr.insert(1, f"**Subclass:** {_rel_link(rec['subclass'], url_to_id)}")
        if rec.get("prof_bonus") is not None:
            hdr.append(f"**Proficiency Bonus:** +{rec['prof_bonus']}")
        lines.append(" | ".join(hdr))
        if rec.get("features"):
            lines.append(f"**Features:** {_link_list(rec['features'], url_to_id)}")
        if isinstance(rec.get("spellcasting"), dict) and rec["spellcasting"]:
            sc_items = [f"{k.replace('_', ' ')}: {v}" for k, v in rec["spellcasting"].items()]
            lines.append(f"**Spellcasting:** {', '.join(sc_items)}")
        if isinstance(rec.get("class_specific"), dict) and rec["class_specific"]:
            cs_items: list[str] = []
            for k, v in rec["class_specific"].items():
                if isinstance(v, dict):
                    sub_v = ", ".join(f"{sk}={sv}" for sk, sv in v.items())
                    cs_items.append(f"{k.replace('_', ' ')}: {sub_v}")
                else:
                    cs_items.append(f"{k.replace('_', ' ')}: {v}")
            lines.append(f"**Class Specific:** {', '.join(cs_items)}")

    elif rtype == "features":
        hdr = [f"**Class:** {_rel_link(rec['class'], url_to_id)}"]
        if isinstance(rec.get("subclass"), dict):
            hdr.append(f"**Subclass:** {_rel_link(rec['subclass'], url_to_id)}")
        if isinstance(rec.get("level"), dict):
            hdr.append(f"**Level:** {_rel_link(rec['level'], url_to_id)}")
        lines.append(" | ".join(hdr))
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "species":
        size_s = rec.get("size")
        if not size_s and isinstance(rec.get("size_options"), dict):
            size_s = rec["size_options"].get("desc") or "Small or Medium"
        lines.append(
            f"**Creature Type:** {rec.get('type', '')} | **Size:** {size_s or ''} | **Speed:** {rec.get('speed', '')} ft."
        )
        if rec.get("traits"):
            lines.append(f"**Traits:** {_link_list(rec['traits'], url_to_id)}")
        if rec.get("subspecies"):
            lines.append(f"**Subspecies:** {_link_list(rec['subspecies'], url_to_id)}")

    elif rtype == "subspecies":
        hdr = [f"**Species:** {_rel_link(rec['species'], url_to_id)}"]
        if isinstance(rec.get("damage_type"), dict):
            hdr.append(f"**Damage Type:** {_rel_link(rec['damage_type'], url_to_id)}")
        lines.append(" | ".join(hdr))
        if rec.get("traits"):
            t_parts = []
            for t in rec["traits"]:
                if isinstance(t, dict):
                    lnk = _rel_link(t, url_to_id)
                    lvl = t.get("level")
                    t_parts.append(f"{lnk} (Level {lvl})" if lvl is not None else lnk)
            lines.append(f"**Traits:** {', '.join(t_parts)}")

    elif rtype == "traits":
        hdr: list[str] = []
        if rec.get("species"):
            hdr.append(f"**Species:** {_link_list(rec['species'], url_to_id)}")
        if rec.get("subspecies"):
            hdr.append(f"**Subspecies:** {_link_list(rec['subspecies'], url_to_id)}")
        if rec.get("speed") is not None:
            hdr.append(f"**Speed:** {rec['speed']} ft.")
        if hdr:
            lines.append(" | ".join(hdr))
        if rec.get("spells"):
            sp_parts = []
            for entry in rec["spells"]:
                if isinstance(entry, dict) and isinstance(entry.get("spell"), dict):
                    s = _rel_link(entry["spell"], url_to_id)
                    meta = []
                    if entry.get("level") is not None:
                        meta.append(f"level {entry['level']}")
                    if entry.get("uses"):
                        meta.append(f"uses: {entry['uses']}")
                    if entry.get("recovery"):
                        meta.append(f"recovery: {entry['recovery']}")
                    if meta:
                        s += f" ({', '.join(meta)})"
                    sp_parts.append(s)
            if sp_parts:
                lines.append(f"**Spells:** {', '.join(sp_parts)}")
        if rec.get("proficiency_choices"):
            pc = _format_choice_refs(rec["proficiency_choices"], url_to_id)
            if pc:
                lines.append(f"**Proficiency Choice:** {pc}")
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "backgrounds":
        lines.append(f"**Ability Scores:** {_link_list(rec.get('ability_scores'), url_to_id)}")
        if isinstance(rec.get("feat"), dict):
            f_lnk = _rel_link(rec["feat"], url_to_id)
            if rec["feat"].get("note"):
                f_lnk += f" ({rec['feat']['note']})"
            lines.append(f"**Feat:** {f_lnk}")
        if rec.get("proficiencies"):
            lines.append(f"**Proficiencies:** {_link_list(rec['proficiencies'], url_to_id)}")
        for ch in rec.get("proficiency_choices") or []:
            s = _format_choice_refs(ch, url_to_id)
            if s:
                lines.append(f"**Proficiency Choice:** {s}")
        if rec.get("equipment_options"):
            lines.append("")
            lines.append("## Equipment Options")
            for ch in rec["equipment_options"]:
                s = _format_choice_refs(ch, url_to_id)
                if s:
                    lines.append(f"- {s}")

    elif rtype == "feats":
        ftype = str(rec.get("type", "")).replace("-", " ").title()
        hdr = [f"**Category:** {ftype}"]
        prereq = _format_prereqs(rec, url_to_id)
        if prereq:
            hdr.append(f"**Prerequisite:** {prereq}")
        if rec.get("repeatable"):
            hdr.append(f"**Repeatable:** {rec['repeatable']}")
        lines.append(" | ".join(hdr))
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "equipment-categories":
        items = rec.get("equipment") or []
        lines.append(f"**Items ({len(items)}):** {_link_list(items, url_to_id)}")

    elif rtype == "proficiencies":
        hdr = [f"**Type:** {rec.get('type', '')}"]
        if isinstance(rec.get("reference"), dict):
            hdr.append(f"**Reference:** {_rel_link(rec['reference'], url_to_id)}")
        lines.append(" | ".join(hdr))
        if rec.get("classes"):
            lines.append(f"**Classes:** {_link_list(rec['classes'], url_to_id)}")
        if rec.get("backgrounds"):
            lines.append(f"**Backgrounds:** {_link_list(rec['backgrounds'], url_to_id)}")

    elif rtype == "ability-scores":
        hdr = [f"**Full Name:** {rec.get('full_name', name)}"]
        if rec.get("skills"):
            hdr.append(f"**Skills:** {_link_list(rec['skills'], url_to_id)}")
        lines.append(" | ".join(hdr))
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "alignments":
        lines.append(f"**Abbreviation:** {rec.get('abbreviation', '')}")
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "skills":
        lines.append(f"**Ability:** {_rel_link(rec['ability_score'], url_to_id)}")
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "poisons":
        lines.append(f"**Type:** {rec.get('type', '')} | **Cost:** {rec.get('cost', '')} gp")
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    elif rtype == "languages":
        kind = "Rare" if rec.get("is_rare") else "Standard"
        lines.append(f"**Rarity:** {kind}")
        note = str(rec.get("note", "")).strip()
        if note:
            lines.append("")
            lines.append(note)

    else:
        # conditions, damage-types, magic-schools, weapon-mastery-properties, weapon-properties
        if rec.get("description"):
            lines.append("")
            lines.append(str(rec["description"]).strip())

    body = "\n".join(lines).rstrip() + "\n"
    return body


def render_full_card(
    eid: str,
    rtype: str,
    name: str,
    summary: str,
    refs: list[str],
    card_body_md: str,
) -> str:
    """Wrap card_body_md with deterministic pure-stdlib YAML frontmatter (SPEC §7.2)."""
    fm_lines = [
        "---",
        f"id: {eid}",
        f"type: {rtype}",
        f"name: {yaml_quote(name)}",
        f"summary: {yaml_quote(summary)}",
    ]
    if refs:
        fm_lines.append("refs:")
        for r in refs:
            fm_lines.append(f"  - {r}")
    else:
        fm_lines.append("refs: []")
    fm_lines.append("---")
    frontmatter = "\n".join(fm_lines)
    return f"{frontmatter}\n{card_body_md}"


def render_cards_index(
    resource_types: list[dict[str, Any]],
    total_entities: int,
    attribution: str,
) -> str:
    """Render dist/cards/INDEX.md catalog + progressive-disclosure guide + CC-BY-4.0 attribution."""
    lines = [
        "# 5e 2024 (SRD 5.2) Compiled Cards Index",
        "",
        f"Compiled offline catalog of **{total_entities:,}** entities across **{len(resource_types)}** resource types.",
        "",
        "## Progressive Disclosure Guide",
        "1. Start here or with `srd list` / `srd search` to locate canonical `<type>:<slug>` IDs without loading large rule blocks.",
        "2. Open `dist/cards/<type>/<slug>.md` for a single entity, or call `srd get <type>:<slug> --depth 1` for a token-budgeted resolved bundle.",
        "3. Follow relative `[Name](../<type>/<slug>.md)` cross-reference links or inspect `refs:` in YAML frontmatter to resolve mechanical dependencies.",
        "",
        "## Resource Catalog",
        "",
        "| Type | Count | Card Path | Filterable Facets |",
        "|---|---:|---|---|",
    ]
    for rt in resource_types:
        rtype = rt["type"]
        count = rt["count"]
        fkeys = ", ".join(f"`{k}`" for k in rt["facet_keys"]) if rt["facet_keys"] else "*(none)*"
        lines.append(f"| `{rtype}` | {count} | `{rtype}/<slug>.md` | {fkeys} |")
    lines.extend(
        [
            "",
            "## Attribution (CC-BY-4.0)",
            "",
            attribution,
            "",
        ]
    )
    return "\n".join(lines)
