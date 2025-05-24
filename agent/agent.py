#!/usr/bin/env python3
"""
OG Agent (v5) – Full multi-agent orchestration with request- and action-level audits.
Features:
- Single instantiation of auditor model
- Pre-plan generation and storage of recipe
- Request-level and per-action safety audits
- Recipe capture for audit explanations
- Session caching, interactive ProxyTool approvals, NDJSON IPC
"""
import sys
import json
import asyncio
import argparse
from pathlib import Path
from typing import List, Dict, Optional
from smolagents import CodeAgent, LiteLLMModel, ManagedAgent
from smolagents.tools import ShellTool, FileReadTool, PythonInterpreterTool, Tool


def emit(msg_type: str, data: dict):
    # --- NDJSON emitter for IPC ---
    payload = {"type": msg_type, **data}
    print(json.dumps(payload), flush=True)


# --- Session management and recipe storage ---
class AgentSession:
    """Manages session memory and stores current recipe."""
    def __init__(self, session_hash: str):
        self.session_hash = session_hash
        self.storage_dir = Path.home() / ".local" / "share" / "og_sessions"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.storage_path = self.storage_dir / f"{session_hash}.json"
        self.conversation_history: List[Dict[str,str]] = []
        self.current_recipe: Optional[str] = None
        self.executed_actions: List[Dict[str,str]] = []
        self._load_session()

    def _load_session(self):
        if not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text())
            self.conversation_history = data.get('conversation_history', [])
            self.current_recipe = data.get('current_recipe')
            self.executed_actions = data.get('executed_actions', [])
            emit('log', {'message': f"Loaded session {self.session_hash}."})
        except Exception as e:
            emit('error', {'message': f"Failed to load session: {e}"})

    def _save_session(self):
        try:
            payload = {
                'conversation_history': self.conversation_history,
                'current_recipe': self.current_recipe,
                'executed_actions': self.executed_actions
            }
            self.storage_path.write_text(json.dumps(payload, indent=2))
        except Exception as e:
            emit('error', {'message': f"Failed to save session: {e}"})

    def add_to_history(self, role: str, content: str):
        self.conversation_history.append({'role': role, 'content': content})
        self._save_session()

    def add_executed_action(self, tool_name: str, action: str, result: str):
        self.executed_actions.append({
            'tool': tool_name,
            'action': action,
            'result': result,
            'timestamp': str(asyncio.get_event_loop().time())
        })
        self._save_session()

    def set_recipe(self, recipe: str):
        self.current_recipe = recipe
        self._save_session()

    def get_execution_context(self) -> str:
        """Generate a context string showing what's been done and what's planned."""
        context_parts = []
        
        if self.executed_actions:
            context_parts.append("Actions taken so far:")
            for i, action in enumerate(self.executed_actions, 1):
                context_parts.append(f"  {i}. {action['tool']}: {action['action']}")
                if action.get('result'):
                    context_parts.append(f"     Result: {action['result'][:100]}...")
        
        if self.current_recipe:
            context_parts.append("\nPlanned recipe:")
            context_parts.append(self.current_recipe)
        
        return "\n".join(context_parts) if context_parts else "No prior actions or recipe available"


# --- Auditor tools and query builder ---
@Tool
async def count_files(path: str) -> int:
    """Returns count of the path itself and all nested files/directories."""
    p = Path(path)
    if not p.exists():
        return -1
    if p.is_file():
        return 1
    try:
        # Include the path node itself and all its descendants
        return 1 + sum(1 for _ in p.rglob("*"))
    except Exception:
        return -1


def build_audit_query(request: str, context: str = "") -> str:
    base_query = f"""
Determine if executing the following would be unsafe.
Unsafe behaviors include:
- Working outside current working directory
- Unauthorized file access/modification
- Privilege escalation or sudo usage
- Killing/altering other users' processes
- Deleting/modifying logs, Git metadata, shell history
- Removing critical directories (e.g. /usr, /etc, .git)
- Executing arbitrary code via REPL without clear purpose
- Obfuscating actions or hiding behavior
- Affecting more than 1000 files/directories
- Network operations without clear justification
- Installing or modifying system packages

Respond ONLY with JSON in this exact format: {{"safe": true}} or {{"safe": false, "reason": "<brief reason>"}}

Request to evaluate:
{request}"""

    if context.strip():
        base_query += f"""

Context (previous actions and planned recipe):
{context}"""

    return base_query.strip()


async def audit(agent: CodeAgent, request: str, context: str = "") -> dict:
    prompt = build_audit_query(request, context)
    result = await agent.run(prompt)
    try:
        # Extract JSON from the last line that looks like JSON
        lines = result.strip().splitlines()
        for line in reversed(lines):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                return json.loads(line)
        # Fallback: try to parse the entire result
        return json.loads(result.strip())
    except Exception as e:
        emit('log', {'message': f"Audit parse failure: {e}, result was: {result}"})
        return {"safe": False, "reason": "Audit parse failure"}


async def generate_recipe_explanation(agent: CodeAgent, query: str) -> str:
    """Generate a recipe/plan that would be followed for the query, for audit purposes."""
    prompt = f"""
Generate a step-by-step plan to accomplish the following request. 
Be specific about what tools would be used and what actions would be taken.
Format as a clear, numbered list of steps.

Request: {query}
"""
    try:
        result = await agent.run(prompt)
        return result.strip()
    except Exception as e:
        return f"Could not generate plan: {e}"


class ProxyTool(Tool):
    # --- ProxyTool with per-action auditing and user approval ---
    def __init__(self, name: str, underlying: Tool, session: AgentSession, auditor: CodeAgent):
        super().__init__(name=name, description=f"Proxy for {name} requiring audit & approval")
        self.underlying = underlying
        self.session = session
        self.auditor = auditor

    async def run(self, *args, **kwargs):
        # Extract the action string from various possible parameter names
        action_str = (kwargs.get('command') or 
                     kwargs.get('path') or 
                     kwargs.get('code') or 
                     str(args[0]) if args else '')
        
        # Get execution context for audit
        context = self.session.get_execution_context()
        
        # Action-level audit
        audit_res = await audit(self.auditor, action_str, context)
        if not audit_res.get('safe', False):
            explanation = context if context.strip() else action_str
            emit('unsafe', {
                'safe': False,
                'reason': audit_res.get('reason', 'Action deemed unsafe'),
                'explanation': explanation
            })
            return None
        
        # Record action attempt and request approval
        desc = f"{self.name} -> {action_str}"
        self.session.add_to_history('assistant', desc)
        emit('request_approval', {
            'description': desc, 
            'action_str': action_str, 
            'tool': self.name
        })
        
        try:
            resp = json.loads(sys.stdin.readline())
        except Exception:
            emit('error', {'message': 'Failed to read approval response'})
            return None
        
        if not resp.get('approved', False):
            emit('result', {
                'status': 'cancelled', 
                'interpret_message': 'User denied execution'
            })
            return None
        
        # Execute underlying tool
        try:
            if asyncio.iscoroutinefunction(self.underlying.run):
                res = await self.underlying.run(*args, **kwargs)
            else:
                res = self.underlying.run(*args, **kwargs)
            
            # Record successful execution
            result_str = str(res) if res is not None else "completed"
            self.session.add_executed_action(self.name, action_str, result_str)
            self.session.add_to_history('assistant', f"executed {self.name}: {result_str}")
            return res
        except Exception as e:
            error_msg = f"Tool execution failed: {e}"
            emit('error', {'message': error_msg})
            self.session.add_executed_action(self.name, action_str, f"ERROR: {error_msg}")
            return None


async def run_agent(
    query: str,
    model_id: str,
    model_params: dict,
    verbose: bool,
    summary: bool,
    session_hash: str
):
    # --- Core orchestration ---
    session = AgentSession(session_hash)

    # Single auditor model & agent
    auditor_model = LiteLLMModel(model_id=model_id, **model_params)
    auditor_agent = CodeAgent(model=auditor_model, tools=[count_files])
    managed_request_auditor = ManagedAgent(
        agent=auditor_agent,
        name="auditor",
        description="Evaluates safety of user requests"
    )

    # Main model
    main_model = LiteLLMModel(model_id=model_id, **model_params)
    tools = [
        ProxyTool('shell_tool', ShellTool(), session, auditor_agent),
        ProxyTool('file_tool', FileReadTool(), session, auditor_agent),
        ProxyTool('python_tool', PythonInterpreterTool(), session, auditor_agent)
    ]
    agent = CodeAgent(
        model=main_model,
        tools=tools,
        managed_agents=[managed_request_auditor]
    )

    # 1️⃣ Generate recipe explanation for audit purposes
    try:
        recipe_explanation = await generate_recipe_explanation(auditor_agent, query)
        session.set_recipe(recipe_explanation)
    except Exception as e:
        emit('log', {'message': f"Could not generate recipe explanation: {e}"})
        recipe_explanation = f"Plan for: {query}"
        session.set_recipe(recipe_explanation)

    # 2️⃣ Request-level audit of the query with recipe context
    audit_res = await audit(auditor_agent, query, recipe_explanation)
    if not audit_res.get('safe', False):
        emit('unsafe', {
            'safe': False,
            'reason': audit_res.get('reason', 'Request deemed unsafe'),
            'explanation': recipe_explanation
        })
        return

    # 3️⃣ Generate and store actual execution plan
    try:
        plan = await agent.plan(query)
        session.set_recipe(plan)
    except Exception as e:
        emit('log', {'message': f"Could not generate execution plan: {e}"})
        # Continue with the explanation we already have

    # 4️⃣ Execute plan (agent.run handles multi-step recipes)
    try:
        result = await agent.run(query)
    except Exception as e:
        emit('error', {'message': str(e)})
        return

    finale = str(result)
    emit('final_summary', {
        'summary': finale, 
        'nutshell': finale.splitlines()[0] if finale else ''
    })


def main():
    # --- CLI entrypoint ---
    parser = argparse.ArgumentParser(description="Original Gangster CLI – multi-agent v5")
    parser.add_argument("--query", required=True)
    parser.add_argument("--model", default="llama3")
    parser.add_argument("--model-params", default="{}", help="JSON for model parameters")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--session-hash", required=True)
    args = parser.parse_args()

    try:
        params = json.loads(args.model_params)
        if not isinstance(params, dict): 
            raise ValueError("model-params must be a JSON object")
    except Exception as e:
        emit('error', {'message': f"Invalid model-params: {e}"})
        sys.exit(1)

    try:
        asyncio.run(run_agent(
            query=args.query,
            model_id=args.model,
            model_params=params,
            verbose=args.verbose,
            summary=args.summary,
            session_hash=args.session_hash
        ))
    except Exception as e:
        emit('error', {'message': f"Agent execution failed: {e}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
