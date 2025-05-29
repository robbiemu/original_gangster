from importlib import resources
import subprocess


def run_show_context_script():
    script_content = (
        resources.files("agent.scripts").joinpath("show_context.sh").read_text()
    )
    result = subprocess.run(
        ["bash", "-s"],
        input=script_content,
        capture_output=True,
        text=True,
    )
    return result.stdout
