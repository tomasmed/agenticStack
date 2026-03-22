# agenticScheduler — Task List

## In Progress

- [x] **Repo split: stack vs workpiece**
  Separate the engine (flows, crews, tools, manifests) into its own repo (Repo A) from the target project being built (Repo B). Migration to Repo A complete. Remaining work: wire up `TARGET_REPO_PATH` / `TARGET_REPO_URL` env vars so the engine points at Repo B at runtime.

## Planned

- [ ] **Workspace redesign**
  Implement the new `workspace/` structure in Repo B. Remove `approved/` — approval state lives in `pipeline_state.json`. Remove top-level `generated/` — enrichment is now scoped per run under `runs/{run-id}/generated/`. Replace `current/` folder with a `.run_id` pointer file. Update all flow path references accordingly.

- [ ] **Move `context/` to Repo B**
  `workspace/context/` (visual identity, project context, domain bias) belongs in the target project repo, not the engine. Update the flow to read context from `TARGET_REPO_PATH/workspace/context/`.