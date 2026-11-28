"""Independent SRD 5.2 rules audit of arena MatchTranscripts.

Re-derives every roll in a trace from its events and checks it against the 2024 rules, keeping its own record of HP,
conditions, death saves, concentration and AC (Shield, Mage Armor). It reads only the trace (initial sheets and
events), never the engine, so it is a second opinion rather than a re-run. It prints, per rule, how many checks ran
and how many failed, so a clean result also shows what was actually exercised.

Usage:
    bin/srd-arena run crypt-of-ghouls --seed 8 --out-trace /tmp/crypt-8.json
    python3 scripts/audit_arena_trace.py /tmp/crypt-*.json

Exit code 1 if any rule has a violation. Reactions are checked: Opportunity Attacks (#38) and Shield's trigger (#33,
#41). Monster traits (#39): Stench saves, Legendary Resistance, Undead Fortitude and Parry are checked.

Condition durations are keyed by source. Ghoul Claw paralysis ("ends at the end of the target's next turn") is
only checked for a Claw rider. Hold Person paralysis is a concentration condition: it ends when the caster's
concentration ends (a failed concentration save, incapacitation, death, or a new concentration spell all emit
`concentration_ended`) or when the target succeeds on the end-of-turn save. The trace often has no
`condition_ended` for the concentration case, because the engine drops those conditions inside
`_drop_concentration`. This audit drops them itself when concentration ends, and flags an attack that still
treats the target as paralyzed after that. Not covered: ranges and cover, and spells other than
Fireball/Healing Word/Mass Healing Word/Bless/Shield/Mage Armor/Hold Person's paralysis.
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

V = collections.defaultdict(list)  # rule -> list of (file, action seq, detail)
N = collections.Counter()  # rule -> number of checks made


def check(rule: str, ok: bool, where: tuple, detail: str = "") -> None:
    N[rule] += 1
    if not ok:
        V[rule].append((*where, detail))


def dice_range(expr: str, crit: bool = False) -> tuple[int, int]:
    """'2d6+3' -> (min, max); crit doubles the dice, not the modifier."""
    lo = hi = 0
    for sign, n, d, k in re.findall(r"([+-]?)\s*(?:(\d*)d(\d+)|(\d+))", expr.replace(" ", "")):
        s = -1 if sign == "-" else 1
        if d:
            cnt = int(n or 1) * (2 if crit else 1)
            lo += s * cnt
            hi += s * cnt * int(d)
        else:
            lo += s * int(k)
            hi += s * int(k)
    return lo, hi


def audit(path: Path) -> None:
    t = json.loads(path.read_text())
    cs = t["initial_state"]["combatants"]
    cs = cs if isinstance(cs, dict) else {c["id"]: c for c in cs}
    side = {i: c["side"] for i, c in cs.items()}
    hp = {i: c["hp"] for i, c in cs.items()}
    hp_max = {i: c["hp_max"] for i, c in cs.items()}
    conds: dict[str, set] = {i: set(c.get("conditions") or []) for i, c in cs.items()}
    conc: dict[str, str | None] = {i: None for i in cs}
    death = {i: [0, 0] for i in cs}  # successes, failures
    # Ghoul Claw (and other on-hit riders) only. Hold Person is tracked separately, below.
    pending_par_end: dict[str, int] = {}  # target -> 1 until its next turn_ended is checked
    # Each entry is {kind: "claw"|"concentration"|"other", caster, spell}.
    par_sources: dict[str, list[dict]] = {i: [] for i in cs}
    # Targets whose Hold Person paralysis has already had to end. A later attack that still
    # lists target_paralyzed is a paralysis that outlived concentration (or the end-of-turn save).
    conc_freed: set[str] = set()
    weapons = {i: {w["slug"]: w for w in (c.get("weapons") or [])} for i, c in cs.items()}
    mactions = {i: {a["name"]: a for a in (c.get("monster_actions") or [])} for i, c in cs.items()}
    spell_dc = {i: (c.get("spellcasting") or {}).get("spell_save_dc") for i, c in cs.items()}
    mod = lambda i, ab: (cs[i].get("ability_mods") or {}).get(ab, 0)
    resist = {i: set(c.get("damage_resistances") or []) for i, c in cs.items()}
    vuln = {i: set(c.get("damage_vulnerabilities") or []) for i, c in cs.items()}
    immune = {i: set(c.get("damage_immunities") or []) for i, c in cs.items()}
    shield_on: dict[str, bool] = {i: False for i in cs}
    base_ac = {i: c["ac"] for i, c in cs.items()}
    pos = {i: dict(c["position"]) for i, c in cs.items()}
    reaction = {i: True for i in cs}
    disengaged: dict[str, bool] = {i: False for i in cs}
    parry_bonus: dict[str, int] = {}
    # Saves made at encounter start (the first creature's turn) aren't in `actions`; their results are in initial_state.
    aura_immune: dict[str, set] = {i: set(c.get("aura_immune_to") or []) for i, c in cs.items()}
    auras = {i: next((t for t in c.get("traits_ir") or [] if t.get("kind") == "start_of_turn_aura_save"), None) for i, c in cs.items()}
    turn_order = list(t["initial_state"].get("turn_order") or cs)

    def reach(i: str) -> int:
        best = 0
        for w in cs[i].get("weapons") or []:
            if str(w.get("weapon_range") or "melee") == "melee":
                best = max(best, int(w.get("reach_ft") or (10 if "reach" in (w.get("properties") or []) else 5)))
        for act in cs[i].get("monster_actions") or []:
            if act.get("kind") == "attack" and act.get("attack_kind") == "melee":
                best = max(best, int(act.get("reach_ft") or 5))
        return best

    def can_react(i: str) -> bool:
        return reaction[i] and hp[i] > 0 and not (conds[i] & {"paralyzed", "unconscious", "incapacitated", "stunned", "petrified", "dead"}) \
            and str(cs[i].get("reaction_policy") or "auto") != "none"

    def _sync_paralyzed(tgt: str) -> None:
        if par_sources.get(tgt):
            conds[tgt].add("paralyzed")
        else:
            conds[tgt].discard("paralyzed")

    def _paralysis_source(evs_local: list, idx_local: int) -> dict:
        """Key a paralysis application by the save or spell that caused it."""
        ev = evs_local[idx_local]
        tgt = ev["target_id"]
        caster = ev.get("source_id")
        for prev in reversed(evs_local[:idx_local]):
            pty = prev.get("type")
            if pty == "saving_throw" and prev.get("target_id") == tgt:
                reason = str(prev.get("reason") or "")
                if reason == "hold-person":
                    return {"kind": "concentration", "caster": caster, "spell": "hold-person"}
                if reason.endswith("_rider"):
                    return {"kind": "claw", "caster": caster, "spell": None}
                return {"kind": "other", "caster": caster, "spell": None}
            if pty == "spell_cast" and prev.get("spell") == "hold-person" and tgt in (prev.get("target_ids") or []):
                return {"kind": "concentration", "caster": prev.get("actor_id") or caster, "spell": "hold-person"}
            if pty in ("attack_roll", "spell_attack_roll") and prev.get("target_id") == tgt:
                break
        return {"kind": "other", "caster": caster, "spell": None}

    def _drop_paralysis(tgt: str, reason: str | None) -> None:
        sources = par_sources.get(tgt) or []
        if not sources:
            conds[tgt].discard("paralyzed")
            return
        prefer = "claw" if reason == "duration_expired" else "concentration" if reason == "save_succeeded" else None
        pick = next((i for i, src in enumerate(sources) if src["kind"] == prefer), 0) if prefer else 0
        removed = sources.pop(pick)
        par_sources[tgt] = sources
        _sync_paralyzed(tgt)
        if removed["kind"] == "concentration":
            conc_freed.add(tgt)

    def _end_concentration_paralysis(caster: str, spell: str | None) -> None:
        """Hold Person (and any other concentration paralysis) ends when that concentration ends."""
        for tgt, sources in list(par_sources.items()):
            kept = []
            for src in sources:
                same_caster = src.get("caster") == caster
                same_spell = spell is None or src.get("spell") in (None, spell)
                if src["kind"] == "concentration" and same_caster and same_spell:
                    conc_freed.add(tgt)
                    continue
                kept.append(src)
            par_sources[tgt] = kept
            _sync_paralyzed(tgt)

    cheb = lambda a, b: max(abs(a["x"] - b["x"]), abs(a["y"] - b["y"]))
    fireball_raw = None

    for a in t["actions"]:
        where = (path.stem, a["seq"])
        intent = a["intent"]
        evs = a["events"]
        last_attack = None
        attacks_this_action = 0
        for idx, e in enumerate(evs):
            ty = e["type"]

            if ty == "turn_started":
                fireball_raw = None
                reaction[e["actor_id"]] = True
                disengaged[e["actor_id"]] = False
                who = e["actor_id"]
                # The engine checks auras in turn order; two Ghasts make two "Stench" saves, matched in that order.
                aura_saves = iter([x for x in evs[idx + 1:] if x["type"] == "saving_throw" and x["target_id"] == who
                                   and any(a and x.get("reason") == a.get("name") for a in auras.values())])
                for src in turn_order:
                    aura = auras.get(src)
                    if aura and src != who and hp[src] > 0 and "dead" not in conds[src] and hp[who] > 0 \
                            and "dead" not in conds[who] and src not in aura_immune[who] and cheb(pos[src], pos[who]) <= aura["radius_ft"]:
                        sv = next(aura_saves, None)
                        check("starting a turn in a Stench-type aura forces its save", sv is not None and sv["dc"] == aura["dc"]
                              and sv["ability"] == aura["ability"], where, f"{who} starts its turn {cheb(pos[src], pos[who])} ft from {src}")
                        if sv is not None and sv["success"] and aura.get("immune_on_success"):
                            aura_immune[who].add(src)
                who = e["actor_id"]
                if side[who] == "party" and hp[who] == 0 and "dead" not in conds[who] and "stable" not in conds[who]:
                    rest = evs[idx + 1:] + [x for b in t["actions"][t["actions"].index(a) + 1: t["actions"].index(a) + 2] for x in b["events"]]
                    ds = next((x for x in rest[:6] if x["type"] == "death_save" and x["actor_id"] == who), None)
                    check("downed PC rolls a death save at the start of its turn", ds is not None, where, f"{who} at 0 HP, no death save")
            if ty == "turn_ended":
                who = e["actor_id"]
                if who in pending_par_end:
                    pending_par_end[who] -= 1
                    if pending_par_end[who] == 0:
                        nxt = [x for x in evs[max(0, idx - 3): idx + 4] if x["type"] == "condition_ended" and x["target_id"] == who and x["condition"] == "paralyzed"]
                        check("paralysis ends at end of target's next turn", bool(nxt), where, f"{who} still paralyzed after its next turn")
                        del pending_par_end[who]

            if ty in ("attack_roll", "spell_attack_roll"):
                attacks_this_action += 1
                tgt = e["target_id"]
                adv, dis = bool(e.get("advantage_sources")), bool(e.get("disadvantage_sources"))
                mode = "normal" if adv == dis else ("advantage" if adv else "disadvantage")
                if ty == "attack_roll":
                    check("adv/dis mode follows its sources", e["mode"] == mode, where, f"{e['mode']} vs {e.get('advantage_sources')}/{e.get('disadvantage_sources')}")
                rolls = e["rolls"]
                exp_chosen = max(rolls) if e["mode"] == "advantage" else min(rolls) if e["mode"] == "disadvantage" else rolls[0]
                check("d20 count/choice matches mode", e["chosen"] == exp_chosen and len(rolls) == (1 if e["mode"] == "normal" else 2), where, str(e))
                bonus = sum(b.get("delta", 0) for b in e.get("bonus_dice") or [])
                check("attack total = d20 + bonus + extra dice - exhaustion", e["total"] == e["chosen"] + e["attack_bonus"] + bonus - e.get("exhaustion_penalty", 0), where, str(e))
                nat20, nat1 = e["chosen"] == 20, e["chosen"] == 1
                check("nat20/nat1 flags", e.get("nat20", nat20) == nat20 and e.get("nat1", nat1) == nat1, where, str(e))
                exp_hit = nat20 or (not nat1 and e["total"] >= e["target_ac"])
                check("hit iff nat20 or (not nat1 and total >= AC)", e["hit"] == exp_hit, where, f"total {e['total']} vs AC {e['target_ac']} chosen {e['chosen']} hit={e['hit']}")
                # Target AC: base (+5 while Shield is up)
                base = base_ac[tgt] + parry_bonus.pop(tgt, 0)
                check("target AC = sheet AC (+5 Shield, + Parry)", e["target_ac"] in (base, base + 5) and (e["target_ac"] == base + 5) == shield_on[tgt], where,
                      f"{tgt} AC {e['target_ac']} base {base} shield={shield_on[tgt]}")
                # Attack bonus from the sheet
                if ty == "attack_roll" and e.get("action") and e["action"] in mactions[e["actor_id"]]:
                    check("monster attack bonus = stat block", e["attack_bonus"] == mactions[e["actor_id"]][e["action"]]["attack_bonus"], where, str(e))
                if ty == "attack_roll" and e.get("weapon") in weapons[e["actor_id"]]:
                    check("PC weapon attack bonus = sheet", e["attack_bonus"] == weapons[e["actor_id"]][e["weapon"]]["attack_bonus"], where, str(e))
                # Conditions on the target
                tconds = conds[tgt]
                melee = ty == "attack_roll" and (
                    (e.get("action") and mactions[e["actor_id"]].get(e["action"], {}).get("attack_kind") == "melee")
                    or (e.get("weapon") and weapons[e["actor_id"]].get(e["weapon"], {}).get("weapon_range") == "melee" and e.get("weapon") != "javelin"))
                if tconds & {"paralyzed", "unconscious"}:
                    check("attacks vs paralyzed/unconscious have advantage", adv, where, f"{tgt} {sorted(tconds)} sources={e.get('advantage_sources')}")
                    if melee and e["hit"]:
                        check("melee hit vs paralyzed/unconscious within 5 ft is a crit", e["crit"], where, f"{tgt} {sorted(tconds)}")
                elif tgt in conc_freed and "paralyzed" not in tconds:
                    # Concentration (or the end-of-turn save) already ended Hold Person. An attack that
                    # still cites target_paralyzed means the paralysis outlived that end.
                    still = "target_paralyzed" in (e.get("advantage_sources") or [])
                    check(
                        "Hold Person paralysis ends when concentration ends",
                        not still,
                        where,
                        f"{tgt} still treated as paralyzed after concentration ended",
                    )
                if not nat20 and e.get("crit"):
                    check("non-20 crit only vs paralyzed/unconscious in melee", bool(tconds & {"paralyzed", "unconscious"}) and melee, where, f"{tgt} {sorted(tconds)}")
                last_attack = e

            if ty == "damage_applied":
                tgt = e["target_id"]
                for part in e["breakdown"]:
                    raw, eff, dtype = part["raw"], part["effective"], part["type"]
                    exp = 0 if dtype in immune[tgt] else raw // 2 if dtype in resist[tgt] else raw * 2 if dtype in vuln[tgt] else raw
                    check("resistance/vulnerability/immunity applied", eff == exp, where, f"{tgt} {dtype} raw {raw} -> {eff} (expected {exp})")
                # Damage dice range for the attack that caused it
                if last_attack and last_attack["hit"] and last_attack["target_id"] == tgt and e.get("source_id") == last_attack["actor_id"]:
                    crit = last_attack["crit"]
                    act = mactions[last_attack["actor_id"]].get(last_attack.get("action") or "")
                    if act:
                        by_type = collections.defaultdict(lambda: [0, 0])
                        for p in act["damage_parts"]:
                            if p.get("conditional"):
                                continue
                            lo, hi = dice_range(p["dice"], crit)
                            by_type[p["type"]][0] += lo; by_type[p["type"]][1] += hi
                        for part in e["breakdown"]:
                            lo, hi = by_type.get(part["type"], (None, None))
                            check("monster damage within dice range (crit doubles dice)", lo is not None and lo <= part["raw"] <= hi, where,
                                  f"{last_attack['actor_id']} {last_attack.get('action')} {part['type']} raw {part['raw']} not in [{lo},{hi}] crit={crit}")
                    w = weapons[last_attack["actor_id"]].get(last_attack.get("weapon") or "")
                    if w and len(e["breakdown"]) == 1:
                        dice = w["damage_dice"]
                        lo, hi = dice_range(dice, crit)
                        lo2, hi2 = (dice_range(w["versatile_dice"], crit) if w.get("versatile_dice") else (lo, hi))
                        b = w["damage_bonus"]
                        raw = e["breakdown"][0]["raw"]
                        check("PC weapon damage within dice + mod", min(lo, lo2) + b <= raw <= max(hi, hi2) + b or raw == max(1, raw), where,
                              f"{last_attack['actor_id']} {w['slug']} raw {raw} not in [{min(lo, lo2) + b},{max(hi, hi2) + b}] crit={crit}")
                    last_attack = None
                # Fireball: one roll for everyone, half on a successful save
                if intent.get("spell") == "fireball" and e.get("source_id") == intent["actor_id"]:
                    raw = e["breakdown"][0]["raw"]
                    lo, hi = dice_range("8d6")
                    sv = next((x for x in reversed(evs[:idx]) if x["type"] == "saving_throw" and x["target_id"] == tgt and x.get("reason") == "fireball"), None)
                    if sv:
                        full = raw * 2 if sv["success"] else raw
                        if fireball_raw is None:
                            fireball_raw = full
                        check("fireball 8d6 at slot 3", lo <= full <= hi + 1, where, f"full {full}")
                        check("fireball: one damage roll for all targets", abs(full - fireball_raw) <= 1, where, f"{full} vs {fireball_raw}")
                        check("fireball DEX save DC = caster spell DC", sv["dc"] == spell_dc[intent["actor_id"]] and sv["ability"] == "dex", where, str(sv))
                # Concentration save after damage to a concentrating caster
                if conc[tgt] and e["total_damage"] > 0 and hp[tgt] - e["hp_damage"] > 0:
                    sv = next((x for x in evs[idx + 1: idx + 4] if x["type"] == "saving_throw" and x["target_id"] == tgt and x.get("reason") == "concentration"), None)
                    check("damage to a concentrating caster triggers a CON save", sv is not None, where, f"{tgt} took {e['total_damage']} while concentrating on {conc[tgt]}")
                    if sv:
                        check("concentration DC = max(10, half damage)", sv["dc"] == min(30, max(10, e["total_damage"] // 2)) and sv["ability"] == "con", where,
                              f"dc {sv['dc']} for {e['total_damage']} damage")
                check("hp_before matches running HP", e["hp_before"] == hp[tgt], where, f"{tgt} {e['hp_before']} vs {hp[tgt]}")
                if e.get("undead_fortitude"):
                    check("Undead Fortitude leaves exactly 1 HP after a passed save", e["hp_after"] == 1 and any(
                        x["type"] == "saving_throw" and x.get("reason") == "Undead Fortitude" and x["success"] for x in evs[:idx]),
                        where, str(e))
                else:
                    check("hp_after = max(0, before - hp_damage)", e["hp_after"] == max(0, e["hp_before"] - e["hp_damage"]), where, str(e))
                # Damage at 0 HP: a death-save failure (2 on a crit); massive damage kills outright
                if side[tgt] == "party" and e["hp_before"] == 0 and e["hp_damage"] > 0:
                    nxt = next((x for x in evs[idx + 1: idx + 4] if x["type"] in ("death_save_damage_failure", "combatant_died") and x.get("target_id", x.get("combatant_id")) == tgt), None)
                    check("damage at 0 HP adds death-save failures", nxt is not None, where, f"{tgt}")
                    died0 = any(x["type"] == "combatant_died" and x.get("combatant_id") == tgt for x in evs[idx + 1: idx + 4])
                    check("damage at 0 HP >= max HP kills outright", died0 or e["hp_damage"] < hp_max[tgt], where,
                          f"{tgt} took {e['hp_damage']} at 0 HP, max {hp_max[tgt]}")
                hp[tgt] = e["hp_after"]
                if side[tgt] == "party" and e["hp_before"] > 0 and e["hp_after"] == 0:
                    overflow = e["hp_damage"] - e["hp_before"]
                    died = any(x["type"] == "combatant_died" and x.get("combatant_id") == tgt for x in evs[idx + 1: idx + 4])
                    check("massive damage (overflow >= max HP) kills; otherwise unconscious", died == (overflow >= hp_max[tgt]), where, f"{tgt} overflow {overflow} max {hp_max[tgt]} died={died}")

            if ty == "death_save_damage_failure":
                tgt = e["target_id"]
                death[tgt][1] += e["failures_added"]
                check("death-save failures running total", e["failures_total"] == min(3, death[tgt][1]), where, f"{tgt} {e} vs {death[tgt]}")

            if ty == "death_save":
                who = e["actor_id"]
                r = e["roll"]
                s, f = death[who]
                if r == 20:
                    exp = "revived"
                    hp[who] = 1  # natural 20: regains 1 HP (no healing event)
                    death[who] = [0, 0]
                    conds[who] -= {"unconscious", "stable"}
                elif r == 1:
                    f += 2; exp = "failure"
                elif r >= 10:
                    s += 1; exp = "success"
                else:
                    f += 1; exp = "failure"
                check("death save outcome by roll (1 = two failures, 20 = 1 HP)", e["outcome"].startswith(exp[:4]) or (r == 20 and e["outcome"] in ("revived", "regain_hp", "critical_success", "nat20_revive")) or (r == 1 and e["outcome"] == "nat1_two_failures"), where, f"roll {r} -> {e['outcome']}")
                if r != 20:
                    death[who] = [s, f]
                    check("death save counters", (e["successes"], e["failures"]) == (min(3, s), min(3, f)), where, f"{e} expected {(s, f)}")
                    if f >= 3:
                        check("3 failures -> dies", any(x["type"] == "combatant_died" and x["combatant_id"] == who for x in evs[idx + 1: idx + 4]), where, who)
                    if s >= 3:
                        check("3 successes -> stable", any(x["type"] == "pc_stabilized" and x["combatant_id"] == who for x in evs[idx + 1: idx + 4]), where, who)

            if ty == "healing_applied":
                tgt = e["target_id"]
                check("healing hp_before matches running HP", e["hp_before"] == hp[tgt], where, f"{tgt} {e['hp_before']} vs {hp[tgt]}")
                check("healing capped at max HP", e["hp_after"] == min(hp_max[tgt], e["hp_before"] + e["amount"]), where, str(e))
                hp[tgt] = e["hp_after"]
                if e["hp_before"] == 0 and e["hp_after"] > 0:
                    death[tgt] = [0, 0]
                    conds[tgt] -= {"unconscious", "stable"}

            if ty == "saving_throw":
                tgt = e["target_id"]
                b = sum(x.get("delta", 0) for x in e.get("bonus_dice") or [])
                if not e.get("auto_fail"):
                    check("save total = d20 + mod + extra dice - exhaustion", e["total"] == e["chosen"] + e["modifier"] + b - e.get("exhaustion_penalty", 0), where, str(e))
                    if e.get("legendary_resistance"):
                        check("Legendary Resistance only turns a failed save into a success", e["success"] and e["total"] < e["dc"]
                              and any(x["type"] == "legendary_resistance_used" and x["target_id"] == tgt for x in evs[idx + 1: idx + 3]),
                              where, str(e))
                    else:
                        check("save success iff total >= DC", e["success"] == (e["total"] >= e["dc"]), where, str(e))
                    check("save modifier = sheet save bonus", e["modifier"] == cs[tgt]["saves"][e["ability"]], where, f"{tgt} {e['ability']} {e['modifier']} vs {cs[tgt]['saves'][e['ability']]}")
                if e["ability"] in ("str", "dex") and conds[tgt] & {"paralyzed", "unconscious"}:
                    check("paralyzed/unconscious auto-fail STR/DEX saves", e.get("auto_fail") and not e["success"], where, f"{tgt} {sorted(conds[tgt])}")
                if e.get("reason") == "Claw_rider":
                    check("Claw paralysis save: CON DC 10", e["ability"] == "con" and e["dc"] == 10, where, str(e))
                    par = any(x["type"] == "condition_applied" and x["target_id"] == tgt and x["condition"] == "paralyzed" for x in evs[idx + 1: idx + 3])
                    immune_par = "undead" in str(cs[tgt].get("source_id", "")) or tgt.startswith(("ghoul", "ghast", "skeleton"))
                    check("failed Claw save -> paralyzed (success -> not)", par == (not e["success"] and not immune_par), where, f"{tgt} success={e['success']} paralyzed={par}")
                if e.get("reason") == "end_turn_save_paralyzed" and e.get("success"):
                    while any(src["kind"] == "concentration" for src in par_sources.get(tgt, [])):
                        _drop_paralysis(tgt, "save_succeeded")
                    conc_freed.add(tgt)

            if ty == "action_disengage":
                disengaged[e["actor_id"]] = True
            if ty == "reaction_parry":
                check("Parry needs a free reaction", reaction[e["actor_id"]], where, e["actor_id"])
                reaction[e["actor_id"]] = False
                parry_bonus[e["actor_id"]] = int(e["ac_bonus"])
            if ty == "opportunity_attack":
                rid, mid = e["actor_id"], e["target_id"]
                check("opportunity attack only by a hostile that can react, vs a mover that didn't Disengage",
                      side[rid] != side[mid] and can_react(rid) and not disengaged[mid], where, f"{rid} -> {mid}")
                reaction[rid] = False
            if ty == "moved":
                mover, frm, to = e["actor_id"], e["from_pos"], e["to_pos"]
                expected = sorted(r for r in cs if r != mover and side[r] != side[mover] and can_react(r)
                                  and reach(r) and cheb(pos[r], frm) <= reach(r) < cheb(pos[r], to) and not disengaged[mover])
                # Reactions already resolved in this step (they come before "moved"); reactors that spent theirs
                got = sorted({x["actor_id"] for x in evs[:idx] if x["type"] == "opportunity_attack" and x["target_id"] == mover})
                pending = [r for r in expected if r not in got]
                check("leaving a hostile's reach without Disengage draws its opportunity attack", not pending, where,
                      f"{mover} {frm}->{to}: no attack from {pending}")
                pos[mover] = dict(to)
            if ty == "teleported":
                pos[e["actor_id"]] = dict(e["to_pos"])
            if ty == "mastery_triggered" and e.get("to_pos"):  # Push: forced movement never provokes
                pos[e["target_id"]] = dict(e["to_pos"])

            if ty == "condition_applied":
                tgt = e["target_id"]
                if e["condition"] == "paralyzed":
                    src = _paralysis_source(evs, idx)
                    par_sources.setdefault(tgt, []).append(src)
                    conds[tgt].add("paralyzed")
                    # Only the Claw rider uses "ends at the end of the target's next turn".
                    # Hold Person repeats a save instead, and also ends when concentration ends.
                    if src["kind"] == "claw":
                        pending_par_end[tgt] = 1
                else:
                    conds[tgt].add(e["condition"])
            if ty == "condition_ended":
                if e["condition"] == "paralyzed":
                    _drop_paralysis(e["target_id"], e.get("reason"))
                else:
                    conds[e["target_id"]].discard(e["condition"])
            if ty == "pc_unconscious":
                conds[e["combatant_id"]].add("unconscious")
            if ty == "pc_stabilized":
                conds[e["combatant_id"]].add("stable")
            if ty == "concentration_started":
                conc[e["actor_id"]] = e["spell"]
            if ty == "concentration_ended":
                conc[e["actor_id"]] = None
                # Damage-triggered CON save failure, incapacitation, death, and a new concentration
                # spell all emit this event. Hold Person paralysis ends with it even when the trace
                # has no condition_ended (the engine clears those conditions inside _drop_concentration).
                _end_concentration_paralysis(e["actor_id"], e.get("spell"))
            if ty == "spell_cast" and e["spell"] == "mage-armor":
                for tid in e.get("target_ids") or [e["actor_id"]]:
                    if not cs[tid].get("wearing_armor"):
                        base_ac[tid] = max(base_ac[tid], 13 + mod(tid, "dex"))
            if ty == "spell_cast" and e["spell"] == "shield":
                caster = e["actor_id"]
                nxt = next((x for x in evs[idx + 1:] if x["type"] in ("attack_roll", "spell_attack_roll") and x["target_id"] == caster), None)
                check("Shield is a reaction to being hit (never on the caster's own action)",
                      bool(e.get("as_reaction")) and e.get("trigger") == "hit_by_attack" and a["actor_id"] != caster and nxt is not None,
                      where, f"{caster} cast Shield in {a['actor_id']}'s action, trigger={e.get('trigger')}")
                if nxt is not None:
                    check("Shield only when +5 AC turns the hit into a miss",
                          not nxt.get("nat20") and base_ac[caster] <= nxt["total"] < base_ac[caster] + 5 and not nxt["hit"]
                          and nxt["target_ac"] == base_ac[caster] + 5,
                          where, f"{caster}: total {nxt['total']} vs base AC {base_ac[caster]}, hit={nxt['hit']}")
                check("Shield needs a free reaction", reaction[caster], where, caster)
                reaction[caster] = False
                shield_on[caster] = True
            if ty == "effect_expired" and e.get("kind") == "shield":
                shield_on[e["target_id"]] = False
            if ty in ("combatant_died",):
                conds[e["combatant_id"]].add("dead")

        if intent.get("type") == "attack" and side.get(a["actor_id"]) == "party" and intent.get("weapon"):
            exp = cs[a["actor_id"]].get("extra_attacks", 1)
            check("PC Attack action makes <= its Extra Attack count of attack rolls", attacks_this_action <= exp, where, f"{a['actor_id']} {attacks_this_action} > {exp}")


if __name__ == "__main__":
    files = [Path(p) for p in sys.argv[1:]]
    for f in files:
        audit(f)
    print(f"audited {len(files)} traces, {sum(N.values())} checks across {len(N)} rules\n")
    for rule in sorted(N):
        bad = V.get(rule, [])
        print(f"{'FAIL' if bad else ' ok '}  {N[rule]:5d} checks  {len(bad):4d} bad  {rule}")
    for rule, bad in sorted(V.items()):
        print(f"\n== {rule}: {len(bad)} violation(s); first 4:")
        for b in bad[:4]:
            print("   ", b)
    sys.exit(1 if V else 0)
