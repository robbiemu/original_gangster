import re
import sys
from typing import Dict, List, Optional, Tuple


def parse_plan(plan_str: str, verbose: bool = False) -> Tuple[List[Dict], Optional[Dict]]:
    """
    Parse the plan string into recipe steps based on the new prompt format.
    The new prompt expects a multi-line string of commands, potentially separated by '[STEP]' markers.
    Each block of commands separated by [STEP] becomes a single recipe step.
    The fallback action is no longer explicitly defined by the model in this format, so it will be None.
    """
    if verbose:
        print(f"[AGENT/DEBUG] Parsing plan with new format. Raw plan_str:\n---\n{plan_str}\n---", file=sys.stderr)

    recipe_steps: List[Dict] = []
    fallback_action: Optional[Dict] = None # The new format doesn't explicitly define a fallback

    # Normalize newlines and strip leading/trailing whitespace from the whole plan
    # This ensures consistency regardless of original line endings (e.g., \r\n vs \n)
    plan_str = plan_str.replace('\r\n', '\n').strip()

    # Define the exact delimiter pattern for splitting: newline, literal [STEP], newline. 
    delimiter_pattern = r'\n\[STEP\]\n|^\[STEP\]\n|\n\[STEP\]$'

    # Split the plan by the delimiter.
    raw_segments = re.split(delimiter_pattern, plan_str, flags=re.IGNORECASE)

    # Filter out empty strings
    processed_segments = [s.strip() for s in raw_segments if s.strip()]

    if not processed_segments:
        if verbose:
            print("[AGENT/DEBUG] No discernible command segments found after splitting and trimming. Input might be empty or just delimiters.", file=sys.stderr)
        # If the model just outputs "[STEP]" or an empty string, there's no plan.
        return [], None

    for i, segment_content in enumerate(processed_segments):
        # Each non-empty segment is treated as a single action block for a recipe step.
        recipe_steps.append({
            "description": f"Execute command block {i+1}",
            "expected_outcome": f"Command block {i+1} executed successfully", # Generic outcome
            "action": segment_content, # The actual command(s) for the step
            "tool": "shell_tool" # Assuming all are shell commands from the prompt
        })
    
    if verbose:
        print(f"[AGENT/DEBUG] Parsed recipe steps: {recipe_steps}", file=sys.stderr)
        print(f"[AGENT/DEBUG] Parsed fallback action: {fallback_action}", file=sys.stderr)

    return recipe_steps, fallback_action
