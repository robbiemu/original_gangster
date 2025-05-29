import re
from typing import Dict, List, Optional, Tuple

from agent.emitter import emit


def parse_plan(plan_str: str) -> Tuple[List[Dict], Optional[Dict]]:
    """
    Parse the plan string into recipe steps based on the prompt format.
    The prompt expects a multi-line string of commands, potentially separated by '[STEP]' markers.
    Each block of commands separated by [STEP] becomes a single recipe step.
    """
    emit(
        "debug_log",
        {
            "message": f"Parsing plan. Raw plan_str:\n---\n{plan_str}\n---",
            "location": "orchestrator/plan_parser.parse_plan",
        },
    )

    recipe_steps: List[Dict] = []
    fallback_action: Optional[Dict] = None

    plan_str = plan_str.replace("\r\n", "\n").strip()

    delimiter_pattern = r"\n\[STEP\]\n|^\[STEP\]\n|\n\[STEP\]$"

    raw_segments = re.split(delimiter_pattern, plan_str, flags=re.IGNORECASE)

    processed_segments = [s.strip() for s in raw_segments if s.strip()]

    if not processed_segments:
        emit(
            "debug_log",
            {
                "message": "No discernible command segments found after splitting and trimming. Input might be empty or just delimiters.",
                "location": "orchestrator/plan_parser.parse_plan",
            },
        )
        return [], None

    for i, segment_content in enumerate(processed_segments):
        recipe_steps.append(
            {
                "description": f"Execute command block {i + 1}",
                "expected_outcome": f"Command block {i + 1} executed successfully",
                "action": segment_content,
                "tool": "shell_tool",
            }
        )

    emit(
        "debug_log",
        {
            "message": f"Parsed recipe steps: {recipe_steps}",
            "location": "orchestrator/plan_parser.parse_plan",
        },
    )
    emit(
        "debug_log",
        {
            "message": f"Parsed fallback action: {fallback_action}",
            "location": "orchestrator/plan_parser.parse_plan",
        },
    )

    return recipe_steps, fallback_action
