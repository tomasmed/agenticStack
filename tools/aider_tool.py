
# ─────────────────────────────────────────────────────────────
# tools/aider_tool.py
from crewai.tools import BaseTool
import subprocess
import shutil
from pathlib import Path

def _get_aider_path() -> str:
    # try aider venv first
    aider_venv = Path(".venv-aider/bin/aider")
    if aider_venv.exists():
        return str(aider_venv)
    # fall back to PATH
    found = shutil.which("aider")
    if found:
        return found
    raise RuntimeError(
        "aider not found. Run: python -m venv .venv-aider && "
        "source .venv-aider/bin/activate && pip install aider-chat"
    )

class AiderTool(BaseTool):
    name: str = "run_aider"
    description: str = """Run aider to implement code changes.
    Pass files (list of file paths) and instruction (what to implement).
    Aider will modify the files according to the instruction and commit."""
 
    def _run(self, files: list[str], instruction: str) -> str:
        import shutil
        aider_path = _get_aider_path()
        if not aider_path:
            return "FATAL: aider not found in PATH. Install with: pip install aider-chat"

        try:
            cmd = [
                "aider",
                "--yes",
                "--no-auto-commits",
                "--no-pretty",
                "--no-suggest-shell-commands",
                "--no-check-update",
                "--map-tokens", "1024",
                "--message", instruction,
            ] + files
 
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
 
            if result.returncode != 0:
                return f"Aider failed:\n{result.stderr}"
 
            return result.stdout or "Aider completed successfully"
 
        except subprocess.TimeoutExpired:
            return "Aider timed out after 5 minutes"
        except Exception as e:
            return f"Aider error: {str(e)}"