from agent.common_tools.tools import check_planner_tool_availability
from agent.session import AgentSession


def _get_planning_tools() -> str:
        # Get the availability of conditional planning tools
    tool_availability = check_planner_tool_availability()

    # Define the hardcoded lines for each planning tool
    # These are now listed here and will be conditionally included
    planning_tool_lines = []
    
    # Always available tools
    planning_tool_lines.append("- man_page: Searches man pages.")
    planning_tool_lines.append("- help_flag: Output from the `<command> --help` if available.")
    planning_tool_lines.append("- probe: Show the location and file type of a command.")

    # Conditionally available tools (lines only added if available)
    if tool_availability["info_page"]:
        planning_tool_lines.append("- info_page: Searches info pages.")
    if tool_availability["tldr_page"]:
        planning_tool_lines.append("- tldr_page: Searches tldr pages.")
    if tool_availability["brew_info"]:
        planning_tool_lines.append("- brew_info: Full `brew info` output for a Homebrew package.")
    
    return "\n".join(planning_tool_lines)

def prepare_planning_prompt(query: str) -> str:
    """
    Prepares the prompt for the PlannerAgent to generate the initial recipe.
    """
    planning_tools_section_str = _get_planning_tools()
    
    return f"""Your task is to develop an plan of what commandline steps are needed to solve the request below. The overall goal is to eventually fulfill this request for the user using this coding interface. But first we must get permission, and to do that we need to create an plan of what we will do.

Please generate a series of commands, one command per line, to execute on the commandline to fulfill the following request. If the plan must be dynamic, so that you look at output along the way before the request can be completed, use the special command [STEP] on its own line, at all places where this is essential.

This multi-line output will need to be a string that is returned with the final_answer() tool. So you will compose your final answer like this sample:

Thought:
... (any reasoning or thoughts before composing the final answer) ...
Code:
```py
answer = \"\"\"
... (your multi-line output here) ...
\"\"\"
final_answer(answer)
```

Before you write your final answer, you may use the following tools to gather information and context:
Planning tools: (unavailable to the executor in execution phase)
{planning_tools_section_str}

These planning tools are only available to you during planning, and may not be used in an Act: statement. You can use them in a code block and wait to see the output before producing your final answer. Examples:

Request: {query}
"""


def prepare_recipe_continuation_query(session: AgentSession) -> str:
    """
    Prepares the continuation query for the ExecutorAgent when executing the recipe.
    """
    return f"""
The approved recipe is now being executed to complete the original request.
Your directive is to carry out the steps defined in the recipe, using the current execution context to guide your actions.

Original Request: {session.get_execution_context().splitlines()[0].replace("Actions completed so far:", "Original Request:").strip()}

Current execution context:
{session.get_execution_context()}

Tips:
- Adapt as necessary based on prior results and tool outputs as you proceed.
- Be frugal with the size of the outputs you demand, as we have a limited context window in which to work.
- Make use of variables to store outputs from previous steps rather than relying on context to rewrite them. This will ensure the results are preserved from step to step.

When you have gathered all necessary information and fully resolved the original request, provide a comprehensive final answer summarizing your findings and the outcome.
"""

def prepare_fallback_continuation_query(session: AgentSession) -> str:
    """
    Prepares the continuation query for the ExecutorAgent when executing the fallback.
    """
    return f"""
The user has approved the fallback action. Your directive is to execute this fallback action and then proceed to complete the original request.
Use the current execution context to guide your actions and adapt to any unforeseen results as you proceed.

Original Request: {session.get_execution_context().splitlines()[0].replace("Actions completed so far:", "Original Request:").strip()}

Current execution context:
{session.get_execution_context()}

Only emit a final_summary when the task is fully completed.
"""