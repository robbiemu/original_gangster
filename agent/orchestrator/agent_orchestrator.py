import json
# import litellm
import os
import sys
from typing import Optional

from agent.agents.auditor.agent import factory_auditor_agent
from agent.agents.executor.agent import factory_executor_agent
from agent.agents.planner.agent import factory_planner_agent
from agent.emitter import emit
from agent.orchestrator.command_handler import CommandHandler
from agent.orchestrator.initial_plan_handler import InitialPlanHandler
from agent.session import AgentSession, check_session_exists_in_h5


class AgentOrchestrator:
    """Main orchestrator for the agent system."""
    
    def __init__(self, model_id: str, model_params: dict, auditor_model_id: str, 
                 auditor_model_params: dict, session_hash: str, workdir: str, verbose: bool):
        self.verbose = verbose
        self.workdir = workdir
        
        os.chdir(workdir)

        # if verbose:
        #    litellm.set_verbose(True)
        
        # Initialize session and agents
        self.session = AgentSession(session_hash, emit)
        self.auditor_agent = factory_auditor_agent(auditor_model_id, auditor_model_params)
        self.executor_agent = factory_executor_agent(model_id, model_params, self.session, self.auditor_agent)
        self.planner_agent = factory_planner_agent(model_id, model_params)
        
        # Initialize handlers
        self.plan_handler = InitialPlanHandler(self.planner_agent, self.auditor_agent, self.session, verbose)
        self.command_handler = CommandHandler(self.executor_agent, self.session, verbose)
    
    def run(self, query: Optional[str]) -> None:
        """Main orchestration entry point."""
        if self._is_initial_plan_request():
            self._handle_initial_planning(query)
        else:
            emit("log", {"message": f"Resuming existing session '{self.session.session_hash}'. Waiting for command from Go."})
        
        self._process_commands()
    
    def _is_initial_plan_request(self) -> bool:
        """Check if this is an initial plan request."""
        return not check_session_exists_in_h5(self.session.session_hash)
    
    def _handle_initial_planning(self, query: Optional[str]) -> None:
        """Handle initial planning phase."""
        if not query:
            emit("error", {"message": "Error: Initial plan request requires a query."})
            sys.exit(1)
        
        self.plan_handler.create_and_audit_plan(query)
    
    def _process_commands(self) -> None:
        """Process incoming commands from Go client."""
        while True:
            line = sys.stdin.readline()
            if not line:
                emit("log", {"message": "Go client closed stdin, exiting Python agent."})
                break
            
            try:
                command = json.loads(line.strip())
                should_continue = self.command_handler.handle_command(command)
                if not should_continue:
                    break
            except json.JSONDecodeError:
                emit("error", {"message": f"Failed to parse JSON command from Go: '{line.strip()}'"})
                break
