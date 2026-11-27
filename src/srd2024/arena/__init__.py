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
from .render import (
    render_benchmark_markdown,
    render_match_markdown,
    render_replay_markdown,
    render_scenarios_markdown,
)
from .runner import (
    build_observation,
    compute_transcript_digest,
    replay_match,
    run_benchmark,
    run_match,
)
from .scenarios import BUILTIN_SCENARIO_IDS, list_scenarios, load_scenario
from .scoring import score_match

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
    "OpenAICompatDriver",
    "AnthropicDriver",
    "GeminiDriver",
    "extract_decision_json",
    "create_driver",
    "build_observation",
    "run_match",
    "replay_match",
    "run_benchmark",
    "compute_transcript_digest",
    "score_match",
    "render_scenarios_markdown",
    "render_match_markdown",
    "render_benchmark_markdown",
    "render_replay_markdown",
]
