from crewai import Agent, Crew, Task, Process, LLM
from pathlib import Path
import os


def build_product_owner_crew(
    request: str,
    context_path: str,
    app_state_path: str,
) -> Crew:
    llm = LLM(
        model=f"ollama/{os.getenv('PO_MODEL', '').strip()}",
        base_url=os.getenv("OLLAMA_HOST", "").strip()
    )

    # manifest = who the agent is
    manifest = Path("manifests/ProductOwner.md").read_text()

    # dynamic context = what the agent knows for this run
    project_context = Path(context_path).read_text() if Path(context_path).exists() else ""
    app_state = Path(app_state_path).read_text() if Path(app_state_path).exists() else "No app state yet."

    agent = Agent(
        role="Product Owner",
        goal="Convert a business request into a structured product brief",
        backstory=manifest,
        llm=llm,
        tools=[],
        verbose=True
    )

    task = Task(
        description=f"""
PROJECT CONTEXT (stack, conventions, rules):
{project_context}

CURRENT APP STATE (what already exists):
{app_state}

BUSINESS REQUEST:
{request}

Write a complete product brief in markdown.
Include all required sections from your manifest in this exact order:

## Feature Name
[3-5 word kebab-case name — this becomes the git branch name]

## Feature Summary
[plain English, two paragraphs max]

## User Stories
[operator-focused, present tense, max 5]

## Design Intent
[material references and sensory language only — no colour names or CSS]

## Acceptance Criteria
[specific, testable, max 6]

## What Must Be Preserved
[existing structure not to break]

## Out of Scope
[explicit list]

Rules:
- Feature Name must be kebab-case, 3-5 words, noun phrase only
- Do not specify technology, file names, or implementation details
- Design Intent uses material references, not colour names or CSS
- Return the brief as markdown text only — no preamble, no explanation
        """,
        expected_output="A complete product brief in markdown starting with ## Feature Name",
        agent=agent
    )

    return Crew(agents=[agent], tasks=[task], process=Process.sequential)