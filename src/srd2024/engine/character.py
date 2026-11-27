"""Deterministic 5e-2024 Character Compiler & Validator (STAGE2-SPEC §4.2–§4.4).

Validates a CharacterBuildSpec JSON dict against compiled `dist/rules.json`
and derives the authoritative 2024 statblock with structured errors & warnings.
"""

from __future__ import annotations

from typing import Any

from .store import RulesStore, strip_prefix

ABILITY_KEYS: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")
STANDARD_ARRAY_SORTED: list[int] = [8, 10, 12, 13, 14, 15]
POINT_BUY_COSTS: dict[int, int] = {
    8: 0,
    9: 1,
    10: 2,
    11: 3,
    12: 4,
    13: 5,
    14: 7,
    15: 9,
}
POINT_BUY_BUDGET = 27

_FALLBACK_ASI_LEVELS: dict[str, list[int]] = {
    "fighter": [4, 6, 8, 12, 14, 16],
    "rogue": [4, 8, 10, 12, 16],
}
_DEFAULT_ASI_LEVELS: list[int] = [4, 8, 12, 16]

_FALLBACK_MONK_UNARMORED_MOVEMENT: dict[int, int] = {
    1: 0,
    2: 10,
    3: 10,
    4: 10,
    5: 10,
    6: 15,
    7: 15,
    8: 15,
    9: 15,
    10: 20,
    11: 20,
    12: 20,
    13: 20,
    14: 25,
    15: 25,
    16: 25,
    17: 25,
    18: 30,
    19: 30,
    20: 30,
}

_EXTRA_ATTACK_CLASSES: frozenset[str] = frozenset(
    {"barbarian", "fighter", "monk", "paladin", "ranger"}
)


def ability_mod(score: int) -> int:
    """Compute 5e ability modifier: floor((score - 10) / 2)."""
    return (int(score) - 10) // 2


def _issue(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _extract_slug(item: Any) -> str:
    if isinstance(item, dict):
        raw = item.get("slug") or item.get("index") or item.get("id") or ""
        return strip_prefix(str(raw))
    return strip_prefix(str(item))


def _weapon_mastery_allowance(class_slug: str, class_rec: dict[str, Any], level: int) -> int:
    prog = class_rec.get("weapon_mastery_progression")
    if isinstance(prog, dict):
        val = prog.get(str(level))
        if val is None:
            val = prog.get(level)
        if val is not None:
            return int(val)
    elif isinstance(prog, list) and 1 <= level <= len(prog):
        return int(prog[level - 1])
    elif isinstance(prog, int):
        return prog

    if class_slug == "fighter":
        if level >= 16:
            return 6
        if level >= 10:
            return 5
        if level >= 4:
            return 4
        return 3
    if class_slug == "barbarian":
        if level >= 10:
            return 4
        if level >= 4:
            return 3
        return 2
    if class_slug in ("paladin", "ranger", "rogue"):
        return 2
    return 0


def _asi_levels_for_class(class_slug: str, class_rec: dict[str, Any]) -> list[int]:
    raw = class_rec.get("asi_levels")
    if isinstance(raw, list) and raw:
        return sorted(int(x) for x in raw)
    return list(_FALLBACK_ASI_LEVELS.get(class_slug, _DEFAULT_ASI_LEVELS))


def _extra_attacks_for_class(class_slug: str, level: int) -> int:
    if class_slug == "fighter":
        if level >= 20:
            return 4
        if level >= 11:
            return 3
        if level >= 5:
            return 2
        return 1
    if class_slug in _EXTRA_ATTACK_CLASSES and level >= 5:
        return 2
    return 1


def _is_armor_proficient(class_profs: set[str], armor_cat: str) -> bool:
    if "all-armor" in class_profs:
        return True
    if armor_cat == "shield":
        return "shields" in class_profs or "shield" in class_profs
    return f"{armor_cat}-armor" in class_profs


def _is_weapon_proficient(class_profs: set[str], weapon_rec: dict[str, Any]) -> bool:
    cat = str(weapon_rec.get("weapon_category") or "").strip().lower()
    if cat == "simple" and "simple-weapons" in class_profs:
        return True
    if cat == "martial" and "martial-weapons" in class_profs:
        return True
    slug = str(weapon_rec.get("slug") or "").strip().lower()
    candidates = {slug, f"{slug}s", f"{slug}es"}
    if slug.endswith("y"):
        candidates.add(f"{slug[:-1]}ies")
    if slug == "quarterstaff":
        candidates.add("quarterstaffs")
        candidates.add("quarterstaves")
    return bool(candidates & class_profs)


def _check_feat_prerequisites(
    feat_rec: dict[str, Any],
    char_level: int,
    pick_level: int,
    abilities: dict[str, int],
    features: set[str],
    class_slug: str,
) -> list[str]:
    """Return list of human-readable failed prerequisite reasons (empty if satisfied)."""
    failures: list[str] = []
    feat_type = str(feat_rec.get("feat_type") or feat_rec.get("type") or "").strip().lower()

    prereqs = feat_rec.get("prerequisites")
    min_level: int | None = None
    feature_named: str | None = None
    min_abilities: dict[str, int] = {}

    if isinstance(prereqs, dict):
        if prereqs.get("minimum_level") is not None:
            min_level = int(prereqs["minimum_level"])
        if prereqs.get("feature_named"):
            feature_named = str(prereqs["feature_named"])
        if prereqs.get("ability_score") and prereqs.get("minimum_score"):
            ab = _extract_slug(prereqs["ability_score"])
            min_abilities[ab] = int(prereqs["minimum_score"])
    elif isinstance(prereqs, list):
        for p in prereqs:
            if not isinstance(p, dict):
                continue
            if p.get("minimum_level") is not None:
                lvl = int(p["minimum_level"])
                min_level = lvl if min_level is None else max(min_level, lvl)
            if p.get("feature_named"):
                feature_named = str(p["feature_named"])
            if p.get("ability_score") and p.get("minimum_score"):
                ab = _extract_slug(p["ability_score"])
                min_abilities[ab] = int(p["minimum_score"])
            elif p.get("ability") and p.get("minimum_score"):
                ab = _extract_slug(p["ability"])
                min_abilities[ab] = int(p["minimum_score"])

    # 2024 RAW floor even if a compiled feat omitted explicit minimum_level.
    if min_level is None:
        if feat_type == "general":
            min_level = 4
        elif feat_type == "epic-boon":
            min_level = 19

    effective_level = min(char_level, pick_level)
    if min_level is not None and effective_level < min_level:
        failures.append(f"requires level {min_level}+ (got level {effective_level})")

    if feature_named:
        needle = feature_named.strip().lower().replace(" ", "-")
        has_feature = any(
            needle in f.lower() or f.lower().endswith(needle) for f in features
        )
        # Fallback for Fighting Style feature check.
        if not has_feature and "fighting-style" in needle:
            has_feature = (
                (class_slug == "fighter" and char_level >= 1)
                or (class_slug in ("paladin", "ranger") and char_level >= 2)
            )
        if not has_feature:
            failures.append(f"requires feature '{feature_named}'")

    for ab, req_val in min_abilities.items():
        if abilities.get(ab, 0) < req_val:
            failures.append(f"requires {ab.upper()} >= {req_val} (got {abilities.get(ab, 0)})")

    # Handle OR-style prerequisite_options (e.g., Grappler: STR >= 13 OR DEX >= 13).
    prereq_opts = feat_rec.get("prerequisite_options")
    parsed_opts: list[tuple[str, int]] = []
    if isinstance(prereq_opts, list):
        for opt in prereq_opts:
            if not isinstance(opt, dict):
                continue
            ab_raw = opt.get("ability") or opt.get("ability_score")
            ms = opt.get("minimum_score")
            if ab_raw and ms is not None:
                parsed_opts.append((_extract_slug(ab_raw), int(ms)))
    elif isinstance(prereq_opts, dict):
        from_block = prereq_opts.get("from", {})
        opts_list = from_block.get("options", []) if isinstance(from_block, dict) else []
        if not opts_list and isinstance(prereq_opts.get("options"), list):
            opts_list = prereq_opts["options"]
        for opt in opts_list:
            if not isinstance(opt, dict):
                continue
            ab_raw = opt.get("ability") or opt.get("ability_score")
            ms = opt.get("minimum_score")
            if ab_raw and ms is not None:
                parsed_opts.append((_extract_slug(ab_raw), int(ms)))

    if parsed_opts:
        if not any(abilities.get(ab, 0) >= req_val for ab, req_val in parsed_opts):
            desc = " or ".join(f"{ab.upper()} >= {req_val}" for ab, req_val in parsed_opts)
            failures.append(f"requires {desc}")

    return failures


def _collect_species_traits(
    species_rec: dict[str, Any],
    subspecies_rec: dict[str, Any] | None,
    level: int,
    rules: RulesStore,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return ordered active trait slugs and their compiled trait dicts."""
    slugs: list[str] = []
    seen: set[str] = set()

    def _add(slug: str) -> None:
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    for t in species_rec.get("traits", []) or []:
        if isinstance(t, dict):
            req_lvl = int(t.get("level", 1))
            if req_lvl <= level:
                _add(_extract_slug(t.get("trait") or t))
        else:
            _add(_extract_slug(t))

    if subspecies_rec is not None:
        for t in subspecies_rec.get("traits", []) or []:
            if isinstance(t, dict):
                req_lvl = int(t.get("level", 1))
                if req_lvl <= level:
                    _add(_extract_slug(t.get("trait") or t))
            else:
                _add(_extract_slug(t))

    records: list[dict[str, Any]] = []
    for sl in slugs:
        rec = rules.get_trait(sl)
        if rec is not None:
            records.append(rec)
    return slugs, records


def _extract_level_slots(spellcasting_block: dict[str, Any] | None) -> dict[str, int]:
    if not isinstance(spellcasting_block, dict):
        return {}
    slots: dict[str, int] = {}
    if isinstance(spellcasting_block.get("slots"), dict):
        for k, v in spellcasting_block["slots"].items():
            cnt = int(v)
            if cnt > 0:
                slots[str(k)] = cnt
    for lvl in range(1, 10):
        key = f"spell_slots_level_{lvl}"
        if key in spellcasting_block:
            cnt = int(spellcasting_block[key] or 0)
            if cnt > 0:
                slots[str(lvl)] = cnt
    return dict(sorted(slots.items(), key=lambda kv: int(kv[0])))


def compile_character(spec: dict[str, Any], rules: RulesStore) -> dict[str, Any]:
    """Validate and compile a 5e-2024 CharacterBuildSpec into an authoritative statblock."""
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    if not isinstance(spec, dict):
        return {
            "status": "invalid",
            "valid": False,
            "errors": [_issue("invalid_spec", "$", "Character spec must be a JSON object.")],
            "warnings": [],
            "statblock": None,
        }

    name = str(spec.get("name") or "Unnamed Adventurer").strip()

    # ------------------------------------------------------------------
    # 1. Core Entity Existence & Level / Subspecies / Subclass Checks
    # ------------------------------------------------------------------
    raw_level = spec.get("level", 1)
    level_valid = isinstance(raw_level, int) and not isinstance(raw_level, bool) and 1 <= raw_level <= 20
    if not level_valid:
        errors.append(
            _issue("invalid_level", "level", f"Level must be an integer between 1 and 20 (got {raw_level!r}).")
        )
        level = 1
    else:
        level = int(raw_level)

    species_raw = spec.get("species")
    species_slug = strip_prefix(species_raw) if species_raw else ""
    species_rec = rules.get_species(species_slug) if species_slug else None
    if species_rec is None:
        errors.append(
            _issue("unknown_species", "species", f"Unknown species: {species_raw!r}.")
        )

    subspecies_raw = spec.get("subspecies")
    subspecies_slug = strip_prefix(subspecies_raw) if subspecies_raw else None
    subspecies_rec: dict[str, Any] | None = None
    if species_rec is not None:
        allowed_subspecies = [_extract_slug(s) for s in (species_rec.get("subspecies") or [])]
        if allowed_subspecies:
            if not subspecies_slug:
                errors.append(
                    _issue(
                        "missing_subspecies",
                        "subspecies",
                        f"Species '{species_slug}' requires a subspecies choice.",
                    )
                )
            else:
                subspecies_rec = rules.get_subspecies(subspecies_slug)
                if subspecies_rec is None:
                    errors.append(
                        _issue(
                            "unknown_subspecies",
                            "subspecies",
                            f"Unknown subspecies: {subspecies_raw!r}.",
                        )
                    )
                else:
                    parent_sp = _extract_slug(subspecies_rec.get("species", ""))
                    if parent_sp != species_slug and subspecies_slug not in allowed_subspecies:
                        errors.append(
                            _issue(
                                "subspecies_mismatch",
                                "subspecies",
                                f"Subspecies '{subspecies_slug}' does not belong to species '{species_slug}'.",
                            )
                        )
        elif subspecies_slug:
            errors.append(
                _issue(
                    "invalid_subspecies",
                    "subspecies",
                    f"Species '{species_slug}' does not have subspecies (got '{subspecies_slug}').",
                )
            )

    bg_raw = spec.get("background")
    bg_slug = strip_prefix(bg_raw) if bg_raw else ""
    bg_rec = rules.get_background(bg_slug) if bg_slug else None
    if bg_rec is None:
        errors.append(
            _issue("unknown_background", "background", f"Unknown background: {bg_raw!r}.")
        )

    class_raw = spec.get("class")
    class_slug = strip_prefix(class_raw) if class_raw else ""
    class_rec = rules.get_class(class_slug) if class_slug else None
    if class_rec is None:
        errors.append(
            _issue("unknown_class", "class", f"Unknown class: {class_raw!r}.")
        )

    subclass_raw = spec.get("subclass")
    subclass_slug = strip_prefix(subclass_raw) if subclass_raw else None
    subclass_rec: dict[str, Any] | None = None
    if level_valid:
        if level >= 3 and not subclass_slug:
            errors.append(
                _issue(
                    "missing_subclass",
                    "subclass",
                    f"Characters of level {level} (>= 3) must select a subclass.",
                )
            )
        elif level < 3 and subclass_slug:
            errors.append(
                _issue(
                    "subclass_too_early",
                    "subclass",
                    f"Subclass '{subclass_slug}' cannot be selected before level 3 (current level {level}).",
                )
            )
    if subclass_slug:
        subclass_rec = rules.get_subclass(subclass_slug)
        if subclass_rec is None:
            errors.append(
                _issue("unknown_subclass", "subclass", f"Unknown subclass: {subclass_raw!r}.")
            )
        elif class_rec is not None:
            sc_class = _extract_slug(subclass_rec.get("class", ""))
            allowed_sc = [_extract_slug(s) for s in (class_rec.get("subclasses") or [])]
            if sc_class and sc_class != class_slug and subclass_slug not in allowed_sc:
                errors.append(
                    _issue(
                        "subclass_mismatch",
                        "subclass",
                        f"Subclass '{subclass_slug}' belongs to '{sc_class}', not '{class_slug}'.",
                    )
                )

    # Size resolution.
    size = "Medium"
    if species_rec is not None:
        size_options = species_rec.get("size_options")
        sp_size = species_rec.get("size")
        if isinstance(size_options, list) and size_options:
            valid_sizes = [str(s) for s in size_options]
            default_size = valid_sizes[0]
        elif isinstance(sp_size, list) and sp_size:
            valid_sizes = [str(s) for s in sp_size]
            default_size = valid_sizes[0]
        elif isinstance(sp_size, str) and sp_size:
            valid_sizes = [sp_size]
            default_size = sp_size
        else:
            valid_sizes = ["Medium", "Small"]
            default_size = "Medium"

        req_size = spec.get("size")
        if req_size is None:
            size = default_size
        else:
            matched = [s for s in valid_sizes if s.lower() == str(req_size).strip().lower()]
            if matched:
                size = matched[0]
            else:
                size = str(req_size)
                errors.append(
                    _issue(
                        "invalid_size",
                        "size",
                        f"Size '{req_size}' is not valid for species '{species_slug}' (allowed: {valid_sizes}).",
                    )
                )

    # ------------------------------------------------------------------
    # 2. Base Ability Scores
    # ------------------------------------------------------------------
    ability_method = str(spec.get("ability_method") or "point_buy").strip().lower()
    raw_base = spec.get("base_abilities")
    abilities: dict[str, int] = {ab: 10 for ab in ABILITY_KEYS}

    if not isinstance(raw_base, dict):
        errors.append(
            _issue("invalid_base_abilities", "base_abilities", "base_abilities must be a dict with all 6 abilities.")
        )
    else:
        missing_abs = [ab for ab in ABILITY_KEYS if ab not in raw_base]
        if missing_abs:
            errors.append(
                _issue(
                    "invalid_base_abilities",
                    "base_abilities",
                    f"Missing ability scores: {', '.join(missing_abs)}.",
                )
            )
        else:
            all_ints = True
            for ab in ABILITY_KEYS:
                val = raw_base[ab]
                if not isinstance(val, int) or isinstance(val, bool):
                    all_ints = False
                    errors.append(
                        _issue(
                            "invalid_base_abilities",
                            f"base_abilities.{ab}",
                            f"Ability score '{ab}' must be an integer (got {val!r}).",
                        )
                    )
                else:
                    abilities[ab] = int(val)

            if all_ints:
                if ability_method == "standard_array":
                    if sorted(abilities.values()) != STANDARD_ARRAY_SORTED:
                        errors.append(
                            _issue(
                                "invalid_standard_array",
                                "base_abilities",
                                f"Standard array must contain exact scores {STANDARD_ARRAY_SORTED}, "
                                f"got {sorted(abilities.values())}.",
                            )
                        )
                elif ability_method == "point_buy":
                    total_cost = 0
                    in_range = True
                    for ab, score in abilities.items():
                        if score not in POINT_BUY_COSTS:
                            in_range = False
                            errors.append(
                                _issue(
                                    "point_buy_out_of_range",
                                    f"base_abilities.{ab}",
                                    f"Point-buy score for '{ab}' must be 8..15 (got {score}).",
                                )
                            )
                        else:
                            total_cost += POINT_BUY_COSTS[score]
                    if in_range:
                        if total_cost > POINT_BUY_BUDGET:
                            errors.append(
                                _issue(
                                    "point_buy_exceeded",
                                    "base_abilities",
                                    f"Point-buy total {total_cost} exceeds maximum budget of {POINT_BUY_BUDGET}.",
                                )
                            )
                        elif total_cost < POINT_BUY_BUDGET:
                            warnings.append(
                                _issue(
                                    "point_buy_unspent",
                                    "base_abilities",
                                    f"Point-buy uses {total_cost}/{POINT_BUY_BUDGET} points ({POINT_BUY_BUDGET - total_cost} unspent).",
                                )
                            )
                elif ability_method == "manual":
                    for ab, score in abilities.items():
                        if score < 3 or score > 20:
                            errors.append(
                                _issue(
                                    "ability_out_of_range",
                                    f"base_abilities.{ab}",
                                    f"Manual base ability '{ab}' must be 3..20 (got {score}).",
                                )
                            )
                else:
                    errors.append(
                        _issue(
                            "invalid_ability_method",
                            "ability_method",
                            f"Unsupported ability_method '{ability_method}'. Use 'point_buy', 'standard_array', or 'manual'.",
                        )
                    )

    # ------------------------------------------------------------------
    # 3. 2024 Background ASI
    # ------------------------------------------------------------------
    bg_asi_raw = spec.get("background_asi")
    if bg_asi_raw is None:
        warnings.append(
            _issue(
                "missing_background_asi",
                "background_asi",
                "No background_asi specified (2024 backgrounds grant +2/+1 or +1/+1/+1).",
            )
        )
    elif not isinstance(bg_asi_raw, dict):
        errors.append(
            _issue("invalid_background_asi", "background_asi", "background_asi must be a dict mapping abilities to increases.")
        )
    else:
        allowed_bg_abs: set[str] = set()
        if bg_rec is not None:
            allowed_bg_abs = {_extract_slug(a) for a in (bg_rec.get("ability_scores") or [])}

        cleaned_asi: dict[str, int] = {}
        asi_ok = True
        for raw_k, raw_v in bg_asi_raw.items():
            ab = strip_prefix(raw_k)
            if ab not in ABILITY_KEYS:
                asi_ok = False
                errors.append(
                    _issue("invalid_background_asi", f"background_asi.{raw_k}", f"Unknown ability '{raw_k}'.")
                )
                continue
            if not isinstance(raw_v, int) or isinstance(raw_v, bool) or raw_v <= 0:
                asi_ok = False
                errors.append(
                    _issue(
                        "invalid_background_asi",
                        f"background_asi.{ab}",
                        f"Background ASI increase for '{ab}' must be a positive integer.",
                    )
                )
                continue
            if allowed_bg_abs and ab not in allowed_bg_abs:
                asi_ok = False
                errors.append(
                    _issue(
                        "disallowed_background_asi",
                        f"background_asi.{ab}",
                        f"Background '{bg_slug}' only allows ASI in {sorted(allowed_bg_abs)}, not '{ab}'.",
                    )
                )
            cleaned_asi[ab] = int(raw_v)

        vals_sorted = sorted(cleaned_asi.values())
        if vals_sorted not in ([1, 2], [1, 1, 1]):
            asi_ok = False
            errors.append(
                _issue(
                    "invalid_background_asi_distribution",
                    "background_asi",
                    f"2024 background_asi must be +2/+1 or +1/+1/+1 across distinct abilities (got {cleaned_asi}).",
                )
            )

        if asi_ok or cleaned_asi:
            for ab, inc in cleaned_asi.items():
                if abilities[ab] + inc > 20:
                    errors.append(
                        _issue(
                            "ability_cap_exceeded",
                            f"background_asi.{ab}",
                            f"Background ASI pushes '{ab}' to {abilities[ab] + inc} (> 20).",
                        )
                    )
                abilities[ab] += inc

    # ------------------------------------------------------------------
    # Collect Level Features & Species Traits (needed for feats & derivation)
    # ------------------------------------------------------------------
    trait_slugs: list[str] = []
    trait_recs: list[dict[str, Any]] = []
    if species_rec is not None:
        trait_slugs, trait_recs = _collect_species_traits(species_rec, subspecies_rec, level, rules)

    has_versatile = "versatile" in trait_slugs or any(
        bool(t.get("extra_origin_feat")) for t in trait_recs
    )

    class_features: list[str] = []
    level_rec: dict[str, Any] | None = None
    if class_rec is not None:
        for lv in range(1, level + 1):
            lv_entry = rules.get_level(f"{class_slug}-{lv}")
            if lv_entry is not None:
                if lv == level:
                    level_rec = lv_entry
                for f in lv_entry.get("features") or []:
                    f_slug = _extract_slug(f)
                    if f_slug and f_slug not in class_features:
                        class_features.append(f_slug)
            if subclass_slug and lv >= 3:
                sc_lv_entry = rules.get_level(f"{subclass_slug}-{lv}")
                if sc_lv_entry is not None:
                    for f in sc_lv_entry.get("features") or []:
                        f_slug = _extract_slug(f)
                        if f_slug and f_slug not in class_features:
                            class_features.append(f_slug)

    feature_set = set(class_features)

    # ------------------------------------------------------------------
    # 4. Origin Feats, Fighting Style, Level ASIs / Feats
    # ------------------------------------------------------------------
    chosen_feats: list[str] = []

    def _register_feat(
        feat_slug: str,
        path: str,
        pick_level: int,
        expected_types: tuple[str, ...] | None = None,
        enforce_prereqs: bool = True,
    ) -> dict[str, Any] | None:
        rec = rules.get_feat(feat_slug)
        if rec is None:
            errors.append(_issue("unknown_feat", path, f"Unknown feat: '{feat_slug}'."))
            return None
        ftype = str(rec.get("feat_type") or rec.get("type") or "").strip().lower()
        if expected_types is not None and ftype not in expected_types:
            errors.append(
                _issue(
                    "invalid_feat_type",
                    path,
                    f"Feat '{feat_slug}' has type '{ftype}', expected one of {list(expected_types)}.",
                )
            )
        repeatable = bool(rec.get("repeatable", False))
        if feat_slug in chosen_feats and not repeatable:
            errors.append(
                _issue(
                    "duplicate_feat",
                    path,
                    f"Feat '{feat_slug}' is not repeatable and was already selected.",
                )
            )
        if enforce_prereqs:
            failed = _check_feat_prerequisites(
                rec, level, pick_level, abilities, feature_set, class_slug
            )
            for reason in failed:
                errors.append(
                    _issue(
                        "feat_prerequisite_failed",
                        path,
                        f"Feat '{feat_slug}' prerequisite not met: {reason}.",
                    )
                )
        chosen_feats.append(feat_slug)
        return rec

    bg_default_feat = _extract_slug(bg_rec.get("feat")) if (bg_rec and bg_rec.get("feat")) else None
    raw_origin_feat = spec.get("origin_feat")
    origin_feat_slug = strip_prefix(raw_origin_feat) if raw_origin_feat else bg_default_feat

    if origin_feat_slug:
        of_rec = _register_feat(
            origin_feat_slug,
            "origin_feat",
            pick_level=1,
            expected_types=("origin",),
            enforce_prereqs=True,
        )
        if bg_default_feat and raw_origin_feat and origin_feat_slug != bg_default_feat:
            errors.append(
                _issue(
                    "background_origin_feat_mismatch",
                    "origin_feat",
                    f"Background '{bg_slug}' grants Origin feat '{bg_default_feat}', not '{origin_feat_slug}'.",
                )
            )
        _ = of_rec

    raw_extra_origin = spec.get("extra_origin_feat")
    if raw_extra_origin:
        extra_slug = strip_prefix(raw_extra_origin)
        if not has_versatile:
            errors.append(
                _issue(
                    "extra_origin_feat_not_allowed",
                    "extra_origin_feat",
                    f"Species '{species_slug}' does not have the Versatile trait for an extra Origin feat.",
                )
            )
        _register_feat(
            extra_slug,
            "extra_origin_feat",
            pick_level=1,
            expected_types=("origin",),
            enforce_prereqs=True,
        )

    raw_fs = spec.get("fighting_style")
    if raw_fs:
        fs_slug = strip_prefix(raw_fs)
        _register_feat(
            fs_slug,
            "fighting_style",
            pick_level=level,
            expected_types=("fighting-style",),
            enforce_prereqs=True,
        )

    # Level ASI / Feat choices.
    asi_choices = spec.get("asi_choices") or []
    allowed_asi_levels: set[int] = set()
    if class_rec is not None:
        allowed_asi_levels = {lv for lv in _asi_levels_for_class(class_slug, class_rec) if lv <= level}
        if level >= 19:
            allowed_asi_levels.add(19)
    used_asi_levels: set[int] = set()

    if isinstance(asi_choices, list):
        for idx, choice in enumerate(asi_choices):
            cpath = f"asi_choices[{idx}]"
            if not isinstance(choice, dict):
                errors.append(_issue("invalid_asi_choice", cpath, "Each ASI choice must be a JSON object."))
                continue
            c_lvl = choice.get("level")
            if not isinstance(c_lvl, int) or isinstance(c_lvl, bool):
                errors.append(_issue("invalid_asi_level", f"{cpath}.level", "ASI choice level must be an integer."))
                continue
            if c_lvl > level or (class_rec is not None and c_lvl not in allowed_asi_levels):
                errors.append(
                    _issue(
                        "invalid_asi_level",
                        f"{cpath}.level",
                        f"Level {c_lvl} is not an eligible ASI/Feat level for {class_slug} level {level} "
                        f"(allowed: {sorted(allowed_asi_levels)}).",
                    )
                )
            elif c_lvl in used_asi_levels:
                errors.append(
                    _issue(
                        "duplicate_asi_level",
                        f"{cpath}.level",
                        f"Duplicate ASI choice at level {c_lvl}.",
                    )
                )
            else:
                used_asi_levels.add(c_lvl)

            ctype = str(choice.get("type") or "asi").strip().lower()
            if ctype == "asi":
                incs = choice.get("increases")
                if not isinstance(incs, dict) or not incs:
                    errors.append(
                        _issue("invalid_asi_increases", f"{cpath}.increases", "ASI increases must be a non-empty dict.")
                    )
                    continue
                total_inc = 0
                for raw_ab, delta in incs.items():
                    ab = strip_prefix(raw_ab)
                    if ab not in ABILITY_KEYS:
                        errors.append(
                            _issue("invalid_asi_increases", f"{cpath}.increases.{raw_ab}", f"Unknown ability '{raw_ab}'.")
                        )
                        continue
                    if not isinstance(delta, int) or isinstance(delta, bool) or delta not in (1, 2):
                        errors.append(
                            _issue("invalid_asi_increases", f"{cpath}.increases.{ab}", "ASI increase must be 1 or 2.")
                        )
                        continue
                    total_inc += delta
                    if abilities[ab] + delta > 20:
                        errors.append(
                            _issue(
                                "ability_cap_exceeded",
                                f"{cpath}.increases.{ab}",
                                f"ASI increases '{ab}' to {abilities[ab] + delta} (> 20).",
                            )
                        )
                    abilities[ab] += delta
                if total_inc != 2:
                    errors.append(
                        _issue(
                            "invalid_asi_increases",
                            f"{cpath}.increases",
                            f"ASI must total +2 (got +{total_inc}).",
                        )
                    )
            elif ctype == "feat":
                feat_raw = choice.get("feat")
                if not feat_raw:
                    errors.append(_issue("missing_feat", f"{cpath}.feat", "Feat choice requires 'feat' field."))
                    continue
                f_slug = strip_prefix(feat_raw)
                f_rec = _register_feat(
                    f_slug,
                    f"{cpath}.feat",
                    pick_level=c_lvl,
                    expected_types=None,
                    enforce_prereqs=True,
                )
                cap = 30 if (f_rec and str(f_rec.get("feat_type") or f_rec.get("type") or "").lower() == "epic-boon") else 20
                ab_inc = choice.get("ability_increase")
                if isinstance(ab_inc, str) and ab_inc.strip():
                    ab = strip_prefix(ab_inc)
                    if ab not in ABILITY_KEYS:
                        errors.append(
                            _issue("invalid_asi_increases", f"{cpath}.ability_increase", f"Unknown ability '{ab_inc}'.")
                        )
                    else:
                        if abilities[ab] + 1 > cap:
                            errors.append(
                                _issue(
                                    "ability_cap_exceeded",
                                    f"{cpath}.ability_increase",
                                    f"Feat ability increase pushes '{ab}' to {abilities[ab] + 1} (> {cap}).",
                                )
                            )
                        abilities[ab] += 1
                elif isinstance(ab_inc, dict):
                    for raw_ab, delta in ab_inc.items():
                        ab = strip_prefix(raw_ab)
                        if ab in ABILITY_KEYS and isinstance(delta, int) and not isinstance(delta, bool):
                            if abilities[ab] + delta > cap:
                                errors.append(
                                    _issue(
                                        "ability_cap_exceeded",
                                        f"{cpath}.ability_increase.{ab}",
                                        f"Feat ability increase pushes '{ab}' to {abilities[ab] + delta} (> {cap}).",
                                    )
                                )
                            abilities[ab] += delta
                if f_slug == "ability-score-improvement" and isinstance(choice.get("increases"), dict):
                    for raw_ab, delta in choice["increases"].items():
                        ab = strip_prefix(raw_ab)
                        if ab in ABILITY_KEYS and isinstance(delta, int) and not isinstance(delta, bool):
                            if abilities[ab] + delta > 20:
                                errors.append(
                                    _issue(
                                        "ability_cap_exceeded",
                                        f"{cpath}.increases.{ab}",
                                        f"ASI increases '{ab}' to {abilities[ab] + delta} (> 20).",
                                    )
                                )
                            abilities[ab] += delta
            else:
                errors.append(
                    _issue("invalid_asi_choice", f"{cpath}.type", f"Unsupported ASI choice type '{ctype}'.")
                )

    # Also validate any additional top-level `feats` list entries if provided directly.
    raw_extra_feats = spec.get("feats")
    if isinstance(raw_extra_feats, list):
        already_accounted = list(chosen_feats)
        for idx, rf in enumerate(raw_extra_feats):
            f_slug = strip_prefix(rf)
            if f_slug in already_accounted:
                already_accounted.remove(f_slug)
                continue
            _register_feat(
                f_slug,
                f"feats[{idx}]",
                pick_level=level,
                expected_types=None,
                enforce_prereqs=True,
            )

    # ------------------------------------------------------------------
    # 5. Weapon Masteries
    # ------------------------------------------------------------------
    max_masteries = _weapon_mastery_allowance(class_slug, class_rec, level) if class_rec else 0
    raw_wm_weapons = spec.get("weapon_mastery_weapons")
    raw_wm_slugs = spec.get("weapon_masteries")

    active_mastery_weapons: set[str] = set()
    active_mastery_slugs: set[str] = set()

    if isinstance(raw_wm_weapons, list) and raw_wm_weapons:
        if len(raw_wm_weapons) > max_masteries:
            errors.append(
                _issue(
                    "weapon_mastery_limit_exceeded",
                    "weapon_mastery_weapons",
                    f"{class_slug or 'Class'} level {level} allows at most {max_masteries} weapon masteries "
                    f"(got {len(raw_wm_weapons)}).",
                )
            )
        for idx, rw in enumerate(raw_wm_weapons):
            w_slug = strip_prefix(rw)
            w_rec = rules.get_equipment(w_slug)
            if w_rec is None or w_rec.get("kind") != "weapon":
                errors.append(
                    _issue(
                        "invalid_mastery_weapon",
                        f"weapon_mastery_weapons[{idx}]",
                        f"'{rw}' is not a valid weapon for Weapon Mastery.",
                    )
                )
            else:
                active_mastery_weapons.add(w_slug)

    if isinstance(raw_wm_slugs, list) and raw_wm_slugs:
        if len(raw_wm_slugs) > max_masteries:
            errors.append(
                _issue(
                    "weapon_mastery_limit_exceeded",
                    "weapon_masteries",
                    f"{class_slug or 'Class'} level {level} allows at most {max_masteries} weapon masteries "
                    f"(got {len(raw_wm_slugs)}).",
                )
            )
        for idx, rm in enumerate(raw_wm_slugs):
            m_slug = strip_prefix(rm)
            if rules.get_weapon_mastery(m_slug) is None:
                errors.append(
                    _issue(
                        "unknown_weapon_mastery",
                        f"weapon_masteries[{idx}]",
                        f"Unknown weapon mastery '{rm}'.",
                    )
                )
            else:
                active_mastery_slugs.add(m_slug)

    # ------------------------------------------------------------------
    # 6. Equipment (Armor, Shield, Weapons)
    # ------------------------------------------------------------------
    class_profs: set[str] = (
        {_extract_slug(p) for p in (class_rec.get("proficiencies") or [])}
        if class_rec
        else set()
    )
    equip_spec = spec.get("equipment") or {}
    if not isinstance(equip_spec, dict):
        errors.append(_issue("invalid_equipment", "equipment", "equipment must be a JSON object."))
        equip_spec = {}

    armor_raw = equip_spec.get("armor")
    armor_rec: dict[str, Any] | None = None
    heavy_armor_speed_penalty = 0
    if armor_raw:
        armor_slug = strip_prefix(armor_raw)
        armor_rec = rules.get_equipment(armor_slug)
        if armor_rec is None or armor_rec.get("kind") != "armor":
            errors.append(_issue("unknown_armor", "equipment.armor", f"Unknown armor '{armor_raw}'."))
            armor_rec = None
        else:
            ac_info = armor_rec.get("armor_class") or {}
            armor_cat = str(armor_rec.get("armor_category") or "").strip().lower()
            if ac_info.get("max_bonus") == 2 and armor_cat == "light":
                armor_cat = "medium"
            if class_rec is not None and not _is_armor_proficient(class_profs, armor_cat):
                warnings.append(
                    _issue(
                        "unproficient_armor",
                        "equipment.armor",
                        f"Class '{class_slug}' lacks proficiency with {armor_cat} armor ('{armor_slug}').",
                    )
                )
            str_min = int(armor_rec.get("str_minimum") or 0)
            if str_min > 0 and abilities["str"] < str_min:
                heavy_armor_speed_penalty = 10
                warnings.append(
                    _issue(
                        "heavy_armor_str_penalty",
                        "equipment.armor",
                        f"'{armor_slug}' requires STR {str_min} (character has STR {abilities['str']}); speed reduced by 10 ft.",
                    )
                )

    shield_raw = equip_spec.get("shield", False)
    has_shield = False
    if isinstance(shield_raw, bool):
        has_shield = shield_raw
    elif isinstance(shield_raw, str) and shield_raw.strip():
        sh_slug = strip_prefix(shield_raw)
        sh_rec = rules.get_equipment(sh_slug)
        if sh_rec is None or sh_rec.get("kind") != "shield":
            errors.append(_issue("unknown_shield", "equipment.shield", f"Unknown shield '{shield_raw}'."))
        else:
            has_shield = True
    if has_shield and class_rec is not None and not _is_armor_proficient(class_profs, "shield"):
        warnings.append(
            _issue(
                "unproficient_armor",
                "equipment.shield",
                f"Class '{class_slug}' lacks proficiency with shields.",
            )
        )

    weapons_raw = equip_spec.get("weapons") or []
    equipped_weapons: list[tuple[str, dict[str, Any], bool]] = []
    if isinstance(weapons_raw, list):
        for idx, rw in enumerate(weapons_raw):
            w_slug = strip_prefix(rw)
            w_rec = rules.get_equipment(w_slug)
            if w_rec is None or w_rec.get("kind") != "weapon":
                errors.append(
                    _issue("unknown_weapon", f"equipment.weapons[{idx}]", f"Unknown weapon '{rw}'.")
                )
                continue
            prof = _is_weapon_proficient(class_profs, w_rec) if class_rec else True
            if not prof:
                warnings.append(
                    _issue(
                        "unproficient_weapon",
                        f"equipment.weapons[{idx}]",
                        f"Class '{class_slug}' lacks proficiency with '{w_slug}'; Proficiency Bonus omitted from attack roll.",
                    )
                )
            equipped_weapons.append((w_slug, w_rec, prof))

    # ------------------------------------------------------------------
    # 7. Spells & Cantrips Validation
    # ------------------------------------------------------------------
    trait_bonus_spells: set[str] = set()
    for t_rec in trait_recs:
        for sp in t_rec.get("spells") or []:
            trait_bonus_spells.add(_extract_slug(sp))
        t_sl = str(t_rec.get("slug") or "")
        if t_sl.startswith("lineage-spell-"):
            trait_bonus_spells.add(t_sl[len("lineage-spell-"):])

    spell_ability = (
        strip_prefix(class_rec["spellcasting_ability"])
        if (class_rec and class_rec.get("spellcasting_ability"))
        else None
    )
    lvl_spellcasting = level_rec.get("spellcasting") if level_rec else None
    slots = _extract_level_slots(lvl_spellcasting)
    max_castable_level = max((int(k) for k in slots.keys()), default=0)
    cantrips_limit = int((lvl_spellcasting or {}).get("cantrips_known") or 0)
    prepared_limit = int((lvl_spellcasting or {}).get("prepared_spells") or 0)
    is_caster = spell_ability is not None and isinstance(lvl_spellcasting, dict)

    raw_cantrips = spec.get("cantrips") or []
    raw_prepared = spec.get("prepared_spells") or []

    validated_cantrips: list[str] = []
    class_cantrip_count = 0
    for idx, rc in enumerate(raw_cantrips):
        sp_slug = strip_prefix(rc)
        sp_rec = rules.get_spell(sp_slug)
        if sp_rec is None:
            errors.append(_issue("unknown_spell", f"cantrips[{idx}]", f"Unknown cantrip '{rc}'."))
            continue
        if int(sp_rec.get("level", -1)) != 0:
            errors.append(
                _issue("not_a_cantrip", f"cantrips[{idx}]", f"Spell '{sp_slug}' is level {sp_rec.get('level')}, not a cantrip.")
            )
            continue
        sp_classes = {_extract_slug(c) for c in (sp_rec.get("classes") or [])}
        from_trait = sp_slug in trait_bonus_spells
        if not is_caster and not from_trait:
            errors.append(
                _issue("not_a_spellcaster", f"cantrips[{idx}]", f"Class '{class_slug}' cannot learn cantrip '{sp_slug}'.")
            )
            continue
        if is_caster and class_slug not in sp_classes and not from_trait:
            errors.append(
                _issue(
                    "spell_not_in_class_list",
                    f"cantrips[{idx}]",
                    f"Cantrip '{sp_slug}' is not on the '{class_slug}' spell list.",
                )
            )
            continue
        if not from_trait or class_slug in sp_classes:
            if not from_trait:
                class_cantrip_count += 1
        if sp_slug not in validated_cantrips:
            validated_cantrips.append(sp_slug)

    if is_caster and cantrips_limit > 0:
        if class_cantrip_count > cantrips_limit:
            errors.append(
                _issue(
                    "cantrips_limit_exceeded",
                    "cantrips",
                    f"{class_slug} level {level} knows at most {cantrips_limit} cantrips (got {class_cantrip_count}).",
                )
            )
        elif class_cantrip_count < cantrips_limit:
            warnings.append(
                _issue(
                    "cantrips_under_selected",
                    "cantrips",
                    f"{class_slug} level {level} can know {cantrips_limit} cantrips ({class_cantrip_count} selected).",
                )
            )

    validated_prepared: list[str] = []
    class_prepared_count = 0
    for idx, rp in enumerate(raw_prepared):
        sp_slug = strip_prefix(rp)
        sp_rec = rules.get_spell(sp_slug)
        if sp_rec is None:
            errors.append(_issue("unknown_spell", f"prepared_spells[{idx}]", f"Unknown spell '{rp}'."))
            continue
        sp_lvl = int(sp_rec.get("level", 0))
        if sp_lvl == 0:
            errors.append(
                _issue(
                    "cantrip_in_prepared_spells",
                    f"prepared_spells[{idx}]",
                    f"'{sp_slug}' is a cantrip; list it under 'cantrips' instead of 'prepared_spells'.",
                )
            )
            continue
        sp_classes = {_extract_slug(c) for c in (sp_rec.get("classes") or [])}
        from_trait = sp_slug in trait_bonus_spells
        if not is_caster and not from_trait:
            errors.append(
                _issue(
                    "not_a_spellcaster",
                    f"prepared_spells[{idx}]",
                    f"Class '{class_slug}' cannot prepare spell '{sp_slug}'.",
                )
            )
            continue
        if is_caster and class_slug not in sp_classes and not from_trait:
            errors.append(
                _issue(
                    "spell_not_in_class_list",
                    f"prepared_spells[{idx}]",
                    f"Spell '{sp_slug}' is not on the '{class_slug}' spell list.",
                )
            )
            continue
        if not from_trait and sp_lvl > max_castable_level:
            errors.append(
                _issue(
                    "spell_level_too_high",
                    f"prepared_spells[{idx}]",
                    f"Spell '{sp_slug}' (level {sp_lvl}) exceeds max castable slot level {max_castable_level} for {class_slug} level {level}.",
                )
            )
            continue
        if not from_trait:
            class_prepared_count += 1
        if sp_slug not in validated_prepared:
            validated_prepared.append(sp_slug)

    if is_caster and prepared_limit > 0:
        if class_prepared_count > prepared_limit:
            errors.append(
                _issue(
                    "prepared_spells_limit_exceeded",
                    "prepared_spells",
                    f"{class_slug} level {level} can prepare at most {prepared_limit} spells (got {class_prepared_count}).",
                )
            )
        elif class_prepared_count < prepared_limit:
            warnings.append(
                _issue(
                    "prepared_spells_under_selected",
                    "prepared_spells",
                    f"{class_slug} level {level} can prepare {prepared_limit} spells ({class_prepared_count} selected).",
                )
            )

    # ------------------------------------------------------------------
    # Fatal entity check before deriving statblock
    # ------------------------------------------------------------------
    if species_rec is None or bg_rec is None or class_rec is None or not level_valid:
        return {
            "status": "invalid",
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "statblock": None,
        }

    # ------------------------------------------------------------------
    # 8. Derive Authoritative 2024 Statblock (§4.4)
    # ------------------------------------------------------------------
    mods = {ab: ability_mod(abilities[ab]) for ab in ABILITY_KEYS}
    pb = int(level_rec["prof_bonus"]) if (level_rec and "prof_bonus" in level_rec) else (2 + (level - 1) // 4)
    hit_die = int(class_rec.get("hit_die") or 8)

    # HP calculation.
    toughness_bonus_per_lvl = 0
    for t_rec in trait_recs:
        if t_rec.get("hp_per_level_bonus"):
            toughness_bonus_per_lvl += int(t_rec["hp_per_level_bonus"])
        elif str(t_rec.get("slug") or "") == "dwarven-toughness":
            toughness_bonus_per_lvl += 1
    if "dwarven-toughness" in trait_slugs and toughness_bonus_per_lvl == 0:
        toughness_bonus_per_lvl = 1

    hp_method = str(spec.get("hp_method") or "fixed").strip().lower()
    hp_rolls = spec.get("hp_rolls") or []
    hp_max = max(1, hit_die + mods["con"]) + toughness_bonus_per_lvl
    if level > 1:
        if hp_method == "rolled" and isinstance(hp_rolls, list) and len(hp_rolls) >= level - 1:
            for idx in range(level - 1):
                r = int(hp_rolls[idx])
                if r < 1 or r > hit_die:
                    errors.append(
                        _issue(
                            "invalid_hp_roll",
                            f"hp_rolls[{idx}]",
                            f"HP roll {r} out of range 1..{hit_die}.",
                        )
                    )
                hp_max += max(1, r + mods["con"]) + toughness_bonus_per_lvl
        else:
            fixed_die = (hit_die // 2) + 1
            hp_max += (level - 1) * (max(1, fixed_die + mods["con"]) + toughness_bonus_per_lvl)

    # AC calculation.
    has_defense = "defense" in chosen_feats
    stealth_disadvantage = False
    if armor_rec is not None:
        ac_info = armor_rec.get("armor_class") or {}
        base_ac = int(ac_info.get("base", 10))
        dex_bonus = bool(ac_info.get("dex_bonus", False))
        max_bonus = ac_info.get("max_bonus")
        dex_part = (
            min(mods["dex"], int(max_bonus))
            if (dex_bonus and max_bonus is not None)
            else (mods["dex"] if dex_bonus else 0)
        )
        ac = base_ac + dex_part
        parts = [f"{armor_rec.get('name', 'Armor')} ({base_ac})"]
        if dex_bonus and dex_part != 0:
            sign = "+" if dex_part > 0 else ""
            parts.append(f"DEX ({sign}{dex_part})")
        if has_shield:
            ac += 2
            parts.append("Shield (+2)")
        if has_defense:
            ac += 1
            parts.append("Defense (+1)")
        ac_breakdown = " + ".join(parts)
        stealth_disadvantage = bool(armor_rec.get("stealth_disadvantage", False))
    else:
        candidates: list[tuple[int, str]] = []
        base_unarmored = 10 + mods["dex"] + (2 if has_shield else 0)
        base_desc = f"Unarmored (10 + DEX {mods['dex']:+d})" + (" + Shield (+2)" if has_shield else "")
        candidates.append((base_unarmored, base_desc))

        if class_slug == "barbarian":
            barb_ac = 10 + mods["dex"] + mods["con"] + (2 if has_shield else 0)
            barb_desc = (
                f"Barbarian Unarmored Defense (10 + DEX {mods['dex']:+d} + CON {mods['con']:+d})"
                + (" + Shield (+2)" if has_shield else "")
            )
            candidates.append((barb_ac, barb_desc))

        if class_slug == "monk" and not has_shield:
            monk_ac = 10 + mods["dex"] + mods["wis"]
            monk_desc = f"Monk Unarmored Defense (10 + DEX {mods['dex']:+d} + WIS {mods['wis']:+d})"
            candidates.append((monk_ac, monk_desc))

        ac, ac_breakdown = max(candidates, key=lambda x: x[0])

    # Initiative.
    initiative = mods["dex"] + (pb if "alert" in chosen_feats else 0)

    # Speed.
    speed_ft = int(species_rec.get("speed_ft") or species_rec.get("speed") or 30)
    for t_rec in trait_recs:
        override = t_rec.get("speed_override_ft")
        if override is not None:
            speed_ft = max(speed_ft, int(override))
    if subspecies_slug == "elven-lineage-wood-elf" or "wood-elf-speed-increase" in trait_slugs:
        speed_ft = max(speed_ft, 35)

    if class_slug == "monk" and armor_rec is None and not has_shield:
        monk_bonus = 0
        if level_rec and isinstance(level_rec.get("class_specific"), dict):
            cs = level_rec["class_specific"]
            raw_um = cs.get("unarmored_movement_bonus")
            if raw_um is None:
                raw_um = cs.get("unarmored_movement")
            if raw_um is not None:
                monk_bonus = int(raw_um)
            else:
                monk_bonus = _FALLBACK_MONK_UNARMORED_MOVEMENT.get(level, 0)
        else:
            monk_bonus = _FALLBACK_MONK_UNARMORED_MOVEMENT.get(level, 0)
        speed_ft += monk_bonus

    speed_ft = max(0, speed_ft - heavy_armor_speed_penalty)

    # Saving throws.
    save_profs = sorted({_extract_slug(s) for s in (class_rec.get("saving_throws") or [])})
    saving_throws = {
        ab: mods[ab] + (pb if ab in save_profs else 0) for ab in ABILITY_KEYS
    }

    # Skills.
    all_skills = rules.get_skills()
    if not all_skills:
        all_skills = {
            "acrobatics": {"ability": "dex"},
            "animal-handling": {"ability": "wis"},
            "arcana": {"ability": "int"},
            "athletics": {"ability": "str"},
            "deception": {"ability": "cha"},
            "history": {"ability": "int"},
            "insight": {"ability": "wis"},
            "intimidation": {"ability": "cha"},
            "investigation": {"ability": "int"},
            "medicine": {"ability": "wis"},
            "nature": {"ability": "int"},
            "perception": {"ability": "wis"},
            "performance": {"ability": "cha"},
            "persuasion": {"ability": "cha"},
            "religion": {"ability": "int"},
            "sleight-of-hand": {"ability": "dex"},
            "stealth": {"ability": "dex"},
            "survival": {"ability": "wis"},
        }

    skill_profs_set: set[str] = set()
    for bp in bg_rec.get("proficiencies") or []:
        sl = _extract_slug(bp)
        if sl.startswith("skill-"):
            sl = sl[len("skill-"):]
        if sl in all_skills:
            skill_profs_set.add(sl)

    for idx, sp_raw in enumerate(spec.get("skill_proficiencies") or []):
        sl = strip_prefix(sp_raw)
        if sl.startswith("skill-"):
            sl = sl[len("skill-"):]
        if sl not in all_skills:
            errors.append(
                _issue("unknown_skill", f"skill_proficiencies[{idx}]", f"Unknown skill '{sp_raw}'.")
            )
        else:
            skill_profs_set.add(sl)

    skill_profs = sorted(skill_profs_set)
    skills: dict[str, int] = {}
    for sk in sorted(all_skills.keys()):
        sk_entry = all_skills[sk]
        sk_ab = _extract_slug(sk_entry.get("ability") or sk_entry.get("ability_score") or "dex")
        skills[sk] = mods.get(sk_ab, 0) + (pb if sk in skill_profs_set else 0)

    passive_perception = 10 + skills.get("perception", mods["wis"])

    # Senses & Damage Resistances.
    darkvision_ft = 0
    dmg_resistances: set[str] = set()
    for t_rec in trait_recs:
        dv = int(t_rec.get("darkvision_ft") or 0)
        if dv > darkvision_ft:
            darkvision_ft = dv
        for dr in t_rec.get("damage_resistances") or []:
            dmg_resistances.add(_extract_slug(dr))
        tsl = str(t_rec.get("slug") or "")
        if tsl == "dwarven-resilience":
            dmg_resistances.add("poison")
        elif tsl == "darkvision-60":
            darkvision_ft = max(darkvision_ft, 60)
        elif tsl == "darkvision-120":
            darkvision_ft = max(darkvision_ft, 120)
        elif tsl.startswith("lineage-resistance-"):
            dmg_resistances.add(tsl[len("lineage-resistance-"):])
        elif tsl.startswith("draconic-damage-resistance-"):
            dmg_resistances.add(tsl[len("draconic-damage-resistance-"):])

    if subspecies_rec and subspecies_rec.get("damage_type"):
        dmg_resistances.add(_extract_slug(subspecies_rec["damage_type"]))
    if "dwarven-resilience" in trait_slugs:
        dmg_resistances.add("poison")

    # Weapon Profiles.
    has_archery = "archery" in chosen_feats
    weapon_profiles: list[dict[str, Any]] = []
    for w_slug, w_rec, prof in equipped_weapons:
        raw_atk_ab = str(w_rec.get("attack_ability") or "").strip().lower()
        props = sorted(str(p).lower() for p in (w_rec.get("properties") or []))
        w_range = str(w_rec.get("weapon_range") or "melee").strip().lower()
        if raw_atk_ab == "finesse" or "finesse" in props:
            atk_ab = "str" if mods["str"] >= mods["dex"] else "dex"
        elif raw_atk_ab in ABILITY_KEYS:
            atk_ab = raw_atk_ab
        elif w_range == "ranged":
            atk_ab = "dex"
        else:
            atk_ab = "str"

        # Monk Martial Arts allows DEX for simple melee or Light martial melee weapons.
        if class_slug == "monk" and w_range == "melee":
            w_cat = str(w_rec.get("weapon_category") or "").lower()
            if (w_cat == "simple" or "light" in props) and mods["dex"] > mods[atk_ab]:
                atk_ab = "dex"

        atk_bonus = mods[atk_ab] + (pb if prof else 0) + (2 if (has_archery and w_range == "ranged") else 0)
        dmg_bonus = mods[atk_ab]
        mastery_slug = str(w_rec.get("mastery") or "").strip().lower() or None

        if active_mastery_weapons:
            mastery_active = w_slug in active_mastery_weapons
        elif active_mastery_slugs:
            mastery_active = bool(mastery_slug and mastery_slug in active_mastery_slugs)
        else:
            mastery_active = False

        weapon_profiles.append(
            {
                "weapon_id": str(w_rec.get("id") or f"equipment:{w_slug}"),
                "slug": w_slug,
                "name": str(w_rec.get("name") or w_slug),
                "proficient": prof,
                "weapon_range": w_range,
                "attack_ability": atk_ab,
                "attack_bonus": atk_bonus,
                "damage_dice": str(w_rec.get("damage_dice") or "1"),
                "damage_bonus": dmg_bonus,
                "damage_type": str(w_rec.get("damage_type") or "bludgeoning"),
                "versatile_dice": w_rec.get("versatile_dice"),
                "properties": props,
                "mastery": mastery_slug,
                "mastery_active": mastery_active,
                "reach_ft": w_rec.get("reach_ft"),
                "range_normal_ft": w_rec.get("range_normal_ft"),
                "range_long_ft": w_rec.get("range_long_ft"),
                "throw_range": w_rec.get("throw_range"),
            }
        )

    # Spellcasting Block.
    spellcasting_out: dict[str, Any] | None = None
    if is_caster and spell_ability is not None:
        sp_mod = mods[spell_ability]
        spellcasting_out = {
            "ability": spell_ability,
            "spell_save_dc": 8 + pb + sp_mod,
            "spell_attack_bonus": pb + sp_mod,
            "slots": slots,
            "is_pact_magic": class_slug == "warlock",
            "cantrips": validated_cantrips,
            "prepared_spells": validated_prepared,
        }

    statblock: dict[str, Any] = {
        "name": name,
        "species": f"species:{species_slug}",
        "subspecies": f"subspecies:{subspecies_slug}" if subspecies_slug else None,
        "size": size,
        "background": f"backgrounds:{bg_slug}",
        "class": f"classes:{class_slug}",
        "subclass": f"subclasses:{subclass_slug}" if subclass_slug else None,
        "level": level,
        "proficiency_bonus": pb,
        "abilities": abilities,
        "ability_mods": mods,
        "hp_max": hp_max,
        "hit_die": hit_die,
        "ac": ac,
        "ac_breakdown": ac_breakdown,
        "stealth_disadvantage": stealth_disadvantage,
        "initiative": initiative,
        "speed_ft": speed_ft,
        "saving_throws": saving_throws,
        "save_proficiencies": save_profs,
        "skills": skills,
        "skill_proficiencies": skill_profs,
        "passive_perception": passive_perception,
        "senses": {"darkvision_ft": darkvision_ft},
        "damage_resistances": sorted(dmg_resistances),
        "features": class_features,
        "traits": trait_slugs,
        "feats": chosen_feats,
        "extra_attacks": _extra_attacks_for_class(class_slug, level),
        "weapon_profiles": weapon_profiles,
        "spellcasting": spellcasting_out,
    }

    is_valid = len(errors) == 0
    return {
        "status": "ok" if is_valid else "invalid",
        "valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "statblock": statblock,
    }
