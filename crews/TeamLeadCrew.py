from crewai import Agent, Crew, Task, Process, LLM
from pathlib import Path
import os


def build_team_lead_crew(
    brief_path: str,
    context_path: str,
    codebase_index_path: str,
    visual_identity_path: str = "workspace/context/visual_identity.md",
) -> Crew:

    # frontier justified — gap analysis cascades into everything downstream
    llm = LLM(
        model=os.getenv("FRONTIER_MODEL", "claude-haiku-4-5-20250514"),
        api_key=os.getenv("ANTHROPIC_API_KEY")
    )

    # manifest = who the agent is
    manifest = Path("manifests/TeamLead.md").read_text()

    # dynamic context = what the agent knows for this run
    brief          = Path(brief_path).read_text()
    project_context = Path(context_path).read_text()
    visual_identity = (
        Path(visual_identity_path).read_text()
        if Path(visual_identity_path).exists()
        else ""
    )
    codebase_index = (
        Path(codebase_index_path).read_text()
        if Path(codebase_index_path).exists()
        else "No codebase index available. Greenfield — plan for full construction from scratch."
    )

    agent = Agent(
        role="Tech Lead",
        goal="Produce scoped, file-aware development tickets and an asset manifest",
        backstory=manifest,
        llm=llm,
        tools=[],
        verbose=True
    )

    task = Task(
        description=f"""
You have been handed four context files for this run.

PROJECT CONTEXT (stack, folder conventions, hard rules):
{project_context}

VISUAL IDENTITY (exact Tailwind classes, color system, copy rules):
{visual_identity}

CODEBASE INDEX (what currently exists):
{codebase_index}

PRODUCT BRIEF (what needs to exist after this build):
{brief}

Your task:

1. Determine the gap — what does the brief require that does not exist yet?
   What exists but needs modification? What must not be touched?

2. Produce development tickets following your ticket schema exactly.
   Sequence: scaffolding → layout → components → API routes.
   Each ticket must be independently executable.
   Do not write tickets for things that already exist unchanged.

3. CRITICAL — copy and color in tickets:
   - If the brief contains any UI copy (headlines, subtext, placeholders,
     button labels) include that exact text verbatim in the ticket description.
     Never write "add a headline" — write the actual headline string.
   - If the visual identity specifies Tailwind classes, use those exact class
     names.
   - Interactive elements must use the accent color from visual identity.

4. Produce a structural asset manifest for any image/icon assets needed, dimensions must be specified.

Return your response in this exact format — two clearly marked sections:

## TICKETS
[all tickets here following the ticket schema]

## ASSET_MANIFEST
[valid JSON asset manifest here]
        """,
        expected_output="A TICKETS section and an ASSET_MANIFEST section",
        agent=agent
    )

    return Crew(agents=[agent], tasks=[task], process=Process.sequential)