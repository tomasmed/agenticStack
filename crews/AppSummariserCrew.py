from crewai import Agent, Crew, Task, Process, LLM
from pathlib import Path
import os


def build_app_summariser_crew(target_repo: Path) -> Crew:
    llm = LLM(
        model=f"ollama/{os.getenv('APP_SUMMARISER_MODEL', '').strip()}",
        base_url=os.getenv("OLLAMA_HOST", "").strip()
    )
    manifest = Path("manifests/AppSummariser.md").read_text()

    frontend = target_repo / "frontend"
    if not frontend.exists():
        file_list = "No frontend directory found."
        print(f"[AppSummariserCrew] WARN — frontend directory not found at {frontend}")
    else:
        file_list = "\n".join(
            str(p.relative_to(target_repo)) for p in sorted(frontend.rglob("*"))
            if p.is_file()
            and not any(s in p.parts for s in ("node_modules", ".next", "dist"))
            and p.suffix in (".tsx", ".ts", ".jsx", ".js", ".css")
        ) or "Frontend directory exists but contains no source files."

    agent = Agent(
        role="App Summariser",
        goal="Produce a plain English summary of the current project state",
        backstory=manifest,
        llm=llm,
        tools=[],
        verbose=True
    )

    task = Task(
        description=f"""
Summarise the current state of the project.

FILES FOUND IN FRONTEND:
{file_list}

Produce a plain English markdown summary with these sections:

## Existing components
Name, location, what it likely renders based on filename.
If none exist yet, say so explicitly.

## Existing pages
Route, filename. If none exist yet, say so explicitly.

## Existing API routes
Path and filename. If none exist yet, say so explicitly.

## Global styles
What globals.css contains if it exists.

## What is not yet built
Gaps relative to a full scheduling UI with calendar and chat window.

Be factual. Do not infer intent beyond what filenames suggest.
Note that this is being read from the main branch.
        """,
        expected_output="A plain English markdown summary of the current app state",
        agent=agent
    )

    return Crew(agents=[agent], tasks=[task], process=Process.sequential)