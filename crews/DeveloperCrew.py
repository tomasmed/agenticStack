"""
Developer
---------
Pure orchestration — no LLM wrapper.
Parses tickets from TeamLead, runs one aider call per ticket, one commit each.

Aider runs as a uv-managed tool or isolated venv to avoid dependency conflicts.

Stops on first failure — later tickets depend on earlier ones.
Git identity switched to agent for commits, restored to human in finally.
All git operations run in the target project repo (Repo B).

Resume behaviour: on retry, tickets whose commit message already exists in
commits made AFTER the branch diverged from main are skipped. This scopes
the check to the current feature branch only, not the full repo history.
"""

import os
import re
import subprocess
import time
from pathlib import Path
import shutil
from dotenv import dotenv_values


# ─────────────────────────────────────────────────────────────
# Aider path resolution
# ─────────────────────────────────────────────────────────────

def _get_aider_path() -> str:
    uv_managed_aider = shutil.which("aider")
    if uv_managed_aider:
        return uv_managed_aider

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
    try:
        content = Path(tickets_path).read_text()
    except Exception as e:
        print(f"[Developer] ERROR reading tickets file: {e}")
        return []

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
# Resume helpers — scoped to current branch only
# ─────────────────────────────────────────────────────────────

def _committed_tickets_on_branch(cwd: str, env: dict) -> set[str]:
    """
    Return set of ticket numbers already committed on the current branch,
    scoped to commits that diverged from main. This prevents commits from
    previous feature runs polluting the skip list.
    """
    try:
        # find the merge base — where this branch diverged from main
        merge_base = subprocess.run(
            ["git", "merge-base", "HEAD", "main"],
            cwd=cwd, capture_output=True, text=True, env=env
        )
        if merge_base.returncode != 0:
            # main may not exist yet — fall back to full log
            ref = "HEAD"
        else:
            ref = f"{merge_base.stdout.strip()}..HEAD"

        result = subprocess.run(
            ["git", "log", "--oneline", ref],
            cwd=cwd, capture_output=True, text=True, env=env
        )
        committed = set()
        for line in result.stdout.splitlines():
            match = re.search(r'\b(T-\d+):', line)
            if match:
                committed.add(match.group(1))
        return committed
    except Exception as e:
        print(f"[Developer] WARN — could not read git log: {e}. No tickets will be skipped.")
        return set()


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
    timeout     = int(env.get("AIDER_TIMEOUT", "300"))

    print(f"  Files:   {', '.join(files)}")
    print(f"  Model:   ollama/{dev_model}")
    print(f"  Aider:   {aider_path}")
    print(f"  Timeout: {timeout}s")

    cmd = [
        aider_path,
        "--model",           f"ollama/{dev_model}",
        "--openai-api-base", f"{ollama_host}/v1",
        "--message",         message,
        "--yes",
        "--no-auto-commits",
        "--no-pretty",
        "--no-suggest-shell-commands",
        "--no-check-update",
        "--map-tokens",      "1024",
    ]

    dev_manifest = Path("manifests/Developer.md")
    if dev_manifest.exists():
        cmd += ["--read", str(dev_manifest)]

    cmd += files

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env
        )
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] aider timed out on {ticket['number']} after {timeout}s")
        print(f"  Tip: increase AIDER_TIMEOUT in .env (currently {timeout}s)")
        return False

    if result.stdout:
        print(f"  aider stdout:\n{result.stdout[-1000:]}")
    if result.stderr:
        print(f"  aider stderr:\n{result.stderr[-500:]}")

    return result.returncode == 0


def _commit_ticket(ticket: dict, env: dict, cwd: str) -> bool:
    """Stage ticket files explicitly and commit. Returns False if nothing to commit."""
    files_to_stage = [f for f in ticket["files"] if (Path(cwd) / f).exists()]
    if files_to_stage:
        subprocess.run(["git", "add", "--"] + files_to_stage, cwd=cwd, env=env)
    else:
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

def run_developer_crew(tickets_path: str, context_path: str, target_repo: str):
    """
    Parse tickets, run aider once per ticket, commit each.
    No LLM — pure deterministic orchestration.
    Skips tickets already committed on the current branch since diverging
    from main — resume safe without polluting skip list from previous runs.
    Stops on first failure — later tickets depend on earlier ones.

    Args:
        tickets_path: path to tickets.md (inside run_dir)
        context_path: path to project_context.md (inside workspace/context/)
        target_repo:  path to Repo B — all git ops and aider run here
    """
    env = os.environ.copy()
    env.update(dotenv_values(".env"))
    cwd = str(target_repo)

    try:
        project_context = Path(context_path).read_text() if Path(context_path).exists() else ""
    except Exception as e:
        print(f"[Developer] WARN — could not read project context: {e}")
        project_context = ""

    tickets = _parse_tickets(tickets_path)
    if not tickets:
        print("[Developer] No tickets found — check ticket format in tickets.md")
        return

    # scope skip check to this branch only — not full repo history
    already_committed = _committed_tickets_on_branch(cwd, env)
    if already_committed:
        print(f"[Developer] Already committed on this branch: {sorted(already_committed)} — skipping")

    pending = [t for t in tickets if t["number"] not in already_committed]

    if not pending:
        print("[Developer] All tickets already committed on this branch — nothing to do")
        return

    print(f"[Developer] {len(tickets)} total ticket(s), {len(pending)} pending")
    print(f"[Developer] Working in: {cwd}")

    human_name  = env.get("GIT_USER_NAME", "")
    human_email = env.get("GIT_USER_EMAIL", "")
    agent_name  = env.get("AGENT_NAME",  "agenticScheduler[bot]")
    agent_email = env.get("AGENT_EMAIL", "agenticscheduler@users.noreply.github.com")

    subprocess.run(["git", "config", "user.name",  agent_name],  cwd=cwd, env=env)
    subprocess.run(["git", "config", "user.email", agent_email], cwd=cwd, env=env)

    completed = []

    try:
        for i, ticket in enumerate(pending, 1):
            print(f"\n[Developer] [{i}/{len(pending)}] {ticket['number']}: {ticket['title']}")

            success = _run_aider(ticket, project_context, env, cwd)
            if not success:
                raise RuntimeError(
                    f"Aider failed on {ticket['number']}. "
                    f"Completed this run: {[t['number'] for t in completed]}. "
                    f"Already committed on branch: {sorted(already_committed)}. "
                    "Fix and reset developer stage to pending to retry."
                )

            committed = _commit_ticket(ticket, env, cwd)
            if not committed:
                raise RuntimeError(
                    f"{ticket['number']} produced no file changes. "
                    "Check aider output above — model may not have understood the instruction."
                )

            completed.append(ticket)
            if i < len(pending):
                time.sleep(1)

        print(f"\n[Developer] All {len(pending)} pending ticket(s) implemented")

    finally:
        if human_name:
            subprocess.run(["git", "config", "user.name",  human_name],  cwd=cwd, env=env)
        if human_email:
            subprocess.run(["git", "config", "user.email", human_email], cwd=cwd, env=env)