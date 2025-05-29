import os
import shutil
from smolagents.tools import tool
import subprocess
from typing import Dict, Optional


@tool
def man_page(name: str) -> Optional[str]:
    """Rendered man page via `col -bx`.
    example usage: (this is not a suggestion just an example)
        page = man_page("grep")
        if page: print("\n".join(line for line in page.splitlines() if "-Z" in line))

    Tip: do not ask for common paramaeters in the query, instead search for them. IE, use man_page("docker") and then search for the run parameter in the output, rather than using man_page("docker run").

    Args:
        name: The name of the man page to retrieve (e.g., "ls", "grep").
    """

    try:
        raw = subprocess.check_output(
            ["man", name], text=True, stderr=subprocess.STDOUT
        )
        return subprocess.run(
            ["col", "-bx"], input=raw, text=True, capture_output=True
        ).stdout
    except Exception:
        return f"--No result for '{name}'--"


@tool
def info_page(name: str) -> Optional[str]:
    """GNU info page.
    example usage: (this is not a suggestion just an example)
        info = info_page("ps")
        print(info)

    Tip: do not ask for common paramaeters in the query, instead search for them. IE, use info_page("docker") and then search for the run parameter in the output, rather than using info_page("docker run").

    Args:
        name: The name of the info page to retrieve (e.g., "coreutils", "bash").
    """

    try:
        return subprocess.check_output(
            ["info", name], text=True, stderr=subprocess.STDOUT
        )
    except Exception:
        return f"--No result for '{name}'--"


@tool
def tldr_page(name: str) -> Optional[str]:
    """Local TLDR examples.
    Examples:
    - print(tldr_page("lsof"))
    - print(tldr_page("npm install"))

    Args:
        name: The command for which to retrieve TLDR examples (e.g., "tar", "git").
    """

    for cmd in (["tlrc", "--no-color", "--quiet", name], ["tldr", "-q", name]):
        if shutil.which(cmd[0]):
            try:
                return subprocess.check_output(cmd, text=True)
            except subprocess.CalledProcessError:
                return f"--No result for '{name}'--"
    return None


@tool
def help_flag(command: str, with_col_bx: bool = False) -> Optional[str]:
    """Output from the `<command> --help` if available.
    Supports both simple commands and commands with subcommands/parameters.
    Optionally pipes the output through `col -bx` for plaintext formatting.

    Examples:
    - print(help_flag("grep"))
    - print(help_flag("docker build", with_col_bx=True))

    Args:
        command: The command (with optional subcommands) for which to retrieve help output.
        with_col_bx: If True, pipe the output through `col -bx` to remove backspaces and other formatting.
    """
    cmd_parts = command.split()
    if not cmd_parts:
        return None

    raw_output: Optional[str] = None

    # Try with --help first
    try:
        raw_output = subprocess.check_output(
            cmd_parts + ["--help"], text=True, stderr=subprocess.STDOUT, timeout=10
        )
    except subprocess.CalledProcessError:
        # Some commands use -h instead of --help
        try:
            raw_output = subprocess.check_output(
                cmd_parts + ["-h"], text=True, stderr=subprocess.STDOUT, timeout=10
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # Keep raw_output as None to try other methods or return None
    except subprocess.TimeoutExpired:
        return "Command timed out."
    except Exception:
        pass  # Keep raw_output as None

    # Try help subcommand for some tools (like git help status) if direct flags failed
    if raw_output is None:
        try:
            if len(cmd_parts) > 1:
                help_cmd = [cmd_parts[0], "help"] + cmd_parts[1:]
                raw_output = subprocess.check_output(
                    help_cmd, text=True, stderr=subprocess.STDOUT, timeout=10
                )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, Exception):
            pass  # Keep raw_output as None

    if raw_output is None:
        return None  # No help found after all attempts

    if with_col_bx:
        try:
            return subprocess.run(
                ["col", "-bx"], input=raw_output, text=True, capture_output=True
            ).stdout
        except Exception:
            # Fallback to raw output if col -bx fails for any reason
            return raw_output

    return raw_output


@tool
def probe(name: str) -> Optional[str]:
    """Show the location and type of a command.
    example usage: `print(probe("lsof"))`. (this is not a suggestion just an example)

    Args:
        name: The command to probe (e.g., "python3", "ls").
    """

    path = shutil.which(name)
    if not path:
        return f"Command '{name}' not found in PATH."
    abs_path = os.path.realpath(path)
    directory = os.path.dirname(abs_path)
    try:
        file_output = subprocess.check_output(
            ["file", "--brief", "--mime", abs_path], text=True
        ).strip()
    except subprocess.CalledProcessError:
        file_output = "Unknown file type"
    is_binary = "charset=binary" in file_output
    return (
        f"Command: {name}\n"
        f"Full Path: {abs_path}\n"
        f"Directory: {directory}\n"
        f"File Type: {file_output}\n"
        f"Is Binary: {'Yes' if is_binary else 'No'}"
    )


@tool
def brew_info(name: str) -> Optional[str]:
    """Full `brew info` output for a Homebrew package.
    example usage: `print(brew_info("sqlite"))`. (this is not a suggestion just an example)

    Args:
        name: The name of the Homebrew package (e.g., "git", "node").
    """

    try:
        return subprocess.check_output(["brew", "info", name], text=True)
    except Exception:
        return f"--No result for '{name}'--"


def get_common_tools():
    """
    Returns list of common tools, conditionally including optional ones based on availability.
    """
    tools = [
        man_page,
        help_flag,
        probe,
    ]

    availability = check_planner_tool_availability()

    if availability["tldr_page"]:
        tools.append(tldr_page)
    if availability["info_page"]:
        tools.append(info_page)
    if availability["brew_info"]:
        tools.append(brew_info)

    return tools


def check_planner_tool_availability() -> Dict[str, bool]:
    """
    Checks the availability of conditional planning tools by looking for their executables.
    Returns a dictionary mapping tool names (as used in the prompt) to boolean availability.
    """
    availability = {
        "info_page": bool(shutil.which("info")),
        "tldr_page": bool(shutil.which("tlrc") or shutil.which("tldr")),
        "brew_info": bool(shutil.which("brew")),
    }
    return availability
