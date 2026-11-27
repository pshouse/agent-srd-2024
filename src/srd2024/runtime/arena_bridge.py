"""Lazy bridge and CLI entrypoint for Stage 3 Multi-Agent 5e 2024 Arena surfaces.

Strictly read-only over `dist/rules.json` via `srd2024.engine` and lazy-imports
`srd2024.arena` only when Stage 3 operations are invoked.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .render import render_operation
from .rules_bridge import load_rules_store

_LLM_SPEC_PREFIXES = ("openai-compat:", "anthropic:", "gemini:")


def _parse_csv_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    items: list[str] = []
    for raw in values:
        for part in raw.split(","):
            token = part.strip()
            if token:
                items.append(token)
    return items or None


def _parse_seeds_arg(values: list[str] | None) -> list[int] | None:
    if not values:
        return None
    seeds: list[int] = []
    for raw in values:
        text = raw.strip()
        if not text:
            continue
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid --seeds JSON: {exc.msg}") from exc
            if not isinstance(parsed, list) or any(
                isinstance(x, bool) or not isinstance(x, int) for x in parsed
            ):
                raise ValueError("--seeds JSON must be an array of integers.")
            seeds.extend(parsed)
            continue
        for part in text.split(","):
            token = part.strip()
            if not token:
                continue
            try:
                seeds.append(int(token))
            except ValueError as exc:
                raise ValueError(f"Invalid integer '{token}' in --seeds.") from exc
    if not seeds:
        raise ValueError("--seeds must include at least one integer.")
    return seeds


def _load_json_file_or_stdin(raw: str, *, label: str) -> Any:
    text = raw.strip()
    if not text:
        raise ValueError(f"Empty {label} path.")
    if text == "-":
        content = sys.stdin.read()
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on stdin for {label}: {exc.msg}") from exc
    path = Path(text).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{label} file not found: '{path}'")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {label} file '{path}': {exc.msg}") from exc


def _parse_scenario_arg(raw: str) -> str | dict[str, Any]:
    text = raw.strip()
    if not text:
        raise ValueError("Scenario argument must be a non-empty scenario ID or JSON path.")
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid inline scenario JSON: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Inline scenario JSON must be an object.")
        return parsed
    return text


def _apply_llm_config(
    spec: str | dict[str, Any],
    *,
    api_base: str | None = None,
    api_key_env: str | None = None,
) -> str | dict[str, Any]:
    if isinstance(spec, dict):
        out = dict(spec)
        if api_base is not None and "api_base" not in out:
            out["api_base"] = api_base
        if api_key_env is not None and "api_key_env" not in out:
            out["api_key_env"] = api_key_env
        return out
    if not spec.startswith(_LLM_SPEC_PREFIXES) or (api_base is None and api_key_env is None):
        return spec
    cfg: dict[str, Any] = {"spec": spec}
    if api_base is not None:
        cfg["api_base"] = api_base
    if api_key_env is not None:
        cfg["api_key_env"] = api_key_env
    return cfg


def execute_arena_scenarios() -> dict[str, Any]:
    """List built-in 5e 2024 (SRD 5.2) arena scenarios."""
    from srd2024.arena import list_scenarios

    scenarios = list_scenarios()
    return {
        "status": "ok",
        "schema_version": "3",
        "scenarios": scenarios,
    }


def get_arena_scenarios_resource() -> dict[str, Any]:
    """Return JSON payload for the `srd://arena/scenarios` MCP resource."""
    from srd2024.arena import list_scenarios

    return {
        "schema_version": "3",
        "scenarios": list_scenarios(),
    }


def execute_arena_run(
    rules: Any,
    *,
    scenario: str | Path | dict[str, Any],
    party_agent: str | dict[str, Any] = "heuristic-tactical",
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    seed: int = 42,
    max_rounds: int | None = None,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
    api_base: str | None = None,
    api_key_env: str | None = None,
    enemy_api_base: str | None = None,
    enemy_api_key_env: str | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Run a single deterministic 5e 2024 arena match via `srd2024.arena`."""
    from srd2024.arena import create_driver, run_match

    if max_rounds is not None and max_rounds < 1:
        raise ValueError("max_rounds must be >= 1.")
    if max_actions_per_turn < 1:
        raise ValueError("max_actions_per_turn must be >= 1.")
    if max_retries_per_action < 0:
        raise ValueError("max_retries_per_action must be >= 0.")

    party_driver = create_driver(
        party_agent,
        rules=rules,
        seed=seed,
        api_base=api_base,
        api_key_env=api_key_env,
        temperature=temperature,
    )
    enemy_driver = create_driver(
        enemy_agent,
        rules=rules,
        seed=seed + 1,
        api_base=enemy_api_base,
        api_key_env=enemy_api_key_env,
        temperature=temperature,
    )
    return run_match(
        scenario,
        party_driver=party_driver,
        enemy_driver=enemy_driver,
        rules=rules,
        seed=seed,
        max_rounds=max_rounds,
        max_actions_per_turn=max_actions_per_turn,
        max_retries_per_action=max_retries_per_action,
    )


def execute_arena_bench(
    rules: Any,
    *,
    scenarios: list[str] | None = None,
    agents: list[str | dict[str, Any]] | None = None,
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    seeds: list[int] | None = None,
    max_rounds: int | None = None,
    api_base: str | None = None,
    api_key_env: str | None = None,
) -> dict[str, Any]:
    """Run a multi-seed / multi-scenario arena benchmark via `srd2024.arena`."""
    from srd2024.arena import run_benchmark

    if max_rounds is not None and max_rounds < 1:
        raise ValueError("max_rounds must be >= 1.")
    if seeds is not None and len(seeds) == 0:
        raise ValueError("seeds must contain at least one integer.")

    contenders: list[str | dict[str, Any]] | None = None
    if agents is not None:
        contenders = [
            _apply_llm_config(spec, api_base=api_base, api_key_env=api_key_env)
            for spec in agents
        ]

    return run_benchmark(
        scenarios=scenarios,
        contenders=contenders,
        enemy_agent=enemy_agent,
        seeds=seeds,
        rules=rules,
        max_rounds=max_rounds,
    )


def execute_arena_replay(rules: Any, transcript: dict[str, Any]) -> dict[str, Any]:
    """Deterministically replay a saved MatchTranscript via `srd2024.arena`."""
    from srd2024.arena import replay_match

    if not isinstance(transcript, dict):
        raise ValueError("Trace JSON must be an object containing a MatchTranscript.")
    return replay_match(transcript, rules)


def execute_arena_play(
    rules: Any,
    *,
    scenario: str | Path | dict[str, Any] | None = None,
    play_as: str | None = None,
    seat_overrides: dict[str, Any] | list[str] | None = None,
    party_agent: str | dict[str, Any] = "heuristic-tactical",
    enemy_agent: str | dict[str, Any] = "heuristic-tactical",
    nl_driver_spec: str | dict[str, Any] | None = None,
    api_base: str | None = None,
    api_key_env: str | None = None,
    temperature: float = 0.0,
    seed: int = 42,
    max_rounds: int | None = None,
    max_actions_per_turn: int = 6,
    max_retries_per_action: int = 2,
    save_path: str | Path | None = None,
    resume_path: str | Path | None = None,
    out_trace: str | Path | None = None,
    force: bool = False,
    input_fn: Any = input,
    output_fn: Any = print,
) -> dict[str, Any]:
    """Run an interactive human-in-the-loop 5e 2024 (SRD 5.2) play session."""
    from srd2024.arena import create_driver, run_interactive_session
    from srd2024.arena.agents import BaseLLMDriver

    if scenario is None and resume_path is None:
        raise ValueError("Provide a scenario ID/path or --resume <checkpoint.json>.")
    if max_rounds is not None and max_rounds < 1:
        raise ValueError("max_rounds must be >= 1.")
    if max_actions_per_turn < 1:
        raise ValueError("max_actions_per_turn must be >= 1.")
    if max_retries_per_action < 0:
        raise ValueError("max_retries_per_action must be >= 0.")

    nl_driver_obj: BaseLLMDriver | None = None
    if nl_driver_spec is not None:
        drv = create_driver(
            nl_driver_spec,
            rules=rules,
            seed=seed,
            api_base=api_base,
            api_key_env=api_key_env,
            temperature=temperature,
        )
        if not isinstance(drv, BaseLLMDriver):
            raise ValueError(
                f"--nl-driver must be a live LLM driver spec (openai-compat:/anthropic:/gemini:), got {nl_driver_spec!r}."
            )
        nl_driver_obj = drv

    return run_interactive_session(
        scenario,
        rules=rules,
        play_as=play_as,
        seat_overrides=seat_overrides,
        party_agent=party_agent,
        enemy_agent=enemy_agent,
        nl_driver=nl_driver_obj,
        input_fn=input_fn,
        output_fn=output_fn,
        seed=seed,
        max_rounds=max_rounds,
        max_actions_per_turn=max_actions_per_turn,
        max_retries_per_action=max_retries_per_action,
        save_path=save_path,
        resume_path=resume_path,
        out_trace=out_trace,
        force=force,
        api_base=api_base,
        api_key_env=api_key_env,
        temperature=temperature,
    )


def add_arena_subcommands(
    subparsers: Any,
    parent_parser: argparse.ArgumentParser,
) -> None:
    """Attach the arena subcommands (`scenarios`, `run`, `bench`, `replay`, `play`)."""
    # 1. scenarios
    subparsers.add_parser(
        "scenarios",
        parents=[parent_parser],
        help="List built-in 5e 2024 (SRD 5.2) arena scenarios.",
    )

    # 2. run
    p_run = subparsers.add_parser(
        "run",
        parents=[parent_parser],
        help="Run a single deterministic multi-agent 5e 2024 arena match.",
    )
    p_run.add_argument(
        "scenario",
        help="Built-in scenario ID (e.g. goblin-ambush, crypt-of-ghouls, wyrmling-lair) or path to custom scenario JSON.",
    )
    p_run.add_argument(
        "--party-agent",
        dest="party_agent",
        default="heuristic-tactical",
        help="Party driver spec (default: heuristic-tactical).",
    )
    p_run.add_argument(
        "--enemy-agent",
        dest="enemy_agent",
        default="heuristic-tactical",
        help="Enemy driver spec (default: heuristic-tactical).",
    )
    p_run.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic PRNG seed (default: 42).",
    )
    p_run.add_argument(
        "--max-rounds",
        dest="max_rounds",
        type=int,
        default=None,
        help="Optional override for maximum combat rounds.",
    )
    p_run.add_argument(
        "--max-actions-per-turn",
        dest="max_actions_per_turn",
        type=int,
        default=6,
        help="Maximum actions per combatant turn (default: 6).",
    )
    p_run.add_argument(
        "--max-retries",
        dest="max_retries",
        type=int,
        default=2,
        help="Maximum retries per rejected action before fallback (default: 2).",
    )
    p_run.add_argument(
        "--api-base",
        dest="api_base",
        default=None,
        help="Optional API base URL for party live LLM driver.",
    )
    p_run.add_argument(
        "--api-key-env",
        dest="api_key_env",
        default=None,
        help="Optional environment variable name holding the party LLM API key.",
    )
    p_run.add_argument(
        "--enemy-api-base",
        dest="enemy_api_base",
        default=None,
        help="Optional API base URL for enemy live LLM driver.",
    )
    p_run.add_argument(
        "--enemy-api-key-env",
        dest="enemy_api_key_env",
        default=None,
        help="Optional environment variable name holding the enemy LLM API key.",
    )
    p_run.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature for live LLM drivers (default: 0.0).",
    )
    p_run.add_argument(
        "--out-trace",
        dest="out_trace",
        default=None,
        help="Write the full MatchTranscript JSON to this file path.",
    )
    p_run.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing --out-trace file.",
    )

    # 3. bench
    p_bench = subparsers.add_parser(
        "bench",
        parents=[parent_parser],
        help="Run a multi-seed, multi-scenario agent evaluation benchmark and leaderboard.",
    )
    p_bench.add_argument(
        "--scenarios",
        action="append",
        default=None,
        help="Comma-separated scenario IDs or JSON paths (default: all 3 built-in scenarios).",
    )
    p_bench.add_argument(
        "--agents",
        action="append",
        default=None,
        help="Comma-separated contender driver specs (default: heuristic-tactical,greedy,random).",
    )
    p_bench.add_argument(
        "--enemy-agent",
        dest="enemy_agent",
        default="heuristic-tactical",
        help="Enemy driver spec (default: heuristic-tactical).",
    )
    p_bench.add_argument(
        "--seeds",
        action="append",
        default=None,
        help="Comma-separated integer PRNG seeds (default: 1,2,3).",
    )
    p_bench.add_argument(
        "--max-rounds",
        dest="max_rounds",
        type=int,
        default=None,
        help="Optional override for maximum combat rounds per match.",
    )
    p_bench.add_argument(
        "--api-base",
        dest="api_base",
        default=None,
        help="Optional API base URL for live LLM contenders.",
    )
    p_bench.add_argument(
        "--api-key-env",
        dest="api_key_env",
        default=None,
        help="Optional environment variable name holding the LLM API key.",
    )
    p_bench.add_argument(
        "--out-json",
        dest="out_json",
        default=None,
        help="Write the full BenchmarkReport JSON to this file path.",
    )

    # 4. replay
    p_replay = subparsers.add_parser(
        "replay",
        parents=[parent_parser],
        help="Deterministically replay a saved MatchTranscript JSON and verify digest parity.",
    )
    p_replay.add_argument(
        "trace_file",
        help="Path to saved MatchTranscript JSON file (or '-' for stdin).",
    )

    # 5. play (Stage 4: Human-in-the-Loop Interactive Tabletop Play)
    p_play = subparsers.add_parser(
        "play",
        parents=[parent_parser],
        help="Play an interactive 5e 2024 (SRD 5.2) tabletop match alongside or against AI agents.",
    )
    p_play.add_argument(
        "scenario",
        nargs="?",
        default=None,
        help="Built-in scenario ID or path to custom scenario JSON (optional when --resume is used).",
    )
    p_play.add_argument(
        "--as",
        dest="play_as",
        default=None,
        help="Seat role to play: a combatant ID (e.g. pc-bram), 'party', or 'dm'.",
    )
    p_play.add_argument(
        "--seat",
        dest="seats",
        action="append",
        default=None,
        help="Per-seat driver override '<combatant_id>=<driver_spec>' (repeatable).",
    )
    p_play.add_argument(
        "--party-agent",
        dest="party_agent",
        default="heuristic-tactical",
        help="Default AI driver for non-human party seats (default: heuristic-tactical).",
    )
    p_play.add_argument(
        "--enemy-agent",
        dest="enemy_agent",
        default="heuristic-tactical",
        help="Default AI driver for non-human enemy seats (default: heuristic-tactical).",
    )
    p_play.add_argument(
        "--nl-driver",
        dest="nl_driver",
        default=None,
        help="Optional LLM driver spec (e.g. openai-compat:gpt-4o-mini) for natural-language-to-intent mode.",
    )
    p_play.add_argument(
        "--api-base",
        dest="api_base",
        default=None,
        help="Optional API base URL for live LLM seat or NL driver.",
    )
    p_play.add_argument(
        "--api-key-env",
        dest="api_key_env",
        default=None,
        help="Optional environment variable name holding the LLM API key.",
    )
    p_play.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature for live LLM seat/NL drivers (default: 0.0).",
    )
    p_play.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic PRNG seed (default: 42).",
    )
    p_play.add_argument(
        "--max-rounds",
        dest="max_rounds",
        type=int,
        default=None,
        help="Optional override for maximum combat rounds.",
    )
    p_play.add_argument(
        "--max-actions-per-turn",
        dest="max_actions_per_turn",
        type=int,
        default=6,
        help="Maximum actions per combatant turn (default: 6).",
    )
    p_play.add_argument(
        "--max-retries",
        dest="max_retries",
        type=int,
        default=2,
        help="Maximum retries per rejected action before fallback (default: 2).",
    )
    p_play.add_argument(
        "--save",
        dest="save_path",
        default=None,
        help="Path to save a resumable checkpoint JSON when pausing with 'save' or 'quit'.",
    )
    p_play.add_argument(
        "--resume",
        dest="resume_path",
        default=None,
        help="Path to a saved checkpoint JSON to resume an interactive session.",
    )
    p_play.add_argument(
        "--out-trace",
        dest="out_trace",
        default=None,
        help="Write the final MatchTranscript JSON to this file path (replayable via 'srd-arena replay').",
    )
    p_play.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite an existing --save checkpoint or --out-trace file.",
    )


def run_arena_cli_command(
    args: argparse.Namespace,
    rules_override: str | None,
) -> tuple[str, dict[str, Any], int]:
    """Execute a parsed arena CLI subcommand and return (operation, payload, exit_code)."""
    subcmd = getattr(args, "arena_command", None)
    if subcmd == "scenarios":
        return "arena_scenarios", execute_arena_scenarios(), 0

    rules = load_rules_store(rules_override)

    if subcmd == "run":
        scenario_input = _parse_scenario_arg(args.scenario)
        transcript = execute_arena_run(
            rules,
            scenario=scenario_input,
            party_agent=args.party_agent,
            enemy_agent=args.enemy_agent,
            seed=args.seed,
            max_rounds=args.max_rounds,
            max_actions_per_turn=args.max_actions_per_turn,
            max_retries_per_action=args.max_retries,
            api_base=args.api_base,
            api_key_env=args.api_key_env,
            enemy_api_base=args.enemy_api_base,
            enemy_api_key_env=args.enemy_api_key_env,
            temperature=args.temperature,
        )
        if args.out_trace:
            out_path = Path(args.out_trace).expanduser()
            if out_path.exists() and not getattr(args, "force", False):
                raise FileExistsError(
                    f"Refusing to overwrite '{out_path}'. Pass --force to replace it."
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(transcript, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return "arena_run", transcript, 0

    if subcmd == "bench":
        scenarios = _parse_csv_list(args.scenarios)
        agents = _parse_csv_list(args.agents)
        seeds = _parse_seeds_arg(args.seeds)
        report = execute_arena_bench(
            rules,
            scenarios=scenarios,
            agents=agents,
            enemy_agent=args.enemy_agent,
            seeds=seeds,
            max_rounds=args.max_rounds,
            api_base=args.api_base,
            api_key_env=args.api_key_env,
        )
        if args.out_json:
            out_path = Path(args.out_json).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        return "arena_bench", report, 0

    if subcmd == "replay":
        transcript = _load_json_file_or_stdin(args.trace_file, label="trace")
        if not isinstance(transcript, dict):
            raise ValueError("Trace JSON must be an object.")
        result = execute_arena_replay(rules, transcript)
        exit_code = 0 if bool(result.get("deterministic_match")) else 1
        return "arena_replay", result, exit_code

    if subcmd == "play":
        if args.scenario is None and args.resume_path is None:
            raise ValueError("Provide a scenario ID/path or --resume <checkpoint.json>.")
        scenario_input = _parse_scenario_arg(args.scenario) if args.scenario is not None else None
        fmt = getattr(args, "format", "markdown")
        if fmt == "json":
            # Keep stdout strictly pure JSON; stream interactive HUD/prompts to stderr.
            def _stderr_out(msg: str) -> None:
                print(msg, file=sys.stderr, flush=True)

            def _stderr_in(prompt: str) -> str:
                if prompt:
                    sys.stderr.write(prompt)
                    sys.stderr.flush()
                line = sys.stdin.readline()
                if line == "":
                    raise EOFError
                return line.rstrip("\r\n")

            play_in = _stderr_in
            play_out = _stderr_out
        else:
            play_in = input
            play_out = print

        result = execute_arena_play(
            rules,
            scenario=scenario_input,
            play_as=args.play_as,
            seat_overrides=args.seats,
            party_agent=args.party_agent,
            enemy_agent=args.enemy_agent,
            nl_driver_spec=args.nl_driver,
            api_base=args.api_base,
            api_key_env=args.api_key_env,
            temperature=args.temperature,
            seed=args.seed,
            max_rounds=args.max_rounds,
            max_actions_per_turn=args.max_actions_per_turn,
            max_retries_per_action=args.max_retries,
            save_path=args.save_path,
            resume_path=args.resume_path,
            out_trace=args.out_trace,
            force=bool(getattr(args, "force", False)),
            input_fn=play_in,
            output_fn=play_out,
        )
        if result.get("status") == "paused":
            return "arena_play_paused", result, 0
        return "arena_run", result, 0

    raise ValueError(f"Unknown arena subcommand: {subcmd}")


def build_arena_parser() -> argparse.ArgumentParser:
    """Build the standalone `srd-arena` CLI argument parser."""
    sub_common = argparse.ArgumentParser(add_help=False)
    sub_common.add_argument(
        "--rules",
        dest="rules_sub",
        default=None,
        help="Path to rules.json (overrides SRD2024_RULES and default dist/rules.json).",
    )
    sub_common.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )

    parser = argparse.ArgumentParser(
        prog="srd-arena",
        description="Zero-server CLI for the 5e 2024 (SRD 5.2) Multi-Agent Tabletop & Model Eval Arena.",
    )
    parser.add_argument(
        "--rules",
        dest="rules",
        default=None,
        help="Path to rules.json (overrides SRD2024_RULES and default dist/rules.json).",
    )
    subparsers = parser.add_subparsers(dest="arena_command", required=True)
    add_arena_subcommands(subparsers, sub_common)
    return parser


def main_arena(argv: Sequence[str] | None = None) -> int:
    """Entry point for `bin/srd-arena`."""
    parser = build_arena_parser()
    args = parser.parse_args(argv)
    rules_override = args.rules_sub if args.rules_sub is not None else args.rules

    try:
        op, payload, exit_code = run_arena_cli_command(args, rules_override)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive top-level CLI guard
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    output = render_operation(op, payload, fmt=args.format)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()

    if payload.get("status") == "error":
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main_arena())
