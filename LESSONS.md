# Lessons Earned

Institutional memory for this stack. These are things that were learned the hard way — each one cost real debugging time or a broken run. Feed this file to any AI assistant working on this repo alongside the README.

Implementation-specific lessons (aider quirks, framework-specific gotchas in Repo B) live in that repo's own lessons file.

---

## Architecture

**Planning blind to codebase = structural drift**
A planning agent writing tickets without codebase context creates new structure that displaces working components. Fix: always run the code intelligence pass before the planning agent, every run.

**The gap analysis belongs to the frontier model**
Enrichment = facts and structure. Reasoning = frontier. Don't pre-digest reasoning work with a local model — it adds noise, not signal. CodebaseReader produces facts. TeamLead produces judgement.

**Enrichment beats retry**
Investing in context quality upstream produces better first-pass output than retrying a poorly-contextualised call. This is the primary philosophical difference from most agentic frameworks.

**Manifests are job descriptions, not project briefs**
Baking stack conventions or project context into a manifest means it breaks every time you change project or stack. Manifests describe the agent. `workspace/context/` describes the project.

**Execution layers need no LLM wrapper**
If an agent's only job is to call one tool, it's not an agent — it's a function. Adding an LLM wrapper doubles the model hops with no quality benefit. Call the tool directly from the flow.

---

## File and State Management

**Agents generate, flows write**
The path-hallucination bug: an agent given WriteFileTool writes wherever the LLM decided to write. Fix: no WriteFileTool on agents. The flow calls `result.raw` and writes to a locked path. Deterministic, auditable, no surprises.

**TeamLead output needs structured markers for multi-artifact parsing**
When one LLM call must produce two files (e.g. tickets + asset manifest), use explicit section markers the flow can split on — `## TICKETS` / `## ASSET_MANIFEST`. Robust to model variation in output formatting.

**Agents that receive empty inputs will hallucinate content**
If a manifest or asset list is empty, local models will invent plausible-sounding content rather than returning nothing. Guard every conditional stage: if the input set is empty, skip the stage entirely rather than passing empty input to the agent.

---

## Infrastructure

**Dependency isolation for subprocess tools is non-negotiable**
Tools invoked as subprocesses need their own virtual environment. Shared venvs cause dependency conflicts that are painful to debug and non-obvious to diagnose. The subprocess boundary is the isolation layer.

**Symlinks are fragile on Windows/WSL**
`workspace/current/` should be a real folder (or a `.run_id` pointer file), not a symlink. Symlinks silently fail or misbehave on Windows/WSL in ways that are hard to debug.

**Shell env vars bleed into dotenv**
Never put project config in `~/.bashrc`. `python-dotenv` without `override=True` will not overwrite existing shell env vars. Use:
```python
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)
```

**LiteLLM adds significant startup latency**
Use lazy imports — import flows inside the handler block, not at module top. Also set:
```bash
LITELLM_LOCAL_MODEL_COST_MAP=true
LITELLM_TELEMETRY=false
```
Cuts startup from ~20s to ~3-5s.

---

## Cost

**Frontier-at-chokepoints is cheap in practice**
The first complete pipeline run cost ~$0.01. One frontier model call for the planning agent (gap analysis + ticket writing). All other agents ran on local models at $0.00. This is what the mixed model strategy looks like in practice.