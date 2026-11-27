"""Verification tests for Criteria 6, 7, and 8 (SPEC §13).

Covers:
6. Search & Unicode normalization (Dragon's -> Dragon’s, Will-o'-Wisp -> Will-o’-Wisp,
   weird punctuation safety, exact slug/name ranking #1, facet filtering on spells/
   equipment/monsters/magic-items).
7. Resolved-bundle engine (equipment:longsword, spells:fireball, ambiguous slugs
   shield/druid/light/acid, tight max_tokens degradation without truncating root,
   anti-hub guard, inbound expansion, depth=0/1/2).
8. srd_graph (inbound/outbound, e.g. Sap mastery) & srd_validate (valid/ambiguous/
   invalid, inter-entity links, structural & feat-prerequisite advisories, provenance).
"""

from __future__ import annotations

import unittest
from typing import Any

from srd2024.compiler.build import SRD_ATTRIBUTION
from tests.helpers import EXPECTED_ENTITY_COUNT, EXPECTED_TYPE_COUNT, run_cli_json


def _extract_rows(payload: Any, *candidate_keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in candidate_keys:
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    raise AssertionError(f"Could not find list under {candidate_keys} in payload keys: {list(payload) if isinstance(payload, dict) else type(payload)}")


class TestSearchAndFacets(unittest.TestCase):
    """Criterion 6: Search, Unicode normalization, punctuation safety, and facet filtering."""

    def test_unicode_normalization_curly_apostrophe(self) -> None:
        # "Dragon's" (ASCII apostrophe) and "Dragon’s" (typographic U+2019) must produce
        # identical ranked results, including spells:dragons-breath (#1) and typographic
        # Dragon’s body hits (e.g. monsters:half-dragon).
        res_ascii = run_cli_json(["search", "Dragon's", "--limit", "25"])
        res_curly = run_cli_json(["search", "Dragon\u2019s", "--limit", "25"])
        rows_ascii = _extract_rows(res_ascii, "results", "items", "hits", "entities")
        rows_curly = _extract_rows(res_curly, "results", "items", "hits", "entities")
        self.assertEqual(rows_ascii, rows_curly, "ASCII and typographic apostrophe queries should match identically")
        ids = [r["id"] for r in rows_ascii]
        self.assertEqual(ids[0], "spells:dragons-breath")
        expected_body_typographic = {
            "monsters:adult-gold-dragon",
            "monsters:ancient-gold-dragon",
            "monsters:half-dragon",
            "features:draconic-sorcery-draconic-spells",
            "subclasses:draconic-sorcery",
        }
        self.assertTrue(
            bool(set(ids) & expected_body_typographic),
            f"Expected Dragon's search to match typographic Dragon’s entities, got {ids}",
        )

        # Will-o'-Wisp (typographic apostrophe in name: "Will-o’-Wisp")
        res_wisp = run_cli_json(["search", "Will-o'-Wisp", "--limit", "5"])
        wisp_rows = _extract_rows(res_wisp, "results", "items", "hits", "entities")
        self.assertTrue(wisp_rows, "Expected Will-o'-Wisp search hit")
        self.assertEqual(wisp_rows[0]["id"], "monsters:will-o-wisp")

        # Exact name resolution via srd_get using ASCII apostrophe
        get_wisp = run_cli_json(["get", "Will-o'-Wisp", "--depth", "0"])
        self.assertEqual(get_wisp["status"], "ok")
        self.assertEqual(get_wisp["root"]["id"], "monsters:will-o-wisp")

    def test_weird_punctuation_never_crashes_and_stubs_only(self) -> None:
        for weird_query in ('"fire-ball"', "C++", '\'""*', "(((:)))", "---", "armor +2", "fire*"):
            res = run_cli_json(["search", "--limit", "5", "--format", "json", "--", weird_query])
            rows = _extract_rows(res, "results", "items", "hits", "entities")
            for row in rows:
                self.assertIn("id", row)
                self.assertIn("type", row)
                self.assertIn("name", row)
                self.assertIn("summary", row)
                self.assertNotIn("card_body_md", row, "srd_search must return stubs, never full cards")
                self.assertNotIn("card_md", row, "srd_search must return stubs, never full cards")

    def test_exact_slug_and_name_rank_first(self) -> None:
        for query, expected_id in (
            ("fireball", "spells:fireball"),
            ("Longsword", "equipment:longsword"),
            ("paralyzed", "conditions:paralyzed"),
        ):
            res = run_cli_json(["search", query, "--limit", "5"])
            rows = _extract_rows(res, "results", "items", "hits", "entities")
            self.assertTrue(rows, f"No search hits for {query}")
            self.assertEqual(rows[0]["id"], expected_id, f"Expected {expected_id} #1 for {query}, got {rows[:3]}")

    def test_search_type_filter(self) -> None:
        res = run_cli_json(["search", "shield", "--type", "spells", "--limit", "10"])
        rows = _extract_rows(res, "results", "items", "hits", "entities")
        self.assertTrue(rows)
        self.assertTrue(all(r["type"] == "spells" for r in rows))
        self.assertEqual(rows[0]["id"], "spells:shield")

    def test_list_catalog_and_facet_filtering(self) -> None:
        # Catalog (no --type)
        catalog = run_cli_json(["list"])
        rtypes = _extract_rows(catalog, "resource_types", "types", "catalog")
        self.assertEqual(len(rtypes), EXPECTED_TYPE_COUNT)
        self.assertEqual(sum(t["count"] for t in rtypes), EXPECTED_ENTITY_COUNT)

        # 1. Spells: level=3, class=wizard, school=evocation -> fireball, lightning-bolt, tiny-hut
        spells = run_cli_json(
            ["list", "--type", "spells", "--filter", "level=3", "--filter", "class=wizard", "--filter", "school=evocation"]
        )
        spell_rows = _extract_rows(spells, "items", "entities", "results")
        spell_ids = {r["id"] for r in spell_rows}
        self.assertEqual(spell_ids, {"spells:fireball", "spells:lightning-bolt", "spells:tiny-hut"})
        self.assertEqual(spells["total"], 3)

        # 2. Equipment: mastery=sap -> 6 weapons including longsword
        sap_eq = run_cli_json(["list", "--type", "equipment", "--filter", "mastery=sap"])
        sap_ids = {r["id"] for r in _extract_rows(sap_eq, "items", "entities", "results")}
        self.assertEqual(
            sap_ids,
            {
                "equipment:flail",
                "equipment:longsword",
                "equipment:mace",
                "equipment:morningstar",
                "equipment:spear",
                "equipment:war-pick",
            },
        )

        # 3. Monsters: cr=1, creature_type=undead -> ghoul, specter
        undead = run_cli_json(["list", "--type", "monsters", "--filter", "cr=1", "--filter", "creature_type=undead"])
        undead_ids = {r["id"] for r in _extract_rows(undead, "items", "entities", "results")}
        self.assertEqual(undead_ids, {"monsters:ghoul", "monsters:specter"})

        # 4. Magic items: rarity=legendary, attunement=true -> 23 items
        leg = run_cli_json(["list", "--type", "magic-items", "--filter", "rarity=legendary", "--filter", "attunement=true"])
        self.assertEqual(leg["total"], 23)

        # 5. OR semantics for repeated same facet key
        or_res = run_cli_json(["list", "--type", "spells", "--filter", "school=evocation", "--filter", "school=abjuration", "--filter", "level=3"])
        or_ids = {r["id"] for r in _extract_rows(or_res, "items", "entities", "results")}
        self.assertIn("spells:fireball", or_ids)
        self.assertIn("spells:counterspell", or_ids)


class TestResolvedBundleEngine(unittest.TestCase):
    """Criterion 7: Resolved-bundle engine."""

    def test_longsword_depth_1_bundle(self) -> None:
        bundle = run_cli_json(["get", "equipment:longsword", "--depth", "1"])
        self.assertEqual(bundle["status"], "ok")
        self.assertEqual(bundle["root"]["id"], "equipment:longsword")
        self.assertTrue(bundle["root"]["card_body_md"])

        deps = {d["id"]: d for d in bundle["dependencies"]}
        for required_dep in (
            "weapon-mastery-properties:sap",
            "weapon-properties:versatile",
            "damage-types:slashing",
        ):
            self.assertIn(required_dep, deps, f"Missing {required_dep} in longsword bundle")
            self.assertEqual(deps[required_dep]["hop"], 1)
            self.assertEqual(deps[required_dep]["tier"], 1)
            self.assertEqual(deps[required_dep]["detail"], "full")
            self.assertTrue(deps[required_dep]["card_body_md"])

        # Verify sorting contract: (hop ASC, tier ASC, id ASC)
        sort_keys = [(d["hop"], d["tier"], d["id"]) for d in bundle["dependencies"]]
        self.assertEqual(sort_keys, sorted(sort_keys))

    def test_fireball_depth_1_tier_priority_and_antihub_guard(self) -> None:
        bundle = run_cli_json(["get", "spells:fireball", "--depth", "1", "--max-tokens", "2000"])
        self.assertEqual(bundle["status"], "ok")
        self.assertEqual(bundle["root"]["id"], "spells:fireball")

        deps = {d["id"]: d for d in bundle["dependencies"]}
        for tier1_id in ("magic-schools:evocation", "damage-types:fire"):
            self.assertIn(tier1_id, deps)
            self.assertEqual(deps[tier1_id]["tier"], 1)
            self.assertEqual(deps[tier1_id]["detail"], "full")

        # Anti-hub guard: classes:wizard and classes:sorcerer are Tier 2 hubs (>400 tokens)
        # so they must NOT expand as full cards when card_tokens_est > 400
        for cls_id in ("classes:wizard", "classes:sorcerer"):
            if cls_id in deps and deps[cls_id]["tokens_est"] > 400:
                self.assertEqual(
                    deps[cls_id]["detail"],
                    "summary",
                    f"Anti-hub guard failed: {cls_id} ({deps[cls_id]['tokens_est']} tokens) expanded in full",
                )
                self.assertIsNone(deps[cls_id]["card_body_md"])

    def test_ambiguous_slugs_never_guess_silently(self) -> None:
        expected_candidates = {
            "shield": {"equipment:shield", "magic-items:shield", "spells:shield"},
            "druid": {"classes:druid", "monsters:druid"},
            "light": {"spells:light", "weapon-properties:light"},
            "acid": {"damage-types:acid", "equipment:acid"},
        }
        for slug, expected_ids in expected_candidates.items():
            res = run_cli_json(["get", slug])
            self.assertEqual(res["status"], "ambiguous", f"Slug {slug!r} should be ambiguous")
            cand_ids = {c["id"] for c in res["candidates"]}
            self.assertEqual(cand_ids, expected_ids, f"Unexpected candidates for {slug!r}: {cand_ids}")
            # Verify sorted candidates
            self.assertEqual([c["id"] for c in res["candidates"]], sorted(c["id"] for c in res["candidates"]))

    def test_not_found_returns_suggestions(self) -> None:
        res = run_cli_json(["get", "firebal"])
        self.assertEqual(res["status"], "not_found")
        self.assertIsInstance(res.get("suggestions"), list)

    def test_tight_max_tokens_degrades_dependencies_without_truncating_root(self) -> None:
        full_bundle = run_cli_json(["get", "equipment:longsword", "--depth", "1", "--max-tokens", "2000"])
        tight_bundle = run_cli_json(["get", "equipment:longsword", "--depth", "1", "--max-tokens", "200"])

        self.assertEqual(tight_bundle["status"], "ok")
        # Root card is NEVER truncated
        self.assertEqual(tight_bundle["root"]["card_body_md"], full_bundle["root"]["card_body_md"])

        stats = tight_bundle["stats"]
        self.assertTrue(
            stats["truncated"] or stats["summary_count"] > 0 or len(stats["omitted_ids"]) > 0,
            f"Expected tight budget to degrade or omit dependencies, got stats={stats}",
        )
        self.assertLess(stats["full_count"], full_bundle["stats"]["full_count"])

        # Determinism check on repeated call
        tight_bundle_2 = run_cli_json(["get", "equipment:longsword", "--depth", "1", "--max-tokens", "200"])
        self.assertEqual(tight_bundle, tight_bundle_2)

    def test_depth_zero_and_inbound_and_rel_filter(self) -> None:
        d0 = run_cli_json(["get", "equipment:longsword", "--depth", "0"])
        self.assertEqual(d0["dependencies"], [])
        self.assertEqual(d0["stats"]["depth"], 0)

        # API URL resolution (§4.2 #2) and bare slug resolution (§4.2 #3)
        via_url = run_cli_json(["get", "/api/2024/equipment/longsword", "--depth", "0"])
        self.assertEqual(via_url["status"], "ok")
        self.assertEqual(via_url["root"]["id"], "equipment:longsword")

        via_slug = run_cli_json(["get", "longsword", "--depth", "0"])
        self.assertEqual(via_slug["status"], "ok")
        self.assertEqual(via_slug["root"]["id"], "equipment:longsword")

        # Rel whitelist filter
        rel_only = run_cli_json(["get", "equipment:longsword", "--depth", "1", "--rel", "weapon_mastery"])
        self.assertEqual([d["id"] for d in rel_only["dependencies"]], ["weapon-mastery-properties:sap"])

        # Depth 2 expansion
        d2 = run_cli_json(["get", "equipment:longsword", "--depth", "2", "--max-tokens", "2000"])
        self.assertEqual(d2["status"], "ok")
        self.assertEqual(d2["stats"]["depth"], 2)

        with_in = run_cli_json(["get", "weapon-mastery-properties:sap", "--depth", "0", "--inbound"])
        self.assertGreater(len(with_in["inbound"]), 0)
        inbound_ids = {item["id"] for item in with_in["inbound"]}
        self.assertIn("equipment:longsword", inbound_ids)


class TestGraphAndValidate(unittest.TestCase):
    """Criterion 8: srd_graph and srd_validate."""

    def test_srd_graph_inbound_sap_mastery_and_outbound_longsword(self) -> None:
        sap_in = run_cli_json(["graph", "weapon-mastery-properties:sap", "--direction", "in"])
        self.assertEqual(sap_in["status"], "ok")
        neighbors = _extract_rows(sap_in, "neighbors", "edges", "nodes")
        neighbor_ids = {n["id"] for n in neighbors}
        self.assertTrue(
            {"equipment:longsword", "equipment:flail", "equipment:mace", "equipment:spear"}.issubset(neighbor_ids),
            f"Expected Sap weapons in inbound graph, got {neighbor_ids}",
        )
        for n in neighbors:
            self.assertEqual(n["direction"], "in")
            self.assertIn("rel", n)
            self.assertIn("hop", n)

        ls_out = run_cli_json(["graph", "equipment:longsword", "--direction", "out"])
        out_ids = {n["id"] for n in _extract_rows(ls_out, "neighbors", "edges", "nodes")}
        self.assertIn("weapon-mastery-properties:sap", out_ids)
        self.assertIn("damage-types:slashing", out_ids)

    def test_srd_validate_mixed_set_links_advisories_and_provenance(self) -> None:
        res = run_cli_json(
            [
                "validate",
                "species:elf",
                "subspecies:elven-lineage-high-elf",
                "classes:wizard",
                "spells:fireball",
                "feats:grappler",
                "shield",
                "nonexistent-xyz",
            ]
        )
        valid_ids = {v["id"] for v in res["valid"]}
        self.assertEqual(
            valid_ids,
            {
                "species:elf",
                "subspecies:elven-lineage-high-elf",
                "classes:wizard",
                "spells:fireball",
                "feats:grappler",
            },
        )

        # Ambiguous
        ambig = res["ambiguous"]
        self.assertEqual(len(ambig), 1)
        self.assertEqual(ambig[0]["query"], "shield")
        self.assertEqual(
            {c["id"] for c in ambig[0]["candidates"]},
            {"equipment:shield", "magic-items:shield", "spells:shield"},
        )

        # Invalid
        invalid = res["invalid"]
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["query"], "nonexistent-xyz")

        # Inter-entity graph links (e.g. fireball->wizard and elf<->elven-lineage-high-elf)
        links = res["links"]
        link_pairs = {(lk["src_id"], lk["dst_id"]) for lk in links}
        self.assertIn(("spells:fireball", "classes:wizard"), link_pairs)
        self.assertTrue(
            ("species:elf", "subspecies:elven-lineage-high-elf") in link_pairs
            or ("subspecies:elven-lineage-high-elf", "species:elf") in link_pairs,
            f"Expected elf<->high-elf link, got {link_pairs}",
        )

        # Advisories: feats:grappler has prerequisites/prerequisite_options
        advisories = res["advisories"]
        self.assertTrue(advisories, "Expected prerequisite advisory for feats:grappler")
        adv_blob = str(advisories).lower()
        self.assertIn("grappler", adv_blob)

        # Provenance present
        prov = res["provenance"]
        self.assertEqual(prov["dataset_id"], "srd-2024-5.2")
        self.assertEqual(prov["license"], "CC-BY-4.0")
        self.assertEqual(prov["attribution"], SRD_ATTRIBUTION)

    def test_srd_validate_missing_parent_advisory(self) -> None:
        # Subclass without its parent class should produce a structural advisory
        res = run_cli_json(["validate", "subclasses:path-of-the-berserker"])
        self.assertEqual({v["id"] for v in res["valid"]}, {"subclasses:path-of-the-berserker"})
        advisories = res["advisories"]
        self.assertTrue(advisories, "Expected missing-parent-class advisory for standalone subclass")
        self.assertIn("barbarian", str(advisories).lower())

        # Subspecies without its parent species should also produce a structural advisory
        sub_res = run_cli_json(["validate", "subspecies:elven-lineage-high-elf"])
        self.assertEqual({v["id"] for v in sub_res["valid"]}, {"subspecies:elven-lineage-high-elf"})
        self.assertTrue(sub_res["advisories"], "Expected missing-parent-species advisory for standalone subspecies")
        self.assertIn("elf", str(sub_res["advisories"]).lower())

        # --no-links skips link & advisory computation
        no_links = run_cli_json(["validate", "classes:wizard", "spells:fireball", "--no-links"])
        self.assertEqual(no_links["links"], [])


if __name__ == "__main__":
    unittest.main()
