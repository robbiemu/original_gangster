import subprocess
from pathlib import Path
from smolagents.tools import tool


@tool
def shell_tool(command: str) -> str:
    """
    Executes a shell command or commands and returns its combined stdout and stderr.
    Includes the command's exit code if non-zero.

    Args:
        command: The shell command(s) to execute.

    Returns:
        A string containing the combined stdout and stderr, clearly labeled.
        If the command has no output, it returns a placeholder message.
        If the command exits with a non-zero status, this is also noted.
    """
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        check=False,  # Do not raise CalledProcessError on non-zero exit codes,
        # instead capture and report the returncode.
    )

    combined_output_parts = []

    if result.stdout:
        combined_output_parts.append("--- STDOUT ---")
        combined_output_parts.append(result.stdout.strip())

    if result.stderr:
        # Only add STDERR header if there's actual stderr content
        # unless STDOUT was also empty, then always show it.
        if result.stdout or result.stderr.strip():
            combined_output_parts.append("--- STDERR ---")
            combined_output_parts.append(result.stderr.strip())

    # Add exit code if it's not 0
    if result.returncode != 0:
        combined_output_parts.append(
            f"--- Command exited with status: {result.returncode} ---"
        )

    # If no output at all (neither stdout, stderr, nor non-zero exit code indicator)
    if not combined_output_parts:
        return "[Command executed with no output]"

    return "\n".join(combined_output_parts)


@tool
def file_content_tool(path: str) -> str:
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
