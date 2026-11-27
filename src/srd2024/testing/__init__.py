"""Stage 4 testing utilities for 5e 2024 (SRD 5.2).

Exports Deterministic Simulation Testing (DST) and stateful fuzzing primitives.
Other submodules (such as AST mutation testing) are intentionally not imported
eagerly so independent tracks remain decoupled.
"""

from __future__ import annotations

from .dst import (
    build_procedural_scenario,
    capture_initial_max_slots,
    check_state_invariants,
    generate_adversarial_intent,
    generate_random_legal_intent,
    main_dst,
    render_dst_markdown,
    run_dst_seed,
    run_dst_suite,
)

__all__ = [
    "build_procedural_scenario",
    "capture_initial_max_slots",
    "check_state_invariants",
    "generate_adversarial_intent",
    "generate_random_legal_intent",
    "main_dst",
    "render_dst_markdown",
    "run_dst_seed",
    "run_dst_suite",
]
