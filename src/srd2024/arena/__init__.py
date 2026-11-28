"""Stage 3 Multi-Agent 5e 2024 (SRD 5.2) Tabletop & Model Eval Arena public API."""

from .agents import (
    AgentDecision,
    AgentDriver,
    AnthropicDriver,
    GeminiDriver,
    GreedyDriver,
    HeuristicTacticalDriver,
    OpenAICompatDriver,
    RandomDriver,
    ReplayDriver,
    create_driver,
    extract_decision_json,
)
from .human import (
    HumanDriver,
    HumanSessionPause,
    build_human_action_menu,
    render_tactical_hud,
)
from .render import (
    render_benchmark_markdown,
    render_match_markdown,
    render_replay_markdown,
    render_scenarios_markdown,
)
from .runner import (
    ENGINE_VERSION,
    STATE_SHAPE_VERSION,
    build_observation,
    build_trace_provenance,
    compute_rules_digest,
    compute_transcript_digest,
    replay_match,
    run_benchmark,
    run_match,
)
from .scenarios import BUILTIN_SCENARIO_IDS, list_scenarios, load_scenario
from .scoring import score_match
from .session import (
    load_session_checkpoint,
    resolve_seat_drivers,
    run_interactive_session,
    save_session_checkpoint,
)

__all__ = [
    "BUILTIN_SCENARIO_IDS",
    "list_scenarios",
    "load_scenario",
    "AgentDecision",
    "AgentDriver",
    "RandomDriver",
    "GreedyDriver",
    "HeuristicTacticalDriver",
    "ReplayDriver",
    "HumanDriver",
    "HumanSessionPause",
    "build_human_action_menu",
    "render_tactical_hud",
    "OpenAICompatDriver",
    "AnthropicDriver",
    "GeminiDriver",
    "extract_decision_json",
    "create_driver",
    "ENGINE_VERSION",
    "STATE_SHAPE_VERSION",
    "build_observation",
    "build_trace_provenance",
    "compute_rules_digest",
    "run_match",
    "replay_match",
    "run_benchmark",
    "compute_transcript_digest",
    "score_match",
    "resolve_seat_drivers",
    "run_interactive_session",
    "save_session_checkpoint",
    "load_session_checkpoint",
    "render_scenarios_markdown",
    "render_match_markdown",
    "render_benchmark_markdown",
    "render_replay_markdown",
]
