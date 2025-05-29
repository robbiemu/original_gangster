#!/usr/bin/env python3
"""
OG Agent – multi‑agent orchestration with request‑ and action‑level audits
plus **HDF5‑backed session snapshots**.
"""

import argparse
import json
import sys
import traceback

from agent.log_levels import LogLevel
from agent.orchestrator.agent_orchestrator import AgentOrchestrator
from .emitter import emit, set_python_log_level
from .session import check_session_exists_in_h5


def run_orchestration(
    query: str,
    executor_model_id: str,
    executor_model_params: dict,
    planner_model_id: str,
    planner_model_params: dict,
    auditor_model_id: str,
    auditor_model_params: dict,
    verbosity: str,
    session_hash: str,
    workdir: str,
    output_threshold_bytes: int,
    json_logs_enabled: bool,
    cache_directory: str,
    summary_mode: bool,
) -> None:
    """Main orchestration function."""
    orchestrator = AgentOrchestrator(
        executor_model_id,
        executor_model_params,
        planner_model_id,
        planner_model_params,
        auditor_model_id,
        auditor_model_params,
        session_hash,
        workdir,
        verbosity,
        json_logs_enabled,
        cache_directory,
        output_threshold_bytes,
        summary_mode,
    )

    orchestrator.run(query)


def parse_model_params(params_str: str, param_name: str) -> dict:
    """Parse and validate model parameters."""
    try:
        params = json.loads(params_str)
        if not isinstance(params, dict):
            raise ValueError(f"{param_name} must be a JSON object")
        return params
    except Exception as e:
        emit("error", {"message": f"Invalid {param_name}: {e}"})
        sys.exit(1)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="OG CLI – multi-agent v6")
    parser.add_argument(
        "--query",
        required=False,
        help="Initial query (required for new sessions, ignored for resumed)",
    )

    # Executor Agent Model Config
    parser.add_argument(
        "--executor-model", default="ollama/llama3:latest", help="Executor model ID"
    )
    parser.add_argument(
        "--executor-params", default="{}", help="JSON for executor model parameters"
    )

    # Planner Agent Model Config
    parser.add_argument(
        "--planner-model", default="ollama/llama3:latest", help="Planner model ID"
    )
    parser.add_argument(
        "--planner-params", default="{}", help="JSON for planner model parameters"
    )

    # Auditor Agent Model Config
    parser.add_argument(
        "--auditor-model", default="ollama/gemma3:27b-it", help="Auditor model ID"
    )
    parser.add_argument(
        "--auditor-params", default="{}", help="JSON for auditor model parameters"
    )

    parser.add_argument("--workdir", required=True, help="Current working directory")
    parser.add_argument(
        "--verbosity",
        default="info",
        help="Set logging verbosity (debug, info, warn, none)",
    )
    parser.add_argument(
        "--summary-mode",
        action="store_true",
        help="Enable summary mode for final output",
    )
    parser.add_argument(
        "--session-hash", required=True, help="Unique hash for the current session"
    )
    parser.add_argument(
        "--output-threshold-bytes",
        type=int,
        default=16768,
        help="Threshold for tool output size before saving to file",
    )

    parser.add_argument(
        "--json-logs-enabled",
        type=str,
        default="True",
        help="Whether to save session state to JSON files (True/False)",
    )
    parser.add_argument(
        "--cache-directory",
        type=str,
        required=True,
        help="Directory for storing JSON session logs",
    )

    args = parser.parse_args()

    # Configure the Python agent's global log level immediately
    set_python_log_level(args.verbosity)

    # Emit startup args at debug level
    emit("debug_log", {"message": f"Launch args: {sys.argv}", "location": "main.main"})
    emit(
        "debug_log", {"message": f"Parsed args: {vars(args)}", "location": "main.main"}
    )

    # Validate session requirements
    is_new_session = not check_session_exists_in_h5(args.session_hash)
    if is_new_session and not args.query:
        emit(
            "error",
            {
                "message": "Error: A new session (based on session hash) requires an initial query."
            },
        )
        sys.exit(1)

    # Parse model parameters for each agent
    executor_model_params = parse_model_params(args.executor_params, "executor-params")
    planner_model_params = parse_model_params(args.planner_params, "planner-params")
    auditor_model_params = parse_model_params(args.auditor_params, "auditor-params")

    try:
        run_orchestration(
            query=args.query,
            executor_model_id=args.executor_model,
            executor_model_params=executor_model_params,
            planner_model_id=args.planner_model,
            planner_model_params=planner_model_params,
            auditor_model_id=args.auditor_model,
            auditor_model_params=auditor_model_params,
            verbosity=args.verbosity,
            session_hash=args.session_hash,
            workdir=args.workdir,
            output_threshold_bytes=args.output_threshold_bytes,
            summary_mode=args.summary_mode,
            json_logs_enabled=args.json_logs_enabled.lower() == "true",
            cache_directory=args.cache_directory,
        )
    except Exception as e:
        tb = traceback.format_exc()
        emit(
            "error",
            {"message": f"Agent execution failed: {e}", "location": "main.main"},
        )
        # Only emit full stack trace if verbosity is debug or warn
        if LogLevel[args.verbosity.upper()] <= LogLevel.WARN:
            emit(
                "warn_log",
                {"message": f"Full stack trace:\n{tb}", "location": "main.main"},
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
