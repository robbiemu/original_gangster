from smolagents.tools import Tool
from types import MethodType
from typing import Callable, Any, Optional


# Define default no-op hooks for the ProxyTool
def _default_before_hook(proxy_instance: "ProxyTool", *args, **kwargs) -> None:
    """Default 'before' hook: performs no action."""
    pass


def _default_around_hook(
    proxy_instance: "ProxyTool", proceed_callable: Callable, *args, **kwargs
) -> Any:
    """
    Default 'around' hook: simply calls the underlying tool's execution.
    :param proxy_instance: The ProxyTool instance itself.
    :param proceed_callable: A callable that executes the underlying tool.
    """
    return proceed_callable(*args, **kwargs)


def _default_after_hook(
    proxy_instance: "ProxyTool",
    result: Any,
    exception: Optional[Exception],
    *args,
    **kwargs,
) -> None:
    """Default 'after' hook: performs no action."""
    pass


class ProxyTool(Tool):
    """
    A generic ProxyTool designed with Aspect-Oriented Programming (AOP) principles.
    It allows custom logic to be injected before, around, and after
    the execution of an underlying tool by passing hook functions during initialization.

    All hooks are synchronous functions to maintain compatibility with smolagents framework.
    """

    def __init__(
        self,
        name: str,
        underlying: Tool,
        description: str = None,
        before_hook: Optional[Callable] = None,
        around_hook: Optional[Callable] = None,
        after_hook: Optional[Callable] = None,
    ):
        """
        Initializes the ProxyTool.

        :param name: The name of the proxy tool.
        :param underlying: The actual Tool instance that this proxy wraps.
        :param description: An optional description for the proxy tool. If not provided,
                            a default based on the underlying tool's description is used.
        :param before_hook: A synchronous callable to execute before the underlying tool.
                            Signature: `def(proxy_instance, *args, **kwargs) -> None`
        :param around_hook: A synchronous callable to execute around the underlying tool.
                            Signature: `def(proxy_instance, proceed_callable: Callable, *args, **kwargs) -> Any`
                            `proceed_callable` is a function that executes the underlying tool.
        :param after_hook: A synchronous callable to execute after the underlying tool,
                           regardless of success or failure.
                           Signature: `def(proxy_instance, result: Any, exception: Optional[Exception], *args, **kwargs) -> None`
        """
        # Call base Tool constructor first, it sets self.name and self.description
        super().__init__(name=name, description=description)

        # Explicitly set attributes that might be used by _bind_forward or description logic
        self.name = name
        self.inputs = getattr(underlying, "inputs", {})
        self.output_type = getattr(underlying, "output_type", "string")

        # Generate a default description if not provided, based on the underlying tool
        underlying_description = getattr(underlying, "description", None)
        if not underlying_description:
            doc = getattr(underlying, "__doc__", "")
            underlying_description = (
                doc.strip().split("\n")[0] if doc else "an unspecified action"
            )

        # Use provided description or fall back to a generic one
        self.description = (
            description
            if description is not None
            else f"Proxy for: {underlying_description}"
        )

        self.underlying = underlying

        # Store the provided hooks or default to no-op functions
        self._before_hook_func = (
            before_hook if before_hook is not None else _default_before_hook
        )
        self._around_hook_func = (
            around_hook if around_hook is not None else _default_around_hook
        )
        self._after_hook_func = (
            after_hook if after_hook is not None else _default_after_hook
        )

        # Dynamically bind the 'forward' method after all necessary attributes are set
        self._bind_forward(self.inputs, self.output_type)

    def _bind_forward(self, inputs: dict, output_type: str):
        """
        Dynamically creates a 'forward' method that matches the signature
        of the underlying tool's inputs, allowing direct calls to the proxy.
        This allows the ProxyTool to be called like the underlying tool itself.
        """
        arg_names = list(inputs.keys())
        arg_list = ", ".join(arg_names)
        kwargs_dict = ", ".join(f"'{k}': {k}" for k in arg_names)

        method_src = f"""
def forward(self, {arg_list}):
    return self.run(**{{{kwargs_dict}}})
"""
        local_ns = {}
        exec(method_src, {}, local_ns)
        # Bind the dynamically created method to the instance
        self.forward = MethodType(local_ns["forward"], self)

    def run(self, *args, **kwargs) -> Any:
        """
        The core execution method for the ProxyTool.
        It orchestrates the AOP hooks around the execution of the underlying tool.
        All operations are synchronous to maintain compatibility with smolagents framework.
        """
        result = None
        exception = None

        # Define the actual underlying tool's execution as a callable for the 'around' hook
        def _proceed_with_underlying_tool(*_args, **_kwargs):
            return self.underlying.forward(*_args, **_kwargs)

        try:
            # 1. Execute the Before hook
            self._before_hook_func(self, *args, **kwargs)

            # 2. Execute the Around hook, passing the callable to proceed with the underlying tool
            result = self._around_hook_func(
                self, _proceed_with_underlying_tool, *args, **kwargs
            )

        except Exception as e:
            # Capture any exception that occurred during a hook or from the underlying tool
            exception = e
        finally:
            # 3. Execute the After hook, ensuring it always runs
            self._after_hook_func(self, result, exception, *args, **kwargs)

        # If an exception occurred, re-raise it after the after_hook has run
        if exception:
            raise exception

        return result
