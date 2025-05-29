
import ast
import json
import re
import sys
from typing import Any, Dict, Optional
from smolagents import ToolCallingAgent, LiteLLMModel
#from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log
#import logging # For tenacity logs
#import litellm # Import litellm to catch its specific exceptions

from agent.agents.auditor.run_context_script import run_show_context_script
from agent.common_tools.tools import get_common_tools
from agent.prompts import _prompts_config
from .tools import get_auditor_tools


#logging.basicConfig(stream=sys.stderr, level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
## Optional: Get a logger for this module to show where retry logs come from
#auditor_logger = logging.getLogger(__name__)

from .tools import get_auditor_tools


def factory_auditor_agent(model_id: str, model_params: Dict) -> ToolCallingAgent:
    auditor_model = LiteLLMModel(model_id=model_id, **model_params)

    tools = get_auditor_tools() + get_common_tools()  

    auditor_agent = ToolCallingAgent(model=auditor_model, tools=tools)
    return auditor_agent


def build_audit_query(request: str, context: str = "") -> str:
    """ Enhanced audit system with directory exploration capabilities """
    
    template = _prompts_config["auditor_query_template"]
    
    terminal_session_context = run_show_context_script()
    full_context_for_template = run_show_context_script()
    if context.strip():
        full_context_for_template += f"\n\nAdditional User Context:\n{context.strip()}"
    return template.format(request=request, context=context, terminal_session_context=terminal_session_context).strip()


def _find_audit_verdict_in_json(data: Any) -> Optional[Dict[str, Any]]:
    """
    Recursively searches for 'SAFE', 'REASON', 'EXPLANATION' keys
    within a dictionary or list, returning the first valid verdict found.
    Keys are case-insensitive.
    """
    if isinstance(data, dict):
        # Convert keys to uppercase for case-insensitive matching
        upper_data = {k.upper(): v for k, v in data.items()}

        # Check if the current dictionary itself contains the verdict keys
        if "SAFE" in upper_data and "REASON" in upper_data and "EXPLANATION" in upper_data:
            return {
                "safe": str(upper_data.get("SAFE", False)).lower() == "true", # Ensure boolean from various inputs
                "reason": str(upper_data.get("REASON", "N/A")),
                "explanation": str(upper_data.get("EXPLANATION", "N/A"))
            }
        
        # Recursively search in nested dictionaries
        for key, value in data.items():
            found = _find_audit_verdict_in_json(value)
            if found:
                return found
    
    # Recursively search in list items
    elif isinstance(data, list):
        for item in data:
            found = _find_audit_verdict_in_json(item)
            if found:
                return found
    
    return None

def _parse_json_verdict(auditor_output: Any) -> Optional[Dict[str, Any]]:
    """
    Attempts to parse a JSON audit verdict from various forms of auditor output.
    This includes direct dictionary output, or JSON embedded in strings.
    """
    # 1. Handle direct dictionary output (from smolagents tool call)
    verdict = _find_audit_verdict_in_json(auditor_output)
    if verdict:
        print("[AGENT/DEBUG] Parsed as direct dictionary output.", file=sys.stderr)
        return verdict

    # Ensure we're working with a string for further parsing attempts
    text = str(auditor_output)

    # 2. Try to extract and parse a JSON object from within the string
    # This regex is more specific, looking for a JSON object structure.
    # It attempts to handle cases like "Final answer: { ... }" or "```json\n{ ... }\n```"
    json_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```|(\{.*\})", text, re.DOTALL)
    if json_match:
        # Prioritize the content within ```json``` block if it exists, otherwise use the general {} match
        json_candidate = json_match.group(1) or json_match.group(2) 
        if json_candidate:
            try:
                # First try json.loads as is
                json_data = json.loads(json_candidate)
                verdict = _find_audit_verdict_in_json(json_data)
                if verdict:
                    print("[AGENT/DEBUG] Parsed as JSON from string (extracted).", file=sys.stderr)
                    return verdict
            except json.JSONDecodeError:
                try:
                    # Fallback: Try using ast.literal_eval for Python-style dicts
                    json_data = ast.literal_eval(json_candidate)
                    if isinstance(json_data, dict):
                        verdict = _find_audit_verdict_in_json(json_data)
                        if verdict:
                            print("[AGENT/DEBUG] Parsed as Python-style dict using ast.literal_eval.", file=sys.stderr)
                            return verdict
                except Exception:
                    print(f"[AGENT/DEBUG] Extracted JSON candidate was not valid JSON or Python dict: '{json_candidate}'", file=sys.stderr)
    
    # 3. Fallback: Try to parse the entire string as JSON directly (e.g., if no prefix/suffix)
    try:
        json_data = json.loads(text.replace("'", '"'))
        verdict = _find_audit_verdict_in_json(json_data)
        if verdict:
            print("[AGENT/DEBUG] Parsed as JSON from full string.", file=sys.stderr)
            return verdict
    except json.JSONDecodeError:
        print("[AGENT/DEBUG] Full text not valid JSON, proceeding to markdown parsing.", file=sys.stderr)
        pass # Not valid JSON, continue to markdown parsing

    return None


def parse_audit_markdown_response(auditor_output: Any) -> Dict[str, Any]:
    """
    Parses the auditor agent's output, prioritizing JSON parsing and
    falling back to markdown regex parsing if JSON is not found.
    """
    print("[AGENT/DEBUG] trying to parse audit response (raw):", auditor_output, file=sys.stderr)

    # First, try to parse as JSON
    json_verdict = _parse_json_verdict(auditor_output)
    if json_verdict:
        return json_verdict

    # If no JSON verdict found, fall back to markdown parsing
    text = str(auditor_output) # Ensure text for markdown parsing

    safe = False
    reason = "N/A"
    explanation = "N/A"

    safe_match = re.search(r"^#+\s*SAFE:\s*(true|false)", text, re.MULTILINE | re.IGNORECASE)
    if safe_match:
        safe = safe_match.group(1).lower() == "true"

    reason_match = re.search(r"^#+\s*REASON:\s*(.*)", text, re.MULTILINE | re.IGNORECASE)
    if reason_match:
        reason = reason_match.group(1).strip()

    explanation_match = re.search(r"^#+\s*EXPLANATION:\s*(.*)", text, re.MULTILINE | re.IGNORECASE | re.DOTALL)
    if explanation_match:
        explanation = explanation_match.group(1).strip()
    
    print(f"[AGENT/DEBUG] Markdown parsing result: safe={safe}, reason='{reason}', explanation='{explanation}'", file=sys.stderr)

    # Ensure default reason/explanation if safe is false but nothing was parsed
    if not safe and reason == "N/A":
        reason = "Unable to determine safety from auditor response."
    if not safe and explanation == "N/A":
        explanation = f"Auditor response format was unexpected: '{text}'."

    return {"safe": safe, "reason": reason, "explanation": explanation}

# @retry(
#     stop=stop_after_attempt(3), # Try up to 3 times (1 original attempt + 2 retries)
#     wait=wait_fixed(2),         # Wait 2 seconds between retries
#     retry=retry_if_exception_type((litellm.APIConnectionError, json.decoder.JSONDecodeError)), # Retry only on these specific errors
#     before_sleep=before_sleep_log(auditor_logger, logging.INFO, exc_info=True) # Log before sleeping, show exception info
# )
def audit_request(auditor: ToolCallingAgent, request: str, context: str) -> Dict[str, Any]:
    """
    Audit a user request or action using the auditor agent.
    Returns a dictionary with 'safe', 'reason', 'explanation', and optionally 'log_message' on error.
    """
    prompt = build_audit_query(request, context)
    result = None
    try:
        result = auditor.run(prompt)
        # Since the model is instructed to output markdown,
        # auditor.run(prompt) will return the raw text response.
        audit_verdict = parse_audit_markdown_response(str(result))
        return audit_verdict
    except Exception as e:
        # Instead of emitting directly, return error info for the caller to emit.
        result_str = str(result) if 'result' in locals() else "N/A" # Capture result if it exists
        return {
            "safe": False,
            "reason": "Audit evaluation failed",
            "explanation": f"Internal audit error: {e}",
            "log_message": f"Audit evaluation failed: {e}, result was: {result_str}"
        }
