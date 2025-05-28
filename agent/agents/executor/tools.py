import subprocess # Correct import for synchronous subprocess.run
from pathlib import Path
from smolagents.tools import tool


@tool
def shell_tool(command: str) -> str:
    """
    Executes a shell command or commands and returns its output.

    Args:
        command: The shell command(s) to execute.

    Returns:
        The output from the command execution.
    """
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    return result.stdout.strip()

@tool
def file_tool(path: str) -> str:
    """
    Reads the contents of a file at the given path.

    Args:
        path: The absolute or relative path to the file.

    Returns:
        The text contents of the file.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return f"[ERROR] Not a file: {p}"
    try:
        return p.read_text()
    except Exception as e:
        return f"[ERROR] {e}"
