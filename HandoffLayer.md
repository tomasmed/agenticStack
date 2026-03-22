# Handoff Layer
## Shared Workspace Reference
*The file-based shared memory between agents and humans.*
*Agents write. Humans review, approve, and push forward.*

---

## What This Is

The handoff layer is the structured workspace that all agents read from
and write to. It is not a database. It is not in-memory state. It is a
folder of human-readable files that serves as the shared memory of the
entire agentic stack.

It draws similarity to how a real team uses a shared drive or wiki —
but with a strict zone structure that makes it clear who wrote what,
when, and for what purpose.

Git tracks everything. Every agent write is reviewable. Every human
approval is an explicit action, not a side effect.

---

## Zone Structure

```
workspace/
  │
  ├── context/                  ← HUMAN ZONE — humans write, agents read only
  │     project_context.md      ← stack, conventions, hard rules per project
  │     operator_profile.md     ← who the end user is, constraints, preferences
  │     [any other static ctx]  ← add as needed per project
  │
  ├── generated/                ← AGENT ZONE — agents write, humans review
  │     app_state.md            ← AppSummariser output: current UI state
  │     codebase_index.md       ← CodebaseReader output: semantic code index
  │
  ├── runs/                     ← HISTORY — one folder per run, never deleted
  │     {run-id}/
  │       brief.md              ← ProductOwner output
  │       tickets.md            ← TeamLead output
  │       asset_manifest.json   ← TeamLead + ArtDirector output
  │       pipeline_state.json   ← run metadata, status, timestamps
  │
  ├── current/                  ← ACTIVE RUN — symlink to runs/{run-id}/
  │     brief.md                ← → runs/{run-id}/brief.md
  │     tickets.md              ← → runs/{run-id}/tickets.md
  │     asset_manifest.json     ← → runs/{run-id}/asset_manifest.json
  │     pipeline_state.json     ← → runs/{run-id}/pipeline_state.json
  │
  └── approved/                 ← APPROVAL ZONE — human-gated writes only
        schedule_proposals/
          latest.md             ← proposed schedule change, pending approval
          {timestamp}.md        ← approved proposals, kept as history
        assets/
          approved_manifest.json ← ArtDirector manifest after human approval
```

---

## Zone Rules

### context/ — Human zone
- Humans write these files. Agents never write here.
- These are the "standing orders" — stable across runs.
- Examples: stack conventions, operator profile, project constraints.
- An agent receiving a task gets the relevant context/ file injected
  into their task description, not their manifest.

### generated/ — Agent zone
- Agents write here after enrichment passes (AppSummariser, CodebaseReader).
- Humans review but do not edit — if wrong, rerun the agent.
- Always regenerated fresh — never cached between runs unless explicitly
  flagged as stable.
- Git tracks changes so humans can diff what the agent understood vs
  what the codebase actually contains.

### runs/{run-id}/ — History zone
- One folder per pipeline run, named by run ID.
- Agents write artifacts here directly.
- Never deleted. Git tracks every run.
- run-id format: {slug}-{YYYYMMDD}-{4hex}

### current/ — Active run zone
- Symlink to runs/{run-id}/ for the active run.
- Agents always read/write via current/ — never hardcode a run-id path.
- On new run: orchestrator updates the symlink before any agent runs.
- Humans review files here — they are reading the active run's artifacts.

### approved/ — Approval zone
- Nothing lands here without human action.
- Agent proposes to current/ or a staging path.
- Human reviews, then explicitly moves or copies to approved/.
- This is the commit point — writing here means "human said yes."
- For scheduling: proposals land in current/, operator approves,
  orchestrator copies to approved/ and triggers db write.

---

## What Each Agent Reads and Writes

| Agent           | Reads                                      | Writes                          |
|-----------------|--------------------------------------------|---------------------------------|
| AppSummariser   | app/components/, app/pages/                | generated/app_state.md          |
| CodebaseReader  | frontend/, backend/ source                 | generated/codebase_index.md     |
| ProductOwner    | context/project_context.md                 | current/brief.md                |
|                 | generated/app_state.md                     |                                 |
|                 | business request (via task)                |                                 |
| TeamLead        | context/project_context.md                 | current/tickets.md              |
|                 | current/brief.md                           | current/asset_manifest.json     |
|                 | generated/codebase_index.md (when ready)   |                                 |
| ArtDirector     | current/asset_manifest.json                | current/asset_manifest.json     |
|                 | current/brief.md                           | (enriches in place)             |
| Developer       | context/project_context.md                 | source files (via aider)        |
|                 | current/tickets.md                         | git commits                     |
| Scheduler       | context/operator_profile.md                | current/schedule_proposal.md    |
|                 | approved/schedule_proposals/ (history)     |                                 |
| FrontDesk       | current/schedule_proposal.md               | triggers orchestrator           |
|                 | context/operator_profile.md                |                                 |

---

## pipeline_state.json schema

Written by orchestrator at run start, updated at each stage.

```json
{
  "run_id": "scheduling-ui-20260320-a3f2",
  "original_request": "build a scheduling web app with calendar and chat",
  "branch": "feature/scheduling-ui-20260320-a3f2",
  "started_at": "2026-03-20T14:32:00Z",
  "stages": {
    "summarise":      { "status": "complete", "completed_at": "..." },
    "product_owner":  { "status": "complete", "completed_at": "..." },
    "team_lead":      { "status": "awaiting_review", "completed_at": "..." },
    "art_director":   { "status": "pending" },
    "developer":      { "status": "pending" }
  }
}
```

Status values: pending | running | complete | awaiting_review | approved | failed

---

## How the Human Gate Works

Every human gate in the flow maps to a status transition:

```
agent writes artifact
        ↓
status → "awaiting_review"
        ↓
human reads file in current/
        ↓
human presses Enter (CLI) or clicks Approve (UI later)
        ↓
status → "approved"
        ↓
next stage triggers
```

The approval action is always explicit. No implicit "if no one stopped it,
it was approved." The orchestrator does not proceed until status is
"approved" for the preceding stage.

---

## .gitignore rules

```gitignore
# Keep structure, ignore generated content
workspace/runs/*/           # run history gitignored locally
                            # or committed — operator decides per project
workspace/current           # symlink — gitignored, recreated on run
workspace/generated/        # always regenerated — optionally gitignored

# Never gitignore:
workspace/context/          # human-written, always tracked
workspace/approved/         # approval history, always tracked
workspace/manifests/        # agent identities, always tracked
```

---

## Why not a database?

The handoff layer is files, not a database, for four reasons:

1. **Human reviewable without tooling** — open any file in any editor,
   read exactly what the agent produced, edit if needed.
2. **Git native** — every agent write is a diffable change. You can see
   exactly what changed between runs, what the agent understood, and
   what a human approved.
3. **Debuggable** — when something goes wrong, the file is there. No
   query needed, no ORM, no migration.
4. **Agent friendly** — agents read and write files naturally. Passing
   a file path is simpler and more auditable than an API call to a
   local database.

A database becomes appropriate when the handoff layer needs to serve
a web UI (the scheduling proposal view, the operator approval screen).
At that point, the approved/ zone gets a thin API layer on top — but
the files remain the source of truth that the API reads from.