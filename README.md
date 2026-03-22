# Agentic Stack Playbook
## Portable Architecture Reference — v4
*This README also serves as context for AI assistants working on this repo.*

---

## What This Is

A set of transferable principles for building agentic systems — derived from building a multi-agent pipeline from scratch and validated against production frameworks (CrewAI, LangGraph). These are opinionated decisions that determine how you make choices when building agentic systems. The implementations change. The principles survive.

Implementation-agnostic: a CrewAI Flow, a custom Python orchestrator, a LangGraph DAG — all are valid substrates. What makes a system compliant is not the tooling, it is the decisions.

---

## Repo Responsibilities

This repo is the **engine**. It contains the flows, crews, tools, and manifests that build and operate on a target project. It is stateless between runs — all run state lives in the target project repo.

The **target project** is what the engine operates on. It contains the application code, the workspace handoff layer, and the project-specific context that shapes how agents think about that engagement. Each **target project**  should maintain its own README describing the specific flow pattern it uses and the conventions the engine should follow when working on it.

The engine points at a target project via two environment variables:

```bash
TARGET_REPO_PATH=/path/to/repo-b      # local checkout, used for file operations
TARGET_REPO_URL=git@github.com/...    # remote, used for git operations
```

---

## The Core Mental Model

Three separable layers. Keep them separate always.

```
┌─────────────────────────────────────────────────────┐
│  INTERFACE LAYER                                    │
│  How humans and systems interact with agents        │
│  Examples: CLI, chatbot window, API endpoint        │
│  Owns: intent interpretation, trigger logic         │
└─────────────────────────┬───────────────────────────┘
                          │ triggers
┌─────────────────────────▼───────────────────────────┐
│  COMPUTATION LAYER   -- Includes Working Memory     │
│  Where agents do bounded, scoped work               │
│  Examples: CrewAI Flows, agentic loops              │
│  Owns: task execution, tool use, proposals          │
└─────────────────────────┬───────────────────────────┘
                          │ reads / proposes writes
┌─────────────────────────▼───────────────────────────┐
│  STATE LAYER                                        │
│  Ground truth. Never written without human approval │
│  Examples: workspace/, git, database                │
│  Owns: history, artifacts, live data, run state     │
└─────────────────────────────────────────────────────┘
```

**The violation to avoid most:** computation layer writing directly to state layer without a human gate. Agents propose. Humans commit.

---

## workspace/ — Working Memory and State

`workspace/` lives **target project** , not the engine. It serves two distinct purposes and should be read accordingly.

**Working Memory** is the engine's hydration layer. On startup the engine reads this to reconstruct what it knows about the project and where a run is up to. It is mutable and intermediate — it exists to serve the engine between stages, not as a record of truth.

**Immutable State** is what has passed a human gate. Once a stage is marked `complete` in `pipeline_state.json`, its artifacts are ground truth and should not be modified. This is the State Layer described in the core mental model.

```
workspace/
  context/                    ← IMMUTABLE STATE: human-written ground truth
                                 project_context.md
                                 visual_identity.md
                                 Stable across runs. Agents read only.

  runs/
    {run-id}/
      pipeline_state.json     ← WORKING MEMORY until a stage is complete,
                                 IMMUTABLE STATE once that stage is marked complete
      brief.md
      tickets.md
      asset_manifest.json
      generated/              ← WORKING MEMORY: agent enrichment, scoped to this run
        app_state.md
        codebase_context.md

  .run_id                     ← WORKING MEMORY: pointer to active run folder
```

**Ownership rules:**
- `context/` is written by humans, read by agents. Never modified by the engine.
- `runs/{run-id}/` is created by the engine at initialisation. One folder per run.
- `runs/{run-id}/generated/` is agent-written enrichment. Derived and reproducible — scoped to the run that produced it, not the project globally.
- `pipeline_state.json` is the single source of truth for run progress and approval state. Stages move through: `pending → running → awaiting_review → approved → complete | failed`. Human approval is recorded here.
- `.run_id` contains the active run identifier. The engine reads this on startup to resume a run in progress.

The distinction matters at resume time: the engine trusts `complete` stages and skips them. Working memory is fair game to rewrite. Immutable state is not.

---

## Core Principles

**1. Agents have bounded context — by design, not by accident**
Each agent has a manifest defining exactly what it knows. Narrow context produces better role fidelity. This is a correctness mechanism, not a constraint.

**2. Manifests describe who the agent is. Files describe what it knows.**
A manifest is a job description — role, personality, working style, hard constraints. It never contains project-specific context. That arrives at runtime via files in `workspace/context/`. Manifests are stable across runs and projects. Context files are not.

```
manifests/TeamLead.md              → "I sequence tickets by dependency order"
workspace/context/project_context.md  → "this project uses Next.js App Router"
runs/{run-id}/brief.md             → "build a calendar and chat window"
runs/{run-id}/generated/
  codebase_context.md              → "CalendarView exists, ChatWindow does not"
```

**3. Roles mirror human team structure**
Name agents after their human equivalent. If you cannot name the human equivalent, the agent scope is probably wrong.

**4. Push model, not pull**
The orchestrator pushes inputs after human review. Agents do not decide when to run. Humans push work forward deliberately.

**5. Human gates are first-class architecture**
Every transition between computation and state requires a human gate. The question is not "should I add a gate?" but "what does the human review at this gate?"

**6. Proposals, not writes**
Agents produce proposed artifacts. Humans approve. State is updated only after approval. The gate artifact must be readable enough that the human genuinely understands what they are approving.

**7. File-based handoffs reduce communication surface deliberately**
Agents communicate through files, not free-form messages. Narrow communication surface produces auditable, debuggable systems.

**8. Mixed model strategy — local first, frontier at chokepoints**
Where quality cascades downstream, use frontier. Where the task is well-scoped prose or pattern matching, local is sufficient. Never use frontier by default. Never use local out of ideology.

**9. Tools are first-class agent capability**
Tool selection per agent is an architectural decision. Agents carry the tools appropriate to their role.

**10. Enrichment before expensive calls — throughput over retry loops**
Invest upstream in making context so good that the frontier model gets it right on the first pass. Pre-processing passes that compress signal before the expensive call are cheaper and more predictable than retry loops.

This is the primary philosophical difference from most agentic frameworks, which treat iteration as the quality mechanism. This playbook treats enrichment as the quality mechanism.

**11. Deterministic steps belong in code, not prompts**
Business logic that must always execute belongs in code. File routing, git commits, subprocess calls — these are deterministic. They belong in the flow, not in agent task descriptions.

**12. Feedback loops close the execution gap**
A pipeline without a feedback loop assumes first output is acceptable. Add validation steps with a maximum retry count and human escalation.

**13. Agent infrastructure is excluded from agent scope by default**
Agents should not index or modify their own infrastructure unless explicitly enabled. `.crewignore` controls this boundary. Self-improvement is deliberate opt-in.

**14. Agents generate content. The orchestrator writes files.**
LLM agents return text via `result.raw`. The orchestrating flow writes that text to the correct path. This is deterministic, auditable, and removes a whole class of path-hallucination bugs.

```
WRONG:  Agent reasons → calls WriteFileTool("/some/path") → writes
RIGHT:  Agent reasons → returns result.raw → flow writes to locked path
```

**15. Execution agents need no LLM wrapper**
Agents whose only job is to call one tool are not agents — they are functions. Wrapping them in an LLM adds a second model hop with no quality benefit. Call the tool directly from the flow.

**16. Dependency isolation for subprocess tools**
Tools invoked as subprocesses should have their own virtual environment. Shared venvs cause dependency conflicts that are painful to debug. The subprocess boundary is the isolation layer.

---

## Agent Interface Contract

Any implementation fills an agent slot if it respects this contract:

```
INPUT:   manifest (who) + context files (what, this run)
PROCESS: bounded task, defined tools
OUTPUT:  result.raw — text content returned to flow
WRITE:   flow writes result.raw to the correct workspace/ path
GATE:    human reviews before next stage proceeds
```

---

## Code Intelligence (context_sidecars/)

`context_sidecars/` is a per-file documentation layer that lives in Repo B, mirroring the source tree. It serves as both human-readable documentation and an agentic hydration layer — giving agents a structured, pre-digested understanding of the codebase without reading raw source files directly.

The pattern uses a three-pass architecture:

```
Pass 1 — Deterministic extraction (no LLM)
  AST or static analysis per changed file.
  Also processes files with no sidecar yet, regardless of git state.

Pass 2 — Local LLM (one call per file needing update)
  Receives structured extraction output only — never raw source.
  Writes human+LLM readable .md sidecar.
  Agent generates text, flow writes file (principle 14).

Pass 3 — Aggregation (one local LLM call, fixed cost)
  Reads all sidecars → single codebase_context.md for planning agents.
  Cost is constant regardless of codebase size.
```

**The gap analysis stays with the frontier planning agent.**
The sidecar layer produces facts. The planning agent produces judgement. Pre-digesting reasoning work with a local model adds noise, not signal.

---

## Flow Patterns

### Linear Pipeline
```
[Enrichment] → [Planning / Gate] → [Execution] → [PR / Gate]
```
Best for: building or modifying a codebase from a brief. One run produces one coherent set of changes.

### Reactive Event Loop
```
[Interface — always on]
        ↓ intent detected
[Computation Flow]
        ↓ proposal → workspace/runs/{run-id}/
[Human Gate]
        ↓ approved
[State write]
```
Best for: scheduling, assistant, or chat-driven flows where the trigger is unpredictable.

### Nested Sub-Stack
```
[Main Flow]
    └── [Agent Slot]
            └── [Sub-Flow: enrichment → retrieval → synthesis]
```
Best for: any agent whose work is complex enough to warrant its own internal pipeline.

### Reflection Loop
```
[Agent] → result.raw → [Validator — deterministic]
              ↓ pass              ↓ fail (max N)
         [flow writes]       [retry with fix]
                                  ↓ N failures
                             [human escalation]
```
Best for: any stage where output quality can be checked programmatically before human review.

---

## Model Selection Heuristic

1. Does quality cascade downstream into everything?
   → Yes: frontier candidate → No: question 2

2. Is this well-scoped prose, pattern matching, or summarisation?
   → Yes: local sufficient → No: question 3

3. Does this require reliable tool or function calling?
   → Local models often fail silently on tool invocation
   → If uncertain: use frontier
   → Better: remove tools from agent, call deterministically in flow

4. Is this execution, not reasoning?
   → Yes: no LLM at all — pure subprocess or API call

---

## Framework Mapping

| Playbook concept      | CrewAI          | LangGraph        | Custom Python    |
|-----------------------|-----------------|------------------|------------------|
| Orchestrator          | Flow            | StateGraph       | orchestrator.py  |
| Agent (reasoning)     | Crew            | Node             | agent class      |
| Agent (execution)     | not needed*     | not needed*      | function         |
| Manifest              | role/backstory  | node metadata    | .md file         |
| Context file          | task description| state input      | injected string  |
| result.raw write      | flow code       | flow code        | flow code        |
| Human gate            | @router         | interrupt()      | input() / API    |
| Mid-run resume        | not native*     | checkpointer     | pipeline_state   |
| Handoff layer         | not native*     | not native*      | workspace/       |
| Dependency isolation  | not native*     | not native*      | separate venv    |

*implemented on top of framework via playbook patterns

---

## What This Playbook Is Not

- Not a replacement for aider, Claude Code, or any specific tool
- Not CrewAI, LangGraph, or AutoGen — it runs on top of any of them
- Not autonomous — humans push each stage deliberately
- Not faster than Claude Code for simple tasks — it is structured for quality, auditability, and control
- Not finished — it evolves with each pipeline built