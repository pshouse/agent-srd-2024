"""Stage 2 Verification Tests (§7.1): Build-Time Rules IR Compiler.

Covers:
1. Build & Artifacts: bin/srd-rules-build executable, pyproject.toml scripts,
   dist/rules.json & dist/web/rules-coverage.json schema_version == "2",
   matching Stage 1 source_hash, CC-BY-4.0 attribution, sorted keys.
2. Determinism: non-destructive temp-dir rebuilds via bin/srd-rules-build
   are byte-identical to each other and to dist/rules.json & dist/web/rules-coverage.json.
3. Entity Completeness & Honest Coverage Floors across all domains:
   - weapon_masteries (8/8 full)
   - conditions (15/15 full, including 2024 exhaustion)
   - equipment (182 total, 38 weapons full, 13 armor/shields full, quirks)
   - spells (339 total, full >= 80, full+partial >= 200, narrative >= 50,
     honesty checks, 9 Component: range quirks, inline upcast scaling)
   - monsters (341 total, full >= 220, narrative == 0, shrieker-fungus,
     vampire-mist, adult-red-dragon, goblin-warrior, ghoul)
   - character_creation (classes, subclasses, species, subspecies,
     backgrounds, feats, levels, traits)
"""

from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from srd2024.compiler.build import SRD_ATTRIBUTION
from tests.helpers import (
    DIST_DIR,
    MANIFEST_PATH,
    PYPROJECT_PATH,
    REPO_ROOT,
    WEB_DIR,
    load_provenance,
)

BIN_RULES_BUILD = REPO_ROOT / "bin" / "srd-rules-build"
RULES_PATH = DIST_DIR / "rules.json"
COVERAGE_PATH = WEB_DIR / "rules-coverage.json"

EXPECTED_WEAPON_MASTERIES = {
    "cleave",
    "graze",
    "nick",
    "push",
    "sap",
    "slow",
    "topple",
    "vex",
}

EXPECTED_CONDITIONS = {
    "blinded",
    "charmed",
    "deafened",
    "exhaustion",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
}

REQUIRED_FULL_SPELLS = {
    "fireball",
    "lightning-bolt",
    "burning-hands",
    "shatter",
    "magic-missile",
    "scorching-ray",
    "guiding-bolt",
    "fire-bolt",
    "sacred-flame",
    "eldritch-blast",
    "acid-splash",
    "ray-of-frost",
    "chill-touch",
    "vicious-mockery",
    "cure-wounds",
    "healing-word",
    "heal",
    "shield",
    "mage-armor",
    "shield-of-faith",
    "bless",
    "bane",
    "hold-person",
    "misty-step",
    "false-life",
    "longstrider",
}

COMPONENT_RANGE_QUIRK_SPELLS = {
    "contagion",
    "divine-smite",
    "find-steed",
    "guidance",
    "power-word-heal",
    "power-word-kill",
    "resistance",
    "searing-smite",
    "sorcerous-burst",
}

HONEST_NARRATIVE_SPELLS = {
    "augury",
    "clairvoyance",
    "comprehend-languages",
    "prestidigitation",
    "wish",
}


def _load_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _load_coverage() -> dict[str, Any]:
    return json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))


class TestRulesBuildAndArtifacts(unittest.TestCase):
    """§7.1.1: Executable, packaging entries, metadata, schema & attribution."""

    def test_rules_build_executable_and_pyproject(self) -> None:
        self.assertTrue(BIN_RULES_BUILD.is_file(), f"Missing {BIN_RULES_BUILD}")
        mode = BIN_RULES_BUILD.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR), f"Not executable (+x): {BIN_RULES_BUILD}")
        first_line = BIN_RULES_BUILD.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("python3", first_line)

        pyproject_text = PYPROJECT_PATH.read_text(encoding="utf-8")
        for script_entry in (
            'srd-build = "srd2024.compiler.__main__:main"',
            'srd = "srd2024.runtime.cli:main"',
            'srd-mcp = "srd2024.runtime.mcp:main"',
            'srd-rules-build = "srd2024.rules_compiler.__main__:main"',
            'srd-engine-mcp = "srd2024.runtime.mcp:main_engine"',
        ):
            self.assertIn(script_entry, pyproject_text)

    def test_rules_json_and_coverage_manifest_schema_and_provenance(self) -> None:
        self.assertTrue(RULES_PATH.is_file(), "dist/rules.json missing")
        self.assertTrue(COVERAGE_PATH.is_file(), "dist/web/rules-coverage.json missing")

        raw_rules_text = RULES_PATH.read_text(encoding="utf-8")
        raw_cov_text = COVERAGE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("\r\n", raw_rules_text, "dist/rules.json must use LF line endings")
        self.assertNotIn("\r\n", raw_cov_text, "dist/web/rules-coverage.json must use LF line endings")

        rules = json.loads(raw_rules_text)
        cov = json.loads(raw_cov_text)
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        prov = load_provenance()

        # Top-level metadata
        self.assertEqual(rules["schema_version"], "2")
        self.assertEqual(rules["dataset_id"], "srd-2024-5.2")
        self.assertEqual(rules["source_hash"], manifest["source_hash"])
        self.assertEqual(rules["upstream"]["repo"], prov["upstream_repo"])
        self.assertEqual(rules["upstream"]["commit"], prov["upstream_commit"])
        self.assertEqual(rules["upstream"]["database_version"], prov["upstream_database_version"])
        self.assertEqual(rules["license"]["content"], "CC-BY-4.0")
        self.assertEqual(rules["license"]["content_url"], prov["content_license"]["url"])
        self.assertEqual(rules["license"]["attribution"], SRD_ATTRIBUTION)

        # Sorted keys on top-level and all domain dicts
        self.assertEqual(list(rules.keys()), sorted(rules.keys()))
        for domain in ("weapon_masteries", "conditions", "equipment", "spells", "monsters"):
            self.assertEqual(list(rules[domain].keys()), sorted(rules[domain].keys()), f"Unsorted keys in {domain}")

        # Coverage manifest matches rules.json
        self.assertEqual(cov["schema_version"], "2")
        self.assertEqual(cov["dataset_id"], "srd-2024-5.2")
        self.assertEqual(cov["source_hash"], manifest["source_hash"])
        self.assertEqual(cov["summary"], rules["coverage_summary"])

        summary = cov["summary"]
        self.assertEqual(summary["total_compiled"], 8 + 15 + 182 + 339 + 341)
        self.assertEqual(summary["full"] + summary["partial"] + summary["narrative"], summary["total_compiled"])
        expected_pct = round(100.0 * (summary["full"] + summary["partial"]) / summary["total_compiled"], 1)
        self.assertEqual(summary["executable_pct"], expected_pct)

        for domain in ("weapon_masteries", "conditions", "equipment", "spells", "monsters"):
            self.assertIn(domain, summary["by_domain"])
            self.assertIn(domain, cov["entities"])
            self.assertEqual(len(cov["entities"][domain]), len(rules[domain]))


class TestRulesDeterminism(unittest.TestCase):
    """§7.1.2: Non-destructive temp-dir rebuilds produce byte-identical artifacts."""

    def test_rules_build_is_byte_identical_across_temp_rebuilds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="srd2024-rules-det-") as tmp:
            out1 = Path(tmp) / "run1"
            out2 = Path(tmp) / "run2"
            for out_dir in (out1, out2):
                proc = subprocess.run(
                    [str(BIN_RULES_BUILD), "--out-dir", str(out_dir)],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"bin/srd-rules-build failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
                )

            r1 = (out1 / "rules.json").read_bytes()
            r2 = (out2 / "rules.json").read_bytes()
            c1 = (out1 / "web" / "rules-coverage.json").read_bytes()
            c2 = (out2 / "web" / "rules-coverage.json").read_bytes()

            self.assertEqual(r1, r2, "Two temp rebuilds produced different rules.json bytes")
            self.assertEqual(c1, c2, "Two temp rebuilds produced different rules-coverage.json bytes")
            self.assertEqual(r1, RULES_PATH.read_bytes(), "Temp rebuild rules.json differs from dist/rules.json")
            self.assertEqual(
                c1,
                COVERAGE_PATH.read_bytes(),
                "Temp rebuild rules-coverage.json differs from dist/web/rules-coverage.json",
            )


class TestRulesCompletenessAndCoverage(unittest.TestCase):
    """§7.1.3: Entity completeness, provenance discipline, and honest coverage floors."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = _load_rules()

    def test_all_compiled_entities_carry_honest_coverage_and_provenance(self) -> None:
        valid_coverages = {"full", "partial", "narrative"}
        valid_parsers = {"structured", "regex+structured", "template+structured"}

        for domain in ("weapon_masteries", "conditions", "equipment", "spells", "monsters"):
            entities = self.rules[domain]
            for slug, ent in entities.items():
                cov = ent.get("coverage")
                self.assertIn(cov, valid_coverages, f"{domain}:{slug} invalid coverage {cov!r}")
                prov = ent.get("provenance")
                self.assertIsInstance(prov, dict, f"{domain}:{slug} missing provenance")
                self.assertTrue(prov.get("source_id"), f"{domain}:{slug} missing provenance.source_id")
                self.assertTrue(prov.get("source_file"), f"{domain}:{slug} missing provenance.source_file")
                self.assertIn(prov.get("parser"), valid_parsers, f"{domain}:{slug} invalid parser {prov.get('parser')!r}")
                self.assertIsInstance(prov.get("unmodeled_clauses"), list)
                if cov == "full":
                    self.assertEqual(
                        prov["unmodeled_clauses"],
                        [],
                        f"{domain}:{slug} marked full but has unmodeled_clauses={prov['unmodeled_clauses']}",
                    )

    def test_weapon_masteries_8_of_8_full(self) -> None:
        wms = self.rules["weapon_masteries"]
        self.assertEqual(set(wms.keys()), EXPECTED_WEAPON_MASTERIES)
        self.assertTrue(all(wm["coverage"] == "full" for wm in wms.values()))

        self.assertEqual(wms["cleave"]["trigger"], "on_hit")
        self.assertTrue(wms["cleave"]["requires_melee"])
        self.assertTrue(wms["cleave"]["once_per_turn"])
        self.assertEqual(wms["cleave"]["effect"]["kind"], "extra_attack")

        self.assertEqual(wms["graze"]["trigger"], "on_miss")
        self.assertEqual(wms["graze"]["effect"]["kind"], "guaranteed_damage")

        self.assertEqual(wms["nick"]["trigger"], "on_light_extra_attack")
        self.assertTrue(wms["nick"]["once_per_turn"])

        self.assertEqual(wms["push"]["trigger"], "on_hit")
        self.assertEqual(wms["push"]["max_target_size"], "Large")
        self.assertEqual(wms["push"]["effect"]["distance_ft"], 10)

        self.assertEqual(wms["sap"]["trigger"], "on_hit")
        self.assertEqual(wms["slow"]["trigger"], "on_hit")
        self.assertTrue(wms["slow"]["requires_damage"])

        self.assertEqual(wms["topple"]["trigger"], "on_hit")
        self.assertEqual(wms["topple"]["effect"]["save_ability"], "con")
        self.assertEqual(wms["topple"]["effect"]["on_fail_condition"], "prone")

        self.assertEqual(wms["vex"]["trigger"], "on_hit")
        self.assertTrue(wms["vex"]["requires_damage"])

    def test_conditions_15_of_15_full_including_2024_exhaustion(self) -> None:
        conds = self.rules["conditions"]
        self.assertEqual(set(conds.keys()), EXPECTED_CONDITIONS)
        self.assertTrue(all(c["coverage"] == "full" for c in conds.values()))

        # 2024 Exhaustion rules (-2 d20 tests/level, -5 ft speed/level, death at 6)
        exh = conds["exhaustion"]["modifiers"]
        self.assertEqual(exh["d20_test_penalty_per_level"], -2)
        self.assertEqual(exh["speed_penalty_ft_per_level"], -5)
        self.assertEqual(exh["death_at_level"], 6)

        # Paralyzed
        para = conds["paralyzed"]
        self.assertIn("incapacitated", para["implies"])
        self.assertTrue(para["modifiers"]["auto_crit_within_5ft"])
        self.assertEqual(set(para["modifiers"]["auto_fail_saves"]), {"str", "dex"})
        self.assertEqual(para["modifiers"]["attacks_against"], "advantage")

        # Petrified
        pet = conds["petrified"]
        self.assertIn("incapacitated", pet["implies"])
        self.assertTrue(pet["modifiers"]["resist_all_damage"])
        self.assertIn("poisoned", pet["modifiers"]["immune_conditions"])

        # Unconscious
        unc = conds["unconscious"]
        self.assertTrue({"incapacitated", "prone"}.issubset(set(unc["implies"])))
        self.assertTrue(unc["modifiers"]["auto_crit_within_5ft"])

        # Prone
        prone = conds["prone"]["modifiers"]
        self.assertEqual(prone["attacks_made"], "disadvantage")
        self.assertEqual(prone["attacks_against_within_5ft"], "advantage")
        self.assertEqual(prone["attacks_against_beyond_5ft"], "disadvantage")

        # Invisible & Restrained
        self.assertEqual(conds["invisible"]["modifiers"]["attacks_made"], "advantage")
        self.assertEqual(conds["invisible"]["modifiers"]["attacks_against"], "disadvantage")
        self.assertTrue(conds["restrained"]["modifiers"]["speed_zero"])
        self.assertIn("dex", conds["restrained"]["modifiers"]["save_disadvantage"])

    def test_equipment_182_weapons_armor_and_quirks(self) -> None:
        eq = self.rules["equipment"]
        self.assertEqual(len(eq), 182)

        weapons = {k: v for k, v in eq.items() if v.get("kind") == "weapon"}
        armors = {k: v for k, v in eq.items() if v.get("kind") in ("armor", "shield")}
        self.assertEqual(len(weapons), 38)
        self.assertEqual(len(armors), 13)
        self.assertTrue(all(w["coverage"] == "full" for w in weapons.values()))
        self.assertTrue(all(a["coverage"] == "full" for a in armors.values()))

        full_cnt = sum(1 for v in eq.values() if v["coverage"] == "full")
        partial_cnt = sum(1 for v in eq.values() if v["coverage"] == "partial")
        self.assertEqual(full_cnt, 51)
        self.assertGreaterEqual(partial_cnt, 40)

        # Weapon quirks: morningstar reach_ft == 5, blowgun damage_dice == "1", all 38 have mastery
        for slug, w in weapons.items():
            self.assertIn(w["mastery"], EXPECTED_WEAPON_MASTERIES, f"Weapon {slug} missing valid mastery")
            self.assertIn(w["attack_ability"], ("str", "dex", "finesse"))
        self.assertEqual(weapons["morningstar"]["reach_ft"], 5)
        self.assertEqual(weapons["blowgun"]["damage_dice"], "1")
        self.assertEqual(weapons["longsword"]["versatile_dice"], "1d10")
        self.assertEqual(weapons["longsword"]["mastery"], "sap")
        self.assertEqual(weapons["rapier"]["attack_ability"], "finesse")
        self.assertEqual(weapons["longbow"]["attack_ability"], "dex")

        # Armor quirks: hide-armor max_bonus == 2, shield +2, plate-armor str_minimum == 15
        self.assertEqual(armors["hide-armor"]["armor_class"]["base"], 12)
        self.assertEqual(armors["hide-armor"]["armor_class"]["max_bonus"], 2)
        self.assertEqual(armors["hide-armor"]["armor_category"], "medium")
        self.assertEqual(armors["shield"]["kind"], "shield")
        self.assertEqual(armors["shield"]["armor_class"]["base"], 2)
        self.assertEqual(armors["chain-mail"]["str_minimum"], 13)
        self.assertEqual(armors["plate-armor"]["str_minimum"], 15)
        self.assertTrue(armors["plate-armor"]["stealth_disadvantage"])

        # Entertainer's pack omits weight cleanly
        self.assertIn("entertainers-pack", eq)
        self.assertIsNone(eq["entertainers-pack"].get("weight"))

    def test_spells_339_coverage_floors_honesty_and_quirks(self) -> None:
        spells = self.rules["spells"]
        self.assertEqual(len(spells), 339)

        full_cnt = sum(1 for s in spells.values() if s["coverage"] == "full")
        partial_cnt = sum(1 for s in spells.values() if s["coverage"] == "partial")
        narrative_cnt = sum(1 for s in spells.values() if s["coverage"] == "narrative")

        # 75, not 80: #51 regraded Acid Arrow, Ice Knife and Vitriolic Sphere to partial (their delayed or
        # secondary damage isn't modeled), which left 78 full.
        self.assertGreaterEqual(full_cnt, 75, f"Expected >= 75 full spells, got {full_cnt}")
        self.assertGreaterEqual(full_cnt + partial_cnt, 200, f"Expected >= 200 executable spells, got {full_cnt + partial_cnt}")
        self.assertGreaterEqual(narrative_cnt, 50, f"Expected >= 50 narrative spells (honesty check), got {narrative_cnt}")

        # All required iconic combat spells are full
        missing_full = sorted(s for s in REQUIRED_FULL_SPELLS if spells[s]["coverage"] != "full")
        self.assertEqual(missing_full, [], f"Iconic spells not marked full: {missing_full}")

        # Honesty checks: pure divination/world-shaping/utility spells must NOT be full
        for slug in HONEST_NARRATIVE_SPELLS:
            self.assertNotEqual(
                spells[slug]["coverage"],
                "full",
                f"Narrative spell {slug!r} must NOT be inflated to coverage='full'",
            )
        for slug in ("augury", "clairvoyance", "wish"):
            self.assertEqual(spells[slug]["coverage"], "narrative", f"Expected {slug} to be narrative")

        # 9 upstream "Component:" range quirks stripped cleanly
        for slug in COMPONENT_RANGE_QUIRK_SPELLS:
            range_raw = spells[slug]["range_raw"]
            self.assertNotIn(
                "Component:",
                range_raw,
                f"Upstream 'Component:' quirk leaked into spells:{slug}.range_raw={range_raw!r}",
            )

        # Spot-check Fireball, Cure Wounds, Shield, Magic Missile, Fire Bolt
        fb = spells["fireball"]
        self.assertEqual(fb["level"], 3)
        self.assertEqual(fb["resolution"], "save")
        self.assertEqual(fb["save"]["ability"], "dex")
        self.assertEqual(fb["save"]["on_success"], "half")
        self.assertEqual(fb["damage"][0]["dice"], "8d6")
        self.assertEqual(fb["damage"][0]["type"], "fire")
        self.assertEqual(fb["scaling"]["slot_damage_per_level"], "1d6")

        cw = spells["cure-wounds"]
        self.assertIsNotNone(cw["healing"])
        self.assertEqual(cw["healing"]["dice"], "2d8")
        self.assertTrue(cw["healing"]["add_spell_mod"])
        self.assertTrue(
            cw["scaling"]["slot_healing_per_level"] in ("2d8", "2d8/level")
            or "2d8" in str(cw["scaling"]["slot_healing_per_level"]),
            f"Expected cure-wounds inline upcast scaling 2d8, got {cw['scaling']}",
        )

        sh = spells["shield"]
        self.assertEqual(sh["action_type"], "reaction")
        self.assertTrue(any(b.get("kind") == "ac_bonus" and b.get("value") == 5 for b in sh["buffs"]))

        mm = spells["magic-missile"]
        self.assertEqual(mm["resolution"], "auto_hit")

        fbolt = spells["fire-bolt"]
        self.assertEqual(fbolt["level"], 0)
        self.assertEqual(fbolt["resolution"], "attack")
        self.assertIsNotNone(fbolt["scaling"]["cantrip_tiers"])
        self.assertEqual(fbolt["scaling"]["cantrip_tiers"]["5"], "2d10")

    def test_monster_coverage_is_honest_about_traits_bonus_actions_and_reactions(self) -> None:
        """#39: a monster is only graded full if every stat-block element it has is executed by the engine.

        A trait or reaction is listed in unmodeled_clauses iff it is neither descriptive nor executable (traits_ir).
        """
        from srd2024.rules_compiler.monsters import _DESCRIPTIVE_TRAITS, _clause_slug, _trait_key

        monsters = self.rules["monsters"]
        for slug, m in monsters.items():
            clauses = m["provenance"]["unmodeled_clauses"]
            executed = {t["name"] for t in m.get("traits_ir") or []}
            with self.subTest(monster=slug):
                self.assertEqual(m["coverage"] == "full", not clauses, "full iff no unmodeled clauses")
                for sa in m.get("special_abilities") or []:
                    listed = f"special_ability:{_clause_slug(sa['name'])}" in clauses
                    expected = _trait_key(sa["name"]) not in _DESCRIPTIVE_TRAITS and sa["name"] not in executed
                    self.assertEqual(listed, expected, sa["name"])
                for b in m.get("bonus_actions") or []:
                    self.assertIn(f"bonus_action:{_clause_slug(b['name'])}", clauses)
                for r in m.get("reactions") or []:
                    self.assertEqual(f"reaction:{_clause_slug(r['name'])}" in clauses, r["name"] not in executed, r["name"])
        # Executed traits (#39) and the ones still out of reach.
        kinds = lambda slug: {t["kind"] for t in monsters[slug].get("traits_ir") or []}
        self.assertEqual(kinds("ghast"), {"start_of_turn_aura_save"})
        self.assertEqual(monsters["ghast"]["provenance"]["unmodeled_clauses"], [])
        self.assertEqual(kinds("hell-hound"), {"pack_tactics"})
        self.assertIn("legendary_resistance", kinds("balor"))
        self.assertEqual(next(t for t in monsters["adult-red-dragon"]["traits_ir"] if t["kind"] == "legendary_resistance")["uses"], 3)
        self.assertEqual(next(t for t in monsters["bandit-captain"]["traits_ir"] if t["kind"] == "parry")["ac_bonus"], 2)
        self.assertEqual(next(t for t in monsters["magmin"]["traits_ir"] if t["kind"] == "death_burst"),
                         {"kind": "death_burst", "name": "Death Burst", "ability": "dex", "dc": 11, "radius_ft": 10,
                          "dice": "2d6", "damage_type": "fire"})
        self.assertIn("special_ability:regeneration", monsters["troll"]["provenance"]["unmodeled_clauses"])  # dies-only-if clause
        self.assertEqual(monsters["frog"]["coverage"], "full")  # only descriptive traits (Amphibious, Standing Leap)

    def test_monsters_341_coverage_floors_and_iconic_monsters(self) -> None:
        monsters = self.rules["monsters"]
        self.assertEqual(len(monsters), 341)

        full_cnt = sum(1 for m in monsters.values() if m["coverage"] == "full")
        narrative_cnt = sum(1 for m in monsters.values() if m["coverage"] == "narrative")
        # #39: "full" means nothing in the stat block is left unexecuted. Traits, bonus actions and reactions aren't
        # executed, so monsters that have them are graded partial (121 full at the time of the fix).
        self.assertGreaterEqual(full_cnt, 115, f"Expected >= 115 full monsters, got {full_cnt}")
        self.assertEqual(narrative_cnt, 0, f"Expected 0 narrative monsters, got {narrative_cnt}")

        # Zero-action edge cases: shrieker-fungus & vampire-mist
        for slug in ("shrieker-fungus", "vampire-mist"):
            self.assertEqual(monsters[slug]["coverage"], "partial", f"Expected {slug} to be partial")
            self.assertEqual(monsters[slug]["actions"], [])

        # Goblin Warrior: Scimitar +4, 1d6+2 slashing + conditional Advantage 1d4 slashing
        gob = monsters["goblin-warrior"]
        self.assertEqual(gob["ac"], 15)
        self.assertEqual(gob["hp_max"], 10)
        scim = next((a for a in gob["actions"] if a["name"].lower() == "scimitar"), None)
        self.assertIsNotNone(scim, "goblin-warrior missing Scimitar action")
        assert scim is not None
        self.assertEqual(scim["attack_bonus"], 4)
        dmg_parts = scim["damage_parts"]
        self.assertTrue(any(p["dice"] == "1d6+2" and p["type"] == "slashing" and not p.get("conditional") for p in dmg_parts))
        self.assertTrue(any(p["dice"] == "1d4" and p.get("conditional") == "advantage" for p in dmg_parts))

        # Ghoul: Claw +4, 1d4+2 slashing, CON DC 10 -> paralyzed
        ghoul = monsters["ghoul"]
        self.assertEqual(ghoul["ac"], 12)
        self.assertEqual(ghoul["hp_max"], 22)
        claw = next((a for a in ghoul["actions"] if a["name"].lower() == "claw"), None)
        self.assertIsNotNone(claw, "ghoul missing Claw action")
        assert claw is not None
        self.assertEqual(claw["attack_bonus"], 4)
        self.assertIsNotNone(claw.get("save"))
        self.assertEqual(claw["save"]["ability"], "con")
        self.assertEqual(claw["save"]["dc"], 10)
        conds = [c["condition"] if isinstance(c, dict) else c for c in claw.get("conditions_applied", [])]
        self.assertIn("paralyzed", conds)

        # Adult Red Dragon: Huge, AC 19, HP 256, Multiattack + Rend + Fire Breath DC 21 DEX half 17d6 fire
        dragon = monsters["adult-red-dragon"]
        self.assertEqual(dragon["size"], "Huge")
        self.assertEqual(dragon["ac"], 19)
        self.assertEqual(dragon["hp_max"], 256)
        self.assertIn("fire", dragon["damage_immunities"])
        actions_by_name = {a["name"].lower(): a for a in dragon["actions"]}
        self.assertIn("multiattack", actions_by_name)
        self.assertIn("rend", actions_by_name)
        self.assertIn("fire breath", actions_by_name)

        rend = actions_by_name["rend"]
        self.assertEqual(rend["attack_bonus"], 14)
        self.assertTrue(any(p["dice"] == "1d10+8" and p["type"] == "slashing" for p in rend["damage_parts"]))
        self.assertTrue(any(p["dice"] == "2d4" and p["type"] == "fire" for p in rend["damage_parts"]))

        breath = actions_by_name["fire breath"]
        self.assertEqual(breath["save"]["ability"], "dex")
        self.assertEqual(breath["save"]["dc"], 21)
        self.assertEqual(breath["save"]["on_success"], "half")
        self.assertTrue(any(p["dice"] == "17d6" and p["type"] == "fire" for p in breath["damage_parts"]))

    def test_character_creation_ir_counts_and_key_tables(self) -> None:
        cc = self.rules["character_creation"]
        self.assertEqual(len(cc["ability_scores"]), 6)
        self.assertEqual(len(cc["skills"]), 18)
        self.assertEqual(len(cc["damage_types"]), 13)
        self.assertEqual(len(cc["classes"]), 12)
        self.assertEqual(len(cc["subclasses"]), 12)
        self.assertEqual(len(cc["species"]), 9)
        self.assertEqual(len(cc["subspecies"]), 24)
        self.assertEqual(len(cc["backgrounds"]), 4)
        self.assertEqual(len(cc["feats"]), 17)
        self.assertEqual(len(cc["levels"]), 287)
        self.assertEqual(len(cc["traits"]), 68)

        # Fighter weapon mastery progression (L1=3, L4=4, L10=5, L16=6)
        fighter_wm = cc["classes"]["fighter"]["weapon_mastery_progression"]
        self.assertEqual(fighter_wm.get("1", fighter_wm.get(1)), 3)
        self.assertEqual(fighter_wm.get("4", fighter_wm.get(4)), 4)
        self.assertEqual(fighter_wm.get("10", fighter_wm.get(10)), 5)
        self.assertEqual(fighter_wm.get("16", fighter_wm.get(16)), 6)

        # Barbarian L1=2, Wizard L1=0
        barb_wm = cc["classes"]["barbarian"]["weapon_mastery_progression"]
        self.assertEqual(barb_wm.get("1", barb_wm.get(1)), 2)
        wiz_wm = cc["classes"]["wizard"]["weapon_mastery_progression"]
        self.assertEqual(wiz_wm.get("1", wiz_wm.get(1)), 0)

        # Backgrounds: Soldier allows str/dex/con, origin feat savage-attacker
        soldier = cc["backgrounds"]["soldier"]
        self.assertEqual(set(soldier["ability_scores"]), {"str", "dex", "con"})
        self.assertEqual(soldier["feat"], "savage-attacker")

        # Species & traits: Wood Elf speed override, Dwarf toughness, Human versatile
        self.assertEqual(cc["traits"]["wood-elf-speed-increase"]["speed_override_ft"], 35)
        self.assertEqual(cc["traits"]["dwarven-toughness"]["hp_per_level_bonus"], 1)
        self.assertTrue(cc["traits"]["versatile"]["extra_origin_feat"])


if __name__ == "__main__":
    unittest.main()
