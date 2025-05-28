from pathlib import Path
import toml 
from typing import Dict

from agent.common_tools.tools import check_planner_tool_availability
from agent.session import AgentSession


# Global variable to store loaded prompts
_prompts_config: Dict[str, str] = {}

def _get_common_tools() -> str:
    # Get the availability of conditional common tools
    tool_availability = check_planner_tool_availability()

    # Define the hardcoded lines for each common tool
    # These are now listed here and will be conditionally included
    common_tool_lines = []
    
    # Always available tools
    common_tool_lines.append("- man_page: Searches man pages.")
    common_tool_lines.append("- help_flag: Output from the `<command> --help` if available.")
    common_tool_lines.append("- probe: Show the location and file type of a command.")

    # Conditionally available tools (lines only added if available)
    if tool_availability["info_page"]:
        common_tool_lines.append("- info_page: Searches info pages.")
    if tool_availability["tldr_page"]:
        common_tool_lines.append("- tldr_page: Searches tldr pages.")
    if tool_availability["brew_info"]:
        common_tool_lines.append("- brew_info: Full `brew info` output for a Homebrew package.")
    
    return "\n".join(common_tool_lines)

def _get_prompts_config_path() -> Path:
    """Determine the path to the prompts.toml file in the user's data directory."""
    home_dir = Path.home()
    return home_dir / ".local" / "share" / "og" / "prompts" / "prompts.toml"

def load_prompts():
    """Load prompts from the TOML file."""
    global _prompts_config
    prompts_path = _get_prompts_config_path()
    
    if not prompts_path.exists():
        raise FileNotFoundError(f"Prompts configuration file not found at {prompts_path}. Please run 'og init'.")
    
    try:
        # Assuming the prompts are under a [prompts] table in the TOML
        _prompts_config = toml.loads(prompts_path.read_text())["prompts"]
    except Exception as e:
        raise RuntimeError(f"Failed to load or parse prompts from {prompts_path}: {e}")

# Load prompts when the module is imported
load_prompts()

def prepare_planning_prompt(query: str) -> str:
    """
    Prepares the prompt for the PlannerAgent to generate the initial recipe.
    """
    planning_tools_section_str = _get_common_tools()
    
    template = _prompts_config["planning_prompt_template"]
    
    return template.format(
        planning_tools_section_str=planning_tools_section_str,
        query=query
    )

def prepare_recipe_continuation_query(session: AgentSession) -> str:
    """
    Prepares the continuation query for the ExecutorAgent when executing the recipe.
    """
    template = _prompts_config["recipe_continuation_query_template"]
    
    tools_section_str = _get_common_tools()
    # Use the directly stored original_query from the session.
    # This is the robust fix to avoid brittle string parsing.
    original_request_line = session.original_query.strip() if session.original_query else "N/A"

    return template.format(
        original_request_line=original_request_line,
        execution_context=session.get_execution_context(),
        tools_section_str=tools_section_str
    )

def prepare_fallback_continuation_query(session: AgentSession) -> str:
    """
    Prepares the continuation query for the ExecutorAgent when executing the fallback.
    """
    template = _prompts_config["fallback_continuation_query_template"]

    tools_section_str = _get_common_tools()

    return template.format(
        # Use the directly stored original_query from the session
        original_request_line=session.original_query.strip() if session.original_query else "N/A",
        execution_context=session.get_execution_context(),
        tools_section_str=tools_section_str
    )
