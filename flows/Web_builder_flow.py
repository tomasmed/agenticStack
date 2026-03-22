from crewai.flow.flow import Flow, listen, start
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import uuid, subprocess, json, os, shutil, re

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

from crews import (
    build_app_summariser_crew,
    build_product_owner_crew,
    build_team_lead_crew,
    run_art_director,
    run_artist,
    run_codebase_reader,
    run_developer_crew,
)

WORKSPACE   = Path("workspace")
CURRENT_DIR = WORKSPACE / "current"
RUNS_DIR    = WORKSPACE / "runs"
RESUME_SAFE = {"complete", "approved"}


# ─────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    state_path = CURRENT_DIR / "pipeline_state.json"
    if not state_path.exists():
        raise FileNotFoundError(
            "No active run found. "
            "Start a new run with: python main.py build '<request>'"
        )
    return json.loads(state_path.read_text())


def _update_state(stage: str, status: str):
    path = CURRENT_DIR / "pipeline_state.json"
    state = json.loads(path.read_text())
    state["stages"][stage]["status"] = status
    if status in (*RESUME_SAFE, "awaiting_review", "failed"):
        state["stages"][stage]["completed_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2))

    # mirror to runs/ for history
    run_id = state["run_id"]
    run_copy = RUNS_DIR / run_id / "pipeline_state.json"
    if run_copy.parent.exists():
        run_copy.write_text(json.dumps(state, indent=2))


def _already_done(stage: str) -> bool:
    try:
        state = _load_state()
    except FileNotFoundError:
        return False
    status = state["stages"].get(stage, {}).get("status", "pending")
    if status in RESUME_SAFE:
        print(f"[WebBuilderFlow] Skipping {stage} — already {status}")
        return True
    return False


def _human_gate(stage: str, review_path: str):
    _update_state(stage, "awaiting_review")
    print(f"\n{'='*50}")
    print(f">>> HUMAN GATE: {stage}")
    print(f">>> Open and review: {review_path}")
    print(f">>> ")
    print(f">>>   code {review_path}")
    print(f">>> ")
    print(f">>> Press Enter to approve, Ctrl+C to abort: ", end="")
    input()
    _update_state(stage, "approved")


def _mirror_to_runs(run_id: str):
    """Copy current/ contents to runs/{run_id}/ for history."""
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for f in CURRENT_DIR.iterdir():
        if f.is_file() and f.name != ".run_id":
            shutil.copy2(f, run_dir / f.name)


# ─────────────────────────────────────────────────────────────
# Feature name extraction + branch rename
# ─────────────────────────────────────────────────────────────

def _extract_feature_name(brief_path: str) -> str | None:
    """
    Parse ## Feature Name section from brief.md.
    Returns sanitised kebab-case name or None if not found/invalid.
    """
    try:
        content = Path(brief_path).read_text()
        match = re.search(
            r'##\s*Feature Name\s*\n+([^\n#]+)',
            content, re.IGNORECASE
        )
        if not match:
            return None
        name = match.group(1).strip()
        # sanitise to valid kebab-case
        name = re.sub(r'[^a-z0-9\-]', '-', name.lower())
        name = re.sub(r'-+', '-', name).strip('-')
        # enforce reasonable length
        words = [w for w in name.split('-') if w]
        if len(words) < 2 or len(words) > 6:
            return None
        return '-'.join(words)
    except Exception:
        return None


def _rename_branch(old_branch: str, feature_name: str) -> str:
    """
    Rename current git branch to feature/{feature_name}.
    Updates pipeline_state.json with new branch name.
    Returns the new branch name (or old if rename failed).
    """
    new_branch = f"feature/{feature_name}"
    result = subprocess.run(
        ["git", "branch", "-m", old_branch, new_branch],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        state = _load_state()
        state["branch"] = new_branch
        (CURRENT_DIR / "pipeline_state.json").write_text(json.dumps(state, indent=2))
        print(f"[WebBuilderFlow] Branch renamed: {old_branch} → {new_branch}")
        return new_branch
    else:
        print(f"[WebBuilderFlow] Branch rename failed: {result.stderr.strip()}")
        print(f"[WebBuilderFlow] Keeping: {old_branch}")
        return old_branch


# ─────────────────────────────────────────────────────────────
# PR creation
# ─────────────────────────────────────────────────────────────

def _create_pull_request(run_id: str, branch: str, request: str):
    """Push branch and open PR to main via GitHub CLI."""
    push = subprocess.run(
        ["git", "push", "-u", "origin", branch],
        capture_output=True, text=True
    )
    if push.returncode != 0:
        print(f"[WebBuilderFlow] Push failed:\n{push.stderr}")
        print(f"[WebBuilderFlow] Create PR manually: gh pr create --base main --head {branch}")
        return

    print(f"[WebBuilderFlow] Pushed: {branch}")

    body = f"""## 🤖 Agent-generated PR

| Field | Value |
|-------|-------|
| Run ID | `{run_id}` |
| Branch | `{branch}` |
| Agent | agenticScheduler WebBuilderFlow |
| Model | TeamLead: {os.getenv('FRONTIER_MODEL')} / Dev: {os.getenv('DEV_MODEL')} |
| Tickets | `workspace/runs/{run_id}/tickets.md` |

### Original request
> {request}

### Review checklist
- [ ] Brief reviewed and approved
- [ ] Tickets reviewed and approved
- [ ] Code diff reviewed
- [ ] Build passes

> ⚠️ This PR was created by an automated agent. Review carefully before merging.
"""

    pr = subprocess.run(
        [
            "gh", "pr", "create",
            "--base",  "main",
            "--head",  branch,
            "--title", f"[agent] {request[:72]}",
            "--body",  body,
            "--label", "agent-generated",
        ],
        capture_output=True, text=True
    )

    if pr.returncode != 0:
        print(f"[WebBuilderFlow] PR creation failed:\n{pr.stderr}")
        print(f"[WebBuilderFlow] Create manually: gh pr create --base main --head {branch}")
    else:
        print(f"\n[WebBuilderFlow] PR created: {pr.stdout.strip()}")


# ─────────────────────────────────────────────────────────────
# Run initialisation — no symlinks, real folders only
# ─────────────────────────────────────────────────────────────

def _init_run(request: str) -> tuple[str, bool]:
    # remove any leftover symlink from old version
    if CURRENT_DIR.is_symlink():
        CURRENT_DIR.unlink()

    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # check for resumable run
    run_id_file = CURRENT_DIR / ".run_id"
    if run_id_file.exists():
        try:
            run_id = run_id_file.read_text().strip()
            state  = _load_state()
            all_done = all(
                s.get("status") in RESUME_SAFE
                for s in state["stages"].values()
            )
            if not all_done:
                print(f"\n[WebBuilderFlow] Resuming run: {run_id}")
                return run_id, True
        except Exception:
            pass  # corrupt state — start fresh

    # new run
    slug   = request[:24].replace(" ", "-").lower()
    date   = datetime.now().strftime("%Y%m%d")
    run_id = f"{slug}-{date}-{uuid.uuid4().hex[:4]}"

    run_id_file.write_text(run_id)

    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "run_id":           run_id,
        "original_request": request,
        "branch":           f"feature/{run_id}",
        "started_at":       datetime.now(timezone.utc).isoformat(),
        "stages": {
            s: {"status": "pending"}
            for s in [
                "summarise", "product_owner", "codebase_index",
                "team_lead", "art_director", "artist", "developer"
            ]
        }
    }
    (CURRENT_DIR / "pipeline_state.json").write_text(json.dumps(state, indent=2))
    (run_dir / "pipeline_state.json").write_text(json.dumps(state, indent=2))

    subprocess.run(["git", "config", "user.name",  os.getenv("AGENT_NAME",  "agent")],       check=False)
    subprocess.run(["git", "config", "user.email", os.getenv("AGENT_EMAIL", "agent@local")], check=False)
    subprocess.run(["git", "checkout", "-b", f"feature/{run_id}"],                            check=False)

    print(f"\n[WebBuilderFlow] New run: {run_id}")
    print(f"[WebBuilderFlow] Workspace: {CURRENT_DIR}")
    return run_id, False


# ─────────────────────────────────────────────────────────────
# Flow
# ─────────────────────────────────────────────────────────────

class WebBuilderFlow(Flow):

    @start()
    def initialise(self):
        request = self.state.get("business_request", "")
        run_id, is_resume = _init_run(request)
        self.state["run_id"]    = run_id
        self.state["is_resume"] = is_resume

    @listen(initialise)
    def summarise_app(self, _):
        if _already_done("summarise"):
            return
        _update_state("summarise", "running")

        result = build_app_summariser_crew().kickoff()
        out = Path("workspace/generated/app_state.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.raw)
        _update_state("summarise", "complete")
        print("[WebBuilderFlow] Written: workspace/generated/app_state.md")

    @listen(summarise_app)
    def run_product_owner(self, _):
        if _already_done("product_owner"):
            return
        _update_state("product_owner", "running")

        result = build_product_owner_crew(
            request=_load_state()["original_request"],
            context_path="workspace/context/project_context.md",
            app_state_path="workspace/generated/app_state.md",
        ).kickoff()

        out = Path("workspace/current/brief.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.raw)

        # rename branch to reflect PO's feature name
        # human reads the brief and approves — name is part of what they review
        feature_name = _extract_feature_name("workspace/current/brief.md")
        if feature_name:
            state      = _load_state()
            old_branch = state.get("branch", f"feature/{self.state['run_id']}")
            new_branch = _rename_branch(old_branch, feature_name)
            self.state["branch"] = new_branch
        else:
            print("[WebBuilderFlow] No valid Feature Name found — keeping run-id branch")

        _mirror_to_runs(self.state["run_id"])
        _human_gate("product_owner", "workspace/current/brief.md")

    @listen(run_product_owner)
    def index_codebase(self, _):
        if _already_done("codebase_index"):
            return
        _update_state("codebase_index", "running")

        force = self.state.get("force_reindex", False)
        run_codebase_reader(source_dir="frontend", force=force)
        run_codebase_reader(source_dir="backend",  force=force)

        _update_state("codebase_index", "complete")
        print("[WebBuilderFlow] Written: workspace/generated/codebase_context.md")

    @listen(index_codebase)
    def run_team_lead(self, _):
        if _already_done("team_lead"):
            return
        _update_state("team_lead", "running")
 
        result = build_team_lead_crew(
            brief_path="workspace/current/brief.md",
            context_path="workspace/context/project_context.md",
            codebase_index_path="workspace/generated/codebase_context.md",
            visual_identity_path="workspace/context/visual_identity.md",
        ).kickoff()
 
        # TeamLead returns two sections — split and write separately
        raw = result.raw
        if "## TICKETS" in raw and "## ASSET_MANIFEST" in raw:
            parts            = raw.split("## ASSET_MANIFEST")
            tickets_section  = parts[0].replace("## TICKETS", "").strip()
            manifest_section = parts[1].strip()
        else:
            tickets_section  = raw
            manifest_section = ""
 
        Path("workspace/current/tickets.md").write_text(tickets_section)
 
        # extract JSON from manifest section robustly
        start = manifest_section.find("{")
        end   = manifest_section.rfind("}")
        if start != -1 and end != -1:
            try:
                manifest_data = json.loads(manifest_section[start:end + 1])
                if "status" not in manifest_data:
                    manifest_data["status"] = "draft"
                Path("workspace/current/asset_manifest.json").write_text(
                    json.dumps(manifest_data, indent=2)
                )
            except json.JSONDecodeError:
                Path("workspace/current/asset_manifest.json").write_text(
                    '{"status": "draft", "assets": []}'
                )
        else:
            Path("workspace/current/asset_manifest.json").write_text(
                '{"status": "draft", "assets": []}'
            )
 
        _mirror_to_runs(self.state["run_id"])
        _human_gate("team_lead", "workspace/current/tickets.md")

    @listen(run_team_lead)
    def run_art_director(self, _):
        if _already_done("art_director"):
            return

        # skip if no assets
        manifest = Path("workspace/current/asset_manifest.json")
        if manifest.exists():
            data = json.loads(manifest.read_text())
            if not data.get("assets"):
                print("\n[WebBuilderFlow] No assets in manifest — skipping ArtDirector")
                _update_state("art_director", "approved")
                return

        _update_state("art_director", "running")

        run_art_director(
            manifest_path="workspace/current/asset_manifest.json",
            brief_path="workspace/current/brief.md",
            visual_identity_path="workspace/context/visual_identity.md",
            output_path="workspace/current/asset_manifest.json"
        )

        _mirror_to_runs(self.state["run_id"])
        print("\n[WebBuilderFlow] Written: workspace/current/asset_manifest.json (enriched)")
        print(">>> HUMAN GATE — review generation blocks in asset_manifest.json")
        print(">>> Set \"status\": \"approved\" in the manifest, then press Enter: ", end="")
        input()
        _update_state("art_director", "approved")

    @listen(run_art_director)
    def run_artist(self, _):
        if _already_done("artist"):
            return

        # skip if no assets
        manifest_data = json.loads(
            Path("workspace/current/asset_manifest.json").read_text()
        )
        if not manifest_data.get("assets"):
            print("\n[WebBuilderFlow] No assets — skipping Artist")
            _update_state("artist", "complete")
            return

        _update_state("artist", "running")
        run_artist(
            manifest_path="workspace/current/asset_manifest.json",
            project_root="."
        )
        _update_state("artist", "complete")
        _mirror_to_runs(self.state["run_id"])
        print("\n[WebBuilderFlow] Assets generated → frontend/public/")

    @listen(run_artist)
    def run_developer(self, _):
        if _already_done("developer"):
            return
        _update_state("developer", "running")

        run_developer_crew(
            tickets_path="workspace/current/tickets.md",
            context_path="workspace/context/project_context.md"
        )

        _update_state("developer", "complete")
        _mirror_to_runs(self.state["run_id"])

        # push branch and open PR — use branch from state (may have been renamed)
        state  = _load_state()
        branch = state.get("branch", f"feature/{self.state['run_id']}")
        _create_pull_request(
            run_id=self.state["run_id"],
            branch=branch,
            request=state["original_request"]
        )