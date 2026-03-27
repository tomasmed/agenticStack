from crewai.flow.flow import Flow, listen, start
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
import uuid, subprocess, json, os, re

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

# ─────────────────────────────────────────────────────────────
# Repo B path resolution
# ─────────────────────────────────────────────────────────────

def _get_target_repo() -> Path:
    """
    Resolve the target project repo (Repo B).
    TARGET_REPO_PATH must be set in .env — this engine is stateless
    and has no default project to operate on.
    """
    raw = os.getenv("TARGET_REPO_PATH", "").strip()
    if not raw:
        raise EnvironmentError(
            "TARGET_REPO_PATH is not set.\n"
            "Set it in .env to point at the target project repo (Repo B).\n"
            "Example: TARGET_REPO_PATH=/path/to/my-project"
        )
    path = Path(raw)
    if not path.exists():
        raise EnvironmentError(
            f"TARGET_REPO_PATH does not exist: {path}\n"
            "Check the path in .env."
        )
    return path


TARGET_REPO  = _get_target_repo()
WORKSPACE    = TARGET_REPO / "workspace"
RUNS_DIR     = WORKSPACE / "runs"
RUN_ID_FILE  = WORKSPACE / ".run_id"
RESUME_SAFE  = {"complete", "approved"}


# ─────────────────────────────────────────────────────────────
# Run directory helpers
# ─────────────────────────────────────────────────────────────

def _run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def _active_run_dir() -> Path:
    """Resolve the active run folder from .run_id pointer."""
    if not RUN_ID_FILE.exists():
        raise FileNotFoundError(
            "No active run found (workspace/.run_id missing).\n"
            "Start a new run with: python main.py build '<request>'"
        )
    run_id = RUN_ID_FILE.read_text().strip()
    run_dir = _run_dir(run_id)
    if not run_dir.exists():
        raise FileNotFoundError(
            f"Run directory missing for run_id '{run_id}': {run_dir}\n"
            "The .run_id pointer may be stale. Start a new run."
        )
    return run_dir


# ─────────────────────────────────────────────────────────────
# State helpers
# ─────────────────────────────────────────────────────────────

def _load_state() -> dict:
    state_path = _active_run_dir() / "pipeline_state.json"
    if not state_path.exists():
        raise FileNotFoundError(
            f"pipeline_state.json not found at {state_path}.\n"
            "Start a new run with: python main.py build '<request>'"
        )
    return json.loads(state_path.read_text())


def _update_state(stage: str, status: str):
    run_dir    = _active_run_dir()
    state_path = run_dir / "pipeline_state.json"
    try:
        state = json.loads(state_path.read_text())
        state["stages"][stage]["status"] = status
        if status in (*RESUME_SAFE, "awaiting_review", "failed"):
            state["stages"][stage]["completed_at"] = datetime.now(timezone.utc).isoformat()
        state_path.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"[WebBuilderFlow] ERROR updating state for stage '{stage}': {e}")


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


def _human_gate(stage: str, review_path: Path):
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


# ─────────────────────────────────────────────────────────────
# Feature name extraction + branch rename
# ─────────────────────────────────────────────────────────────

def _extract_feature_name(brief_path: Path) -> str | None:
    """
    Parse ## Feature Name section from brief.md.
    Returns sanitised kebab-case name or None if not found/invalid.
    """
    try:
        content = brief_path.read_text()
        match = re.search(
            r'##\s*Feature Name\s*\n+([^\n#]+)',
            content, re.IGNORECASE
        )
        if not match:
            return None
        name = match.group(1).strip()
        name = re.sub(r'[^a-z0-9\-]', '-', name.lower())
        name = re.sub(r'-+', '-', name).strip('-')
        words = [w for w in name.split('-') if w]
        if len(words) < 2 or len(words) > 6:
            return None
        return '-'.join(words)
    except Exception as e:
        print(f"[WebBuilderFlow] Could not extract feature name: {e}")
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
        capture_output=True, text=True,
        cwd=str(TARGET_REPO)
    )
    if result.returncode == 0:
        try:
            run_dir    = _active_run_dir()
            state_path = run_dir / "pipeline_state.json"
            state      = json.loads(state_path.read_text())
            state["branch"] = new_branch
            state_path.write_text(json.dumps(state, indent=2))
        except Exception as e:
            print(f"[WebBuilderFlow] Branch renamed but could not update state: {e}")
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
        capture_output=True, text=True,
        cwd=str(TARGET_REPO)
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
        capture_output=True, text=True,
        cwd=str(TARGET_REPO)
    )

    if pr.returncode != 0:
        print(f"[WebBuilderFlow] PR creation failed:\n{pr.stderr}")
        print(f"[WebBuilderFlow] Create manually: gh pr create --base main --head {branch}")
    else:
        print(f"\n[WebBuilderFlow] PR created: {pr.stdout.strip()}")


# ─────────────────────────────────────────────────────────────
# Run initialisation
# ─────────────────────────────────────────────────────────────

def _init_run(request: str) -> tuple[str, bool]:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # check for resumable run
    if RUN_ID_FILE.exists():
        try:
            run_id  = RUN_ID_FILE.read_text().strip()
            run_dir = _run_dir(run_id)
            if run_dir.exists():
                state_path = run_dir / "pipeline_state.json"
                if state_path.exists():
                    state    = json.loads(state_path.read_text())
                    all_done = all(
                        s.get("status") in RESUME_SAFE
                        for s in state["stages"].values()
                    )
                    if not all_done:
                        print(f"\n[WebBuilderFlow] Resuming run: {run_id}")
                        return run_id, True
        except Exception as e:
            print(f"[WebBuilderFlow] Could not resume previous run: {e}. Starting fresh.")

    # new run
    slug   = request[:24].replace(" ", "-").lower()
    date   = datetime.now().strftime("%Y%m%d")
    run_id = f"{slug}-{date}-{uuid.uuid4().hex[:4]}"

    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "generated").mkdir(parents=True, exist_ok=True)

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
    (run_dir / "pipeline_state.json").write_text(json.dumps(state, indent=2))
    RUN_ID_FILE.write_text(run_id)

    subprocess.run(["git", "config", "user.name",  os.getenv("AGENT_NAME",  "agent")],       check=False, cwd=str(TARGET_REPO))
    subprocess.run(["git", "config", "user.email", os.getenv("AGENT_EMAIL", "agent@local")], check=False, cwd=str(TARGET_REPO))
    subprocess.run(["git", "checkout", "-b", f"feature/{run_id}"],                            check=False, cwd=str(TARGET_REPO))

    print(f"\n[WebBuilderFlow] New run: {run_id}")
    print(f"[WebBuilderFlow] Run folder: {run_dir}")
    print(f"[WebBuilderFlow] Target repo: {TARGET_REPO}")
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

        run_dir = _run_dir(self.state["run_id"])
        out     = run_dir / "generated" / "app_state.md"

        try:
            result = build_app_summariser_crew(target_repo=TARGET_REPO).kickoff()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(result.raw)
            _update_state("summarise", "complete")
            print(f"[WebBuilderFlow] Written: {out}")
        except Exception as e:
            _update_state("summarise", "failed")
            print(f"[WebBuilderFlow] ERROR in summarise_app: {e}")
            raise

    @listen(summarise_app)
    def run_product_owner(self, _):
        if _already_done("product_owner"):
            return
        _update_state("product_owner", "running")

        run_dir      = _run_dir(self.state["run_id"])
        app_state    = run_dir / "generated" / "app_state.md"
        context_path = WORKSPACE / "context" / "project_context.md"
        brief_out    = run_dir / "brief.md"

        try:
            result = build_product_owner_crew(
                request=_load_state()["original_request"],
                context_path=str(context_path),
                app_state_path=str(app_state),
            ).kickoff()
            brief_out.write_text(result.raw)
        except Exception as e:
            _update_state("product_owner", "failed")
            print(f"[WebBuilderFlow] ERROR in run_product_owner: {e}")
            raise

        feature_name = _extract_feature_name(brief_out)
        if feature_name:
            state      = _load_state()
            old_branch = state.get("branch", f"feature/{self.state['run_id']}")
            new_branch = _rename_branch(old_branch, feature_name)
            self.state["branch"] = new_branch
        else:
            print("[WebBuilderFlow] No valid Feature Name found — keeping run-id branch")

        _human_gate("product_owner", brief_out)

    @listen(run_product_owner)
    def index_codebase(self, _):
        if _already_done("codebase_index"):
            return
        _update_state("codebase_index", "running")

        run_dir = _run_dir(self.state["run_id"])
        force   = self.state.get("force_reindex", False)

        try:
            run_codebase_reader(source_dir="frontend", target_repo=TARGET_REPO, run_dir=run_dir, force=force)
            run_codebase_reader(source_dir="backend",  target_repo=TARGET_REPO, run_dir=run_dir, force=force)
            _update_state("codebase_index", "complete")
            print(f"[WebBuilderFlow] Written: {run_dir / 'generated' / 'codebase_context.md'}")
        except Exception as e:
            _update_state("codebase_index", "failed")
            print(f"[WebBuilderFlow] ERROR in index_codebase: {e}")
            raise

    @listen(index_codebase)
    def run_team_lead(self, _):
        if _already_done("team_lead"):
            return
        _update_state("team_lead", "running")

        run_dir   = _run_dir(self.state["run_id"])
        brief     = run_dir / "brief.md"
        context   = WORKSPACE / "context" / "project_context.md"
        codebase  = run_dir / "generated" / "codebase_context.md"
        identity  = WORKSPACE / "context" / "visual_identity.md"
        tickets_out  = run_dir / "tickets.md"
        manifest_out = run_dir / "asset_manifest.json"

        try:
            result = build_team_lead_crew(
                brief_path=str(brief),
                context_path=str(context),
                codebase_index_path=str(codebase),
                visual_identity_path=str(identity),
            ).kickoff()
        except Exception as e:
            _update_state("team_lead", "failed")
            print(f"[WebBuilderFlow] ERROR in run_team_lead: {e}")
            raise

        raw = result.raw

        # normalise header variations — models use # or ## inconsistently
        # also handle ## ANALYSIS preamble before ## TICKETS
        normalised = re.sub(r'#{1,3}\s*ASSET_MANIFEST', '## ASSET_MANIFEST', raw)
        normalised = re.sub(r'#{1,3}\s*TICKETS',        '## TICKETS',        normalised)

        if "## TICKETS" in normalised and "## ASSET_MANIFEST" in normalised:
            parts            = normalised.split("## ASSET_MANIFEST")
            tickets_raw      = parts[0]
            manifest_section = parts[1].strip()
            # strip any ## ANALYSIS preamble before ## TICKETS
            tickets_section  = re.split(r'## TICKETS', tickets_raw, maxsplit=1)[-1].strip()
        else:
            tickets_section  = raw
            manifest_section = ""
            print("[WebBuilderFlow] WARN — TeamLead output missing section markers. Tickets written, manifest empty.")

        tickets_out.write_text(tickets_section)

        # strip code fences — models often wrap JSON in ```json ... ```
        manifest_clean = re.sub(r'```(?:json)?\s*', '', manifest_section).strip()

        start = manifest_clean.find("{")
        end   = manifest_clean.rfind("}")
        if start != -1 and end != -1:
            try:
                manifest_data = json.loads(manifest_clean[start:end + 1])
                if "status" not in manifest_data:
                    manifest_data["status"] = "draft"
                manifest_out.write_text(json.dumps(manifest_data, indent=2))
                asset_count = len(manifest_data.get("assets", []))
                print(f"[WebBuilderFlow] Asset manifest written — {asset_count} asset(s)")
            except json.JSONDecodeError as e:
                print(f"[WebBuilderFlow] WARN — could not parse asset manifest JSON: {e}. Writing empty manifest.")
                print(f"[WebBuilderFlow] Raw manifest section saved to tickets.md for inspection.")
                manifest_out.write_text('{"status": "draft", "assets": []}')
        else:
            manifest_out.write_text('{"status": "draft", "assets": []}')

        _human_gate("team_lead", tickets_out)

    @listen(run_team_lead)
    def run_art_director(self, _):
        if _already_done("art_director"):
            return

        run_dir      = _run_dir(self.state["run_id"])
        manifest_out = run_dir / "asset_manifest.json"
        brief        = run_dir / "brief.md"
        identity     = WORKSPACE / "context" / "visual_identity.md"

        try:
            data = json.loads(manifest_out.read_text())
        except Exception as e:
            print(f"[WebBuilderFlow] ERROR reading asset manifest: {e}")
            _update_state("art_director", "failed")
            raise

        if not data.get("assets"):
            print("\n[WebBuilderFlow] No assets in manifest — skipping ArtDirector")
            _update_state("art_director", "approved")
            return

        _update_state("art_director", "running")

        try:
            run_art_director(
                manifest_path=str(manifest_out),
                brief_path=str(brief),
                visual_identity_path=str(identity),
                output_path=str(manifest_out),
            )
        except Exception as e:
            _update_state("art_director", "failed")
            print(f"[WebBuilderFlow] ERROR in run_art_director: {e}")
            raise

        print(f"\n[WebBuilderFlow] Written: {manifest_out} (enriched)")
        print(">>> HUMAN GATE — review generation blocks in asset_manifest.json")
        print(">>> Set \"status\": \"approved\" in the manifest, then press Enter: ", end="")
        input()
        _update_state("art_director", "approved")

    @listen(run_art_director)
    def run_artist(self, _):
        if _already_done("artist"):
            return

        run_dir      = _run_dir(self.state["run_id"])
        manifest_out = run_dir / "asset_manifest.json"

        try:
            manifest_data = json.loads(manifest_out.read_text())
        except Exception as e:
            print(f"[WebBuilderFlow] ERROR reading asset manifest: {e}")
            _update_state("artist", "failed")
            raise

        if not manifest_data.get("assets"):
            print("\n[WebBuilderFlow] No assets — skipping Artist")
            _update_state("artist", "complete")
            return

        _update_state("artist", "running")
        try:
            run_artist(
                manifest_path=str(manifest_out),
                project_root=str(TARGET_REPO)
            )
            _update_state("artist", "complete")
            print(f"\n[WebBuilderFlow] Assets generated → {TARGET_REPO / 'frontend' / 'public'}")
        except Exception as e:
            _update_state("artist", "failed")
            print(f"[WebBuilderFlow] ERROR in run_artist: {e}")
            raise

    @listen(run_artist)
    def run_developer(self, _):
        if _already_done("developer"):
            return
        _update_state("developer", "running")

        run_dir  = _run_dir(self.state["run_id"])
        tickets  = run_dir / "tickets.md"
        context  = WORKSPACE / "context" / "project_context.md"

        try:
            run_developer_crew(
                tickets_path=str(tickets),
                context_path=str(context),
                target_repo=str(TARGET_REPO),
            )
            _update_state("developer", "complete")
        except Exception as e:
            _update_state("developer", "failed")
            print(f"[WebBuilderFlow] ERROR in run_developer: {e}")
            raise

        state  = _load_state()
        branch = state.get("branch", f"feature/{self.state['run_id']}")
        _create_pull_request(
            run_id=self.state["run_id"],
            branch=branch,
            request=state["original_request"]
        )