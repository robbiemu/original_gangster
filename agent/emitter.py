import json
from typing import Any, Callable, Dict
from agent.log_levels import LogLevel

# This global variable will store the Python agent's configured log level.
_python_log_level: LogLevel = LogLevel.INFO


def set_python_log_level(level_str: str):
    """Sets the Python agent's internal log level based on string input."""
    global _python_log_level
    try:
        _python_log_level = LogLevel[level_str.upper()]
    except KeyError:
        # Fallback to INFO if an invalid string is provided.
        # This should ideally be caught by argparse in main.py.
        _python_log_level = LogLevel.INFO


def emit(msg_type: str, data: dict):
    """
    Emits a structured message to stdout as NDJSON.
    Filters certain log message types based on the configured Python log level.
    """
    # Map Python log types to LogLevel for filtering
    log_type_map = {
        "debug_log": LogLevel.DEBUG,
        "info_log": LogLevel.INFO,
        "warn_log": LogLevel.WARN,
    }

    # If it's a categorized log message, check against the current Python log level
    if msg_type in log_type_map:
        if log_type_map[msg_type] >= _python_log_level:
            payload = {"type": msg_type, **data}
            print(json.dumps(payload), flush=True)
    else:
        # Core messages (error, unsafe, plan, result etc.) always emit regardless of Python log level.
        # Go client handles final filtering/display for these.
        payload = {"type": msg_type, **data}
        print(json.dumps(payload), flush=True)


_EmitterCallable = Callable[[str, Dict[str, Any]], None]
