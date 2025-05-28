#!/usr/bin/env python3
"""
OG Agent – multi‑agent orchestration with request‑ and action‑level audits
plus **HDF5‑backed session snapshots**.
"""
import argparse
import json
import sys

from agent.orchestrator.agent_orchestrator import AgentOrchestrator
from .emitter import emit
from .session import check_session_exists_in_h5 


def run_orchestration(query: str, model_id: str, model_params: dict, auditor_model_id: str,
                     auditor_model_params: dict, verbose: bool, 
                     session_hash: str, workdir: str) -> None:
    """Main orchestration function."""
    orchestrator = AgentOrchestrator(
        model_id, model_params, auditor_model_id, auditor_model_params,
        session_hash, workdir, verbose
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
    parser.add_argument("--query", required=False, help="Initial query (required for new sessions, ignored for resumed)")
    parser.add_argument("--model", default="ollama/llama3:latest", help="Main model ID")
    parser.add_argument("--model-params", default="{}", help="JSON for main model parameters")
    parser.add_argument("--auditor-model", default="ollama/gemma3:27b-it", help="Auditor model ID")
    parser.add_argument("--auditor-params", default="{}", help="JSON for auditor model parameters")
    parser.add_argument("--workdir", required=True, help="Current working directory")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging from agent")
    parser.add_argument("--summary-mode", action="store_true", help="Enable summary mode for final output")
    parser.add_argument("--session-hash", required=True, help="Unique hash for the current session")

    args = parser.parse_args()

    # Validate session requirements
    is_new_session = not check_session_exists_in_h5(args.session_hash)
    if is_new_session and not args.query:
        emit("error", {"message": "Error: A new session (based on session hash) requires an initial query."})
        sys.exit(1)

    if args.verbose:
        print("[agent/verbose] Launch args:", sys.argv, file=sys.stderr)
        print("[agent/verbose] Parsed args:", vars(args), file=sys.stderr)

    # Parse model parameters
    main_model_params = parse_model_params(args.model_params, "model-params")
    auditor_model_params = parse_model_params(args.auditor_params, "auditor-params")

    try:
        run_orchestration(
            query=args.query,
            model_id=args.model,
            model_params=main_model_params,
            auditor_model_id=args.auditor_model,
            auditor_model_params=auditor_model_params,
            verbose=args.verbose,
            session_hash=args.session_hash,
            workdir=args.workdir,
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        emit("error", {"message": f"Agent execution failed: {e}"})
        if args.verbose:
            print("[agent/verbose] Full stack trace:", file=sys.stderr)
            print(tb, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
