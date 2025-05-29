import re
import textwrap
import importlib
from typing import Any
from smolagents.agents import CodeAgent
from smolagents.memory import SystemPromptStep, TaskStep, PlanningStep, FinalAnswerStep, ActionStep, Message, MessageRole
from smolagents.models import Model 
from smolagents.monitoring import LogLevel


class SummarizingCodeAgent(CodeAgent):
    """
    A CodeAgent that attempts to summarize parts of the memory to reduce token usage.
    """
    def __init__(self, summarizer_model: Model, summary_length: int = 1024, **kwargs):
        # Call the parent CodeAgent's constructor with all other arguments
        super().__init__(**kwargs)
        
        self.summarizer_model = summarizer_model
        self.summary_length = summary_length
        if not hasattr(self.summarizer_model, "generate"):
            raise ValueError("Summarizer model must implement a `generate` method for summarization.")
        
        self.logger.log(
            f"Initialized SummarizingCodeAgent with summarizer_model: {summarizer_model.__class__.__name__} (id: {getattr(summarizer_model, 'model_id', 'N/A')}), target summary_length: {summary_length} words.",
            level=LogLevel.INFO
        )

    def summarize_content(self, content: str) -> str:
        """
        Helper method to summarize long content using the summarizer model.
        Only summarizes if content is significantly longer than `summary_length`.
        """
        # Simple heuristic: only summarize if content is much longer than target
        if not content or len(content.split()) < self.summary_length * 1.5:
            return content

        prompt = [
            {
                "role": MessageRole.SYSTEM,
                "content": "You are a helpful assistant. Summarize the following text concisely.",
            },
            {
                "role": MessageRole.USER,
                "content": f"Summarize the following text to approximately {self.summary_length} words, focusing on key actions, observations, and outcomes:\n\n{content}",
            },
        ]
        try:
            summary_message = self.summarizer_model.generate(prompt)
            return summary_message.content
        except Exception as e:
            self.logger.log_error(f"Warning: Failed to summarize content due to: {type(e).__name__}: {e}. Using original content for this turn.")
            return content # Fallback to original content on error


    def write_memory_to_messages(
        self,
        summary_mode: bool | None = False, # This 'summary_mode' is often used by MultiStepAgent itself (e.g., for planning updates)
    ) -> list[Message]:
        """
        Overrides the parent method to apply summarization to older memory steps.
        """
        messages: list[Message] = []

        # 1. Always add system prompt. It's concise and foundational.
        messages.extend(self.memory.system_prompt.to_messages(summary_mode=summary_mode))
        
        # 2. Add the TaskStep. Also crucial and generally short.
        # Find and add the TaskStep if it exists
        found_task_step = False
        for step in self.memory.steps:
            if isinstance(step, TaskStep):
                messages.extend(step.to_messages(summary_mode=summary_mode))
                found_task_step = True
                break # Assuming only one TaskStep at the beginning

        # 3. Determine the index of the last ActionStep to keep it unsynthesized (full detail)
        # This is because the LLM needs the full context of its immediate past action/observation.
        last_action_step_idx = -1
        for i in range(len(self.memory.steps) - 1, -1, -1):
            if isinstance(self.memory.steps[i], ActionStep):
                last_action_step_idx = i
                break
        
        # 4. Iterate through all other steps and apply conditional summarization
        for i, memory_step in enumerate(self.memory.steps):
            if isinstance(memory_step, (SystemPromptStep, TaskStep)):
                continue # Already handled

            # Decide whether to summarize this specific step
            # - Always summarize if the general `summary_mode` is active (e.g., for planning calls)
            # - For ActionSteps, summarize if it's *not* the most recent one.
            should_summarize_this_step = summary_mode or (isinstance(memory_step, ActionStep) and i < last_action_step_idx)
            
            if isinstance(memory_step, PlanningStep):
                if should_summarize_this_step:
                    # Summarize the plan content
                    summarized_plan_content = self.summarize_content(memory_step.plan)
                    messages.append(
                        {
                            "role": MessageRole.ASSISTANT, # Plans are LLM outputs
                            "content": f"Summary of plan from step {memory_step.step_number}:\n```\n{summarized_plan_content}\n```",
                        }
                    )
                else:
                    # If not summarizing, include the original planning message
                    messages.extend(memory_step.to_messages(summary_mode=False)) # ensure full detail for this specific step

            elif isinstance(memory_step, ActionStep):
                if should_summarize_this_step:
                    # Collect all relevant text content for summarization
                    all_text_content_for_summary = []

                    # 1. LLM's output (Thoughts, Code)
                    if memory_step.model_output_message and memory_step.model_output_message.content:
                        model_output_content = memory_step.model_output_message.content
                        thoughts_match = re.search(r"Thoughts:\s*(.*?)(?=\nCode:|\n```py|\n```|\Z)", model_output_content, re.DOTALL)
                        code_match = re.search(r"```(?:py|python)?\s*\n(.*?)\n```", model_output_content, re.DOTALL)
                        
                        thoughts_text = thoughts_match.group(1).strip() if thoughts_match else ""
                        code_text = code_match.group(1).strip() if code_match else ""

                        if thoughts_text:
                            all_text_content_for_summary.append(f"Thoughts: {thoughts_text}")
                        if code_text:
                            all_text_content_for_summary.append(f"Code Executed: {code_text}")
                        elif model_output_content: # Fallback if specific parsing failed
                             all_text_content_for_summary.append(f"Agent's LLM output: {model_output_content}")

                    # 2. Observations
                    if memory_step.observations:
                        all_text_content_for_summary.append(f"Observation: {memory_step.observations}")
                    
                    # 3. Errors (if any)
                    if memory_step.error:
                        all_text_content_for_summary.append(f"Error: {memory_step.error}")

                    # Summarize the combined content for this action step
                    combined_summary = self.summarize_content(textwrap.dedent("\n".join(all_text_content_for_summary)).strip())

                    # Add a single summarized message for this step's action and observation
                    if combined_summary:
                        messages.append(
                            {
                                "role": MessageRole.ASSISTANT, # Represents agent's overall action/result for this turn
                                "content": f"Summary of step {memory_step.step_number} activity:\n{combined_summary}",
                            }
                        )
                else:
                    # For the most recent ActionStep (or if not in summary_mode), include full detail
                    messages.extend(memory_step.to_messages(summary_mode=False)) # Ensure full detail here

            elif isinstance(memory_step, FinalAnswerStep):
                # Final answer steps are typically at the very end, but if they appear in history, summarize them.
                if should_summarize_this_step:
                    summarized_answer = self.summarize_content(str(memory_step.final_answer))
                    messages.append({"role": MessageRole.ASSISTANT, "content": f"Final Answer provided earlier: {summarized_answer}"})
                else:
                    messages.extend(memory_step.to_messages(summary_mode=False)) # Full detail if not summarizing

        return messages

    def to_dict(self) -> dict[str, Any]:
        agent_dict = super().to_dict() # Get the base CodeAgent's dictionary representation
        
        # Add the summarizer_model and summary_length to the dictionary
        agent_dict["summarizer_model"] = {
            "class": self.summarizer_model.__class__.__name__,
            "data": self.summarizer_model.to_dict() # Assuming your Model classes have a `to_dict` method
        }
        agent_dict["summary_length"] = self.summary_length
        return agent_dict

    @classmethod
    def from_dict(cls, agent_dict: dict[str, Any], **kwargs) -> "SummarizingCodeAgent":
        # 1. Reconstruct the summarizer_model
        summarizer_model_info = agent_dict["summarizer_model"]
        # Assuming smolagents.models contains the model classes
        model_module = importlib.import_module("smolagents.models")
        summarizer_model_class = getattr(model_module, summarizer_model_info["class"])
        summarizer_model = summarizer_model_class.from_dict(summarizer_model_info["data"])

        # 2. Get the summary_length
        summary_length = agent_dict["summary_length"] # It should always be present

        # 3. Prepare arguments for the parent CodeAgent's from_dict method
        # Filter out `summarizer_model` and `summary_length` as they are specific to this class
        # and would not be expected by the parent's from_dict.
        parent_agent_dict_for_base_init = {k: v for k, v in agent_dict.items() if k not in ["summarizer_model", "summary_length"]}
        
        # Merge external kwargs with the parent's dict, ensuring external kwargs take precedence
        parent_kwargs_for_base_init = parent_agent_dict_for_base_init.copy()
        parent_kwargs_for_base_init.update(kwargs) # Override with any direct kwargs

        # 4. Call the parent class's from_dict to get a base CodeAgent instance
        base_code_agent_instance = super().from_dict(parent_kwargs_for_base_init)

        # 5. Instantiate this SummarizingCodeAgent using parameters from the base instance
        # and the new summarizer-specific parameters.
        # We need to explicitly pass all arguments expected by CodeAgent's __init__
        # and then add our own.
        return cls(
            model=base_code_agent_instance.model,
            tools=list(base_code_agent_instance.tools.values()), # Ensure it's a list of actual Tool objects
            prompt_templates=base_code_agent_instance.prompt_templates,
            max_steps=base_code_agent_instance.max_steps,
            verbosity_level=base_code_agent_instance.logger.level, # Reuse the logger's level
            grammar=base_code_agent_instance.grammar,
            managed_agents=list(base_code_agent_instance.managed_agents.values()), # List of actual Agent objects
            planning_interval=base_code_agent_instance.planning_interval,
            name=base_code_agent_instance.name,
            description=base_code_agent_instance.description,
            provide_run_summary=base_code_agent_instance.provide_run_summary,
            final_answer_checks=base_code_agent_instance.final_answer_checks,
            logger=base_code_agent_instance.logger, # Pass the logger instance directly
            
            # CodeAgent specific params:
            additional_authorized_imports=getattr(base_code_agent_instance, 'additional_authorized_imports', []),
            executor_type=getattr(base_code_agent_instance, 'executor_type', 'local'),
            executor_kwargs=getattr(base_code_agent_instance, 'executor_kwargs', {}),
            max_print_outputs_length=getattr(base_code_agent_instance, 'max_print_outputs_length', None),
            stream_outputs=base_code_agent_instance.stream_outputs,
            
            # SummarizingCodeAgent specific params:
            summarizer_model=summarizer_model,
            summary_length=summary_length,
        )
