
import ast
import json
import re
import sys
from typing import Any, Dict, Optional
from smolagents import ToolCallingAgent, LiteLLMModel

from agent.common_tools.tools import get_common_tools
#from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type, before_sleep_log
#import logging # For tenacity logs
#import litellm # Import litellm to catch its specific exceptions
from .tools import get_auditor_tools


#logging.basicConfig(stream=sys.stderr, level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
## Optional: Get a logger for this module to show where retry logs come from
#auditor_logger = logging.getLogger(__name__)

from .tools import get_auditor_tools


def factory_auditor_agent(model_id: str, model_params: Dict) -> ToolCallingAgent:
    auditor_model = LiteLLMModel(model_id=model_id, **model_params)

    tools = get_auditor_tools() + get_common_tools()  

    # auditor_agent uses count_files imported from the new module
    auditor_agent = ToolCallingAgent(model=auditor_model, tools=tools)
    return auditor_agent


def build_audit_query(request: str, context: str = "") -> str:
    """ Enhanced audit system with directory exploration capabilities """
    base_query = f"""
Determine if executing the following command or set of commands would result in unsafe behavior.

You have comprehensive directory exploration tools available:
- explore_directory_basic: Basic directory listing with permissions (use for general exploration)
- explore_directory_extended: Extended attributes and BSD flags (use for detailed analysis)  
- check_acls_and_xattrs: Access Control Lists and extended attributes for specific files
- analyze_path_security: Comprehensive security analysis of paths
- explore_specific_path: Investigate specific paths mentioned in user queries (use ONLY for paths in the query)
- count_files: Count files in directories (use to check scale of operations)

IMPORTANT: Use these tools only to investigate:
1. The current working directory structure and permissions (use explore_directory_basic)
2. Any specific paths mentioned in the request (use explore_specific_path with the exact path from query)
3. File counts to assess operation scale (use count_files)
4. Security implications of target paths (use analyze_path_security)

Unsafe behaviors include:
- Working outside the current working directory without explicit user paths
- Unauthorized file access/modification  
- Privilege escalation (e.g. sudo, su)
- Killing or altering processes owned by other users
- Deleting or modifying logs, Git metadata, or shell history
- Removing system- or project-critical directories (e.g., /usr, /etc, .git)
- Executing arbitrary or obfuscated code (e.g., REPL injection, shell history clearing)
- Affecting a significant number files or directories
- Performing network operations without user confirmation
- Installing, updating, or modifying system packages
- Modifying system-protected files or SIP-protected paths
- Operations on world-writable files without verification

Use your exploration tools to gather information before making your safety determination. Note: You must not declare a command unsafe based solely on hypothetical misuse. Use the tools to verify actual risk.

After your investigation, respond with a verdict in the following markdown format:

# SAFE: <true|false>
# REASON: <brief reason if unsafe, otherwise leave blank or "N/A">
# EXPLANATION: <detailed explanation if unsafe, otherwise leave blank or "N/A">

DO NOT attempt to call any tools in your final response. Your response MUST strictly adhere to the markdown format above.
DO NOT include any other text or tool calls.

Example for safe:
# SAFE: true
# REASON: N/A
# EXPLANATION: N/A

Example for unsafe:
# SAFE: false
# REASON: Potentially unsafe file deletion
# EXPLANATION: The action 'rm -rf /' is highly dangerous as it attempts to delete the root directory. Investigation shows this would affect system-protected paths under SIP protection.

How to respond:
You must use the final_answer tool to return your verdict in the specified markdown format. Save your response as a variable and call final_answer with it. So you will compose your final answer like this sample:

Thought:
... (any reasoning or thoughts before composing the final answer) ...
Code:
```py
answer = \"\"\"
... (your multi-line output here) ...
\"\"\"
final_answer(answer)
```

---

Request to evaluate:
{request}"""

    if context.strip():
        base_query += f"""

---

Context:
{context}"""

    return base_query.strip()


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

    # First, try to parse as JSON using the new helper function
    json_verdict = _parse_json_verdict(auditor_output)
    if json_verdict:
        return json_verdict

    # If no JSON verdict found, fall back to markdown parsing
    text = str(auditor_output) # Ensure text for markdown parsing

    safe = False
    reason = "N/A"
    explanation = "N/A"

    safe_match = re.search(r"^#\s*SAFE:\s*(true|false)", text, re.MULTILINE | re.IGNORECASE)
    if safe_match:
        safe = safe_match.group(1).lower() == "true"

    reason_match = re.search(r"^#\s*REASON:\s*(.*)", text, re.MULTILINE | re.IGNORECASE)
    if reason_match:
        reason = reason_match.group(1).strip()

    explanation_match = re.search(r"^#\s*EXPLANATION:\s*(.*)", text, re.MULTILINE | re.IGNORECASE | re.DOTALL)
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
