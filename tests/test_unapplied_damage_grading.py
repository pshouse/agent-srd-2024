"""#166: spells whose damage the engine doesn't apply on cast are never graded `full`.

#6 made the engine report this damage (`spell_damage_unresolved`) instead of spending the slot silently.
Divine Favor was still graded `full` while doing nothing, so the compiler now marks every such spell partial
with the reason. The smites (#6) are real on-hit riders and stay full.
"""

from __future__ import annotations

import unittest

from srd2024.engine.combat import ON_HIT_SMITES
from srd2024.engine.store import RulesStore

EXPECTED = {
    "divine-favor": "per_hit_rider_damage_not_applied",
    "hunters-mark": "per_hit_rider_damage_not_applied",
    "hex": "per_hit_rider_damage_not_applied",
    "fire-shield": "retaliatory_damage_not_applied",
    "spike-growth": "zone_movement_damage_not_applied",
    "dimension-door": "conditional_damage_not_applied",
    "power-word-kill": "conditional_damage_not_applied",
}


class TestUnappliedDamageGrading(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = RulesStore()

    def test_each_spell_is_partial_with_its_reason(self) -> None:
        for slug, clause in EXPECTED.items():
            with self.subTest(spell=slug):
                rec = self.rules.get_spell(slug)
                self.assertEqual(rec["coverage"], "partial")
                self.assertIn(clause, rec["provenance"]["unmodeled_clauses"])
                self.assertEqual(rec["resolution"], "auto_apply")  # the engine still reports it as unresolved

    def test_no_full_spell_has_damage_the_engine_skips(self) -> None:
        # The engine applies damage for auto_hit / attack / save resolutions and for the on-hit smites only.
        offenders = sorted(
            slug
            for slug, rec in self.rules._data["spells"].items()
            if rec.get("coverage") == "full"
            and rec.get("damage")
            and rec.get("resolution") not in ("auto_hit", "attack", "save")
            and slug not in ON_HIT_SMITES
        )
        self.assertEqual(offenders, [])
        unapplied = sorted(
            slug
            for slug, rec in self.rules._data["spells"].items()
            if rec.get("resolution") == "auto_apply" and rec.get("damage") and slug not in ON_HIT_SMITES
        )
        self.assertEqual(unapplied, sorted(EXPECTED))

    def test_smites_stay_full(self) -> None:
        self.assertEqual(self.rules.get_spell("divine-smite")["coverage"], "full")


if __name__ == "__main__":
    unittest.main()
