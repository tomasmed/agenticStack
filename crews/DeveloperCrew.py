"""
Developer
---------
Pure orchestration — no LLM wrapper.
Parses tickets from TeamLead, runs one aider call per ticket, one commit each.

Aider runs in an isolated venv (.venv-aider) to avoid dependency conflicts
with crewai. Falls back to PATH aider if isolated venv not found.

Stops on first failure — later tickets may depend on earlier ones.
Git identity switched to agent for commits, restored to human in finally.
"""

import os
import re
import subprocess
import time
from pathlib import Path
import shutil
from dotenv import dotenv_values


# ─────────────────────────────────────────────────────────────
# Aider path resolution — Updated for UV Tools
# ─────────────────────────────────────────────────────────────

def _get_aider_path() -> str:
    # 1. Check if 'aider' is available in the PATH (where uv tool installs it)
    uv_managed_aider = shutil.which("aider")
    if uv_managed_aider:
        return uv_managed_aider

    # 2. Fallback to a .venv pattern
    isolated_venv = Path(".venv-aider/bin/aider")
    if isolated_venv.exists():
        return str(isolated_venv)

    raise RuntimeError(
        "aider not found.\n"
        "Since you are using uv, please run: uv tool install aider-chat"
    )


# ─────────────────────────────────────────────────────────────
# Ticket parser — matches TeamLead **ticket:** T-N format
# ─────────────────────────────────────────────────────────────

def _parse_tickets(tickets_path: str) -> list[dict]:
    content = Path(tickets_path).read_text()
    tickets = []
    blocks  = re.split(r'\*\*ticket:\*\*', content)[1:]

    for block in blocks:
        number = re.search(r'(T-\d+)', block)
        title  = re.search(r'\*\*title:\*\*\s*(.+)', block)
        files  = re.search(r'\*\*files:\*\*\s*(.+)', block)
        desc   = re.search(r'\*\*description:\*\*\s*(.*?)(?=\*\*acceptance:|$)', block, re.DOTALL)
        accept = re.search(r'\*\*acceptance:\*\*\s*(.*?)(?=---|\*\*ticket:|$)', block, re.DOTALL)

        if not (number and title and files):
            continue

        tickets.append({
            "number":      number.group(1),
            "title":       title.group(1).strip(),
            "files":       [f.strip().strip('`') for f in files.group(1).split(",") if f.strip()],
            "description": desc.group(1).strip() if desc else "",
            "acceptance":  accept.group(1).strip() if accept else "",
        })

    return tickets


# ─────────────────────────────────────────────────────────────
# Aider invocation
# ─────────────────────────────────────────────────────────────

def _build_message(ticket: dict, project_context: str) -> str:
    return f"""You are implementing one development ticket exactly as specified.
Do not add scope. Do not modify files not listed in the ticket.
Do not combine with other tickets. Implement only what is described below.

STACK CONVENTIONS:
{project_context}

TICKET: {ticket['number']} — {ticket['title']}

DESCRIPTION:
{ticket['description']}

DONE WHEN:
{ticket['acceptance']}
"""


def _run_aider(ticket: dict, project_context: str, env: dict, cwd: str) -> bool:
    aider_path = _get_aider_path()
    message    = _build_message(ticket, project_context)
    files      = ticket["files"]

    if not files:
        print(f"  [WARN] No files in {ticket['number']} — skipping")
        return False

    ollama_host = env.get("OLLAMA_HOST", "").strip()
    dev_model   = env.get("DEV_MODEL", "").strip()

    print(f"  Files:  {', '.join(files)}")
    print(f"  Model:  ollama/{dev_model}")
    print(f"  Aider:  {aider_path}")

    cmd = [
        aider_path,
        "--model",          f"ollama/{dev_model}",
        "--openai-api-base", f"{ollama_host}/v1",
        "--message",        message,
        "--yes",
        "--no-auto-commits",
        "--no-pretty",
        "--no-suggest-shell-commands",
        "--no-check-update",
        "--map-tokens",     "1024",
    ]

    # developer manifest as read-only context
    dev_manifest = Path("manifests/Developer.md")
    if dev_manifest.exists():
        cmd += ["--read", str(dev_manifest)]

    cmd += files

    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=300,
        env=env
    )

    # always print aider output so failures are visible
    if result.stdout:
        print(f"  aider stdout:\n{result.stdout[-1000:]}")
    if result.stderr:
        print(f"  aider stderr:\n{result.stderr[-500:]}")

    return result.returncode == 0


def _commit_ticket(ticket: dict, env: dict, cwd: str) -> bool:
    """Stage all changes and commit. Returns False if nothing to commit."""
    subprocess.run(["git", "add", "-A"], cwd=cwd, env=env)

    status = subprocess.run(
        ["git", "diff", "--cached", "--stat"],
        cwd=cwd, capture_output=True, text=True, env=env
    )

    if not status.stdout.strip():
        print(f"  [WARN] Nothing staged after {ticket['number']}")
        return False

    msg = f"{ticket['number']}: {ticket['title']}"
    subprocess.run(["git", "commit", "-m", msg], cwd=cwd, env=env)
    print(f"  Committed: {msg}")
    return True


# ─────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────

def run_developer_crew(tickets_path: str, context_path: str):
    """
    Parse tickets, run aider once per ticket, commit each.
    No LLM — pure deterministic orchestration.
    Stops on first failure — later tickets depend on earlier ones.
    """
    env             = os.environ.copy()
    env.update(dotenv_values(".env"))
    cwd             = str(Path.cwd())
    project_context = Path(context_path).read_text() if Path(context_path).exists() else ""
    tickets         = _parse_tickets(tickets_path)

    if not tickets:
        print("[Developer] No tickets found — check ticket format in tickets.md")
        return

    print(f"[Developer] {len(tickets)} ticket(s) to implement")

    # switch to agent git identity
    human_name  = env.get("GIT_USER_NAME", "")
    human_email = env.get("GIT_USER_EMAIL", "")
    agent_name  = env.get("AGENT_NAME",  "agenticScheduler[bot]")
    agent_email = env.get("AGENT_EMAIL", "agenticscheduler@users.noreply.github.com")

    subprocess.run(["git", "config", "user.name",  agent_name],  cwd=cwd, env=env)
    subprocess.run(["git", "config", "user.email", agent_email], cwd=cwd, env=env)

    completed = []

    try:
        for i, ticket in enumerate(tickets, 1):
            print(f"\n[Developer] [{i}/{len(tickets)}] {ticket['number']}: {ticket['title']}")

            success = _run_aider(ticket, project_context, env, cwd)
            if not success:
                raise RuntimeError(
                    f"Aider failed on {ticket['number']}. "
                    f"Completed: {[t['number'] for t in completed]}. "
                    "Fix and reset developer stage to pending."
                )

            committed = _commit_ticket(ticket, env, cwd)
            if not committed:
                raise RuntimeError(
                    f"{ticket['number']} produced no file changes. "
                    "Check aider output above — model may not have understood the instruction."
                )

            completed.append(ticket)
            if i < len(tickets):
                time.sleep(1)

        print(f"\n[Developer] All {len(tickets)} tickets implemented")

    finally:
        # always restore human git identity
        if human_name:
            subprocess.run(["git", "config", "user.name",  human_name],  cwd=cwd, env=env)
        if human_email:
            subprocess.run(["git", "config", "user.email", human_email], cwd=cwd, env=env)