"""
CodebaseReader — codebase intelligence with per-file sidecars

Three passes:

Pass 1 — Tree-sitter (no LLM, deterministic)
  Extracts structure from each changed file:
  exports, hooks, renders, imports, API methods

Pass 2 — Local LLM (one call per changed file)
  Reads tree-sitter output — never raw source code
  Writes a human+LLM readable .md sidecar per file

Pass 3 — Aggregation (one local LLM call)
  Reads all sidecars
  Writes runs/{run-id}/generated/codebase_context.md
  This is what TeamLead receives

Incremental: only processes files changed since last commit.
Manual override: pass force=True to reprocess everything.

Sidecar location: context_sidecars/ in Repo B, mirroring source tree
  frontend/components/CalendarView/CalendarView.jsx
  → context_sidecars/frontend/components/CalendarView/CalendarView.jsx.md
"""

from pathlib import Path
from crewai import Agent, Crew, Task, Process, LLM
from datetime import datetime
import subprocess, os

try:
    import tree_sitter_javascript as tsjs
    import tree_sitter_typescript as tsts
    from tree_sitter import Language, Parser
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

try:
    import tree_sitter_python as tspy
    PYTHON_AVAILABLE = True
except ImportError:
    PYTHON_AVAILABLE = False

SKIP_DIRS   = {"node_modules", ".next", "dist", "__pycache__", ".git"}
SOURCE_EXTS = {".jsx", ".tsx", ".js", ".ts", ".py"}


# ─────────────────────────────────────────────────────────────
# .crewignore parser — reads from Repo A (engine), not Repo B
# ─────────────────────────────────────────────────────────────

def _load_ignore_patterns() -> list[str]:
    crewignore = Path(".crewignore")
    if not crewignore.exists():
        return []
    return [
        line.strip() for line in crewignore.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _is_ignored(path: Path, patterns: list[str]) -> bool:
    import fnmatch
    for pattern in patterns:
        clean = pattern.rstrip("/")
        if any(fnmatch.fnmatch(part, clean) for part in path.parts):
            return True
        if fnmatch.fnmatch(str(path), clean) or fnmatch.fnmatch(path.name, clean):
            return True
    return False


# ─────────────────────────────────────────────────────────────
# Git helpers — all git ops run in Repo B
# ─────────────────────────────────────────────────────────────

def _git_changed(source_dir: str, target_repo: Path) -> set[str] | None:
    try:
        modified = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True, text=True, check=True, cwd=str(target_repo)
        ).stdout.splitlines()
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True, cwd=str(target_repo)
        ).stdout.splitlines()
        return {f for f in modified + untracked
                if Path(f).suffix in SOURCE_EXTS and f.startswith(source_dir)}
    except subprocess.CalledProcessError as e:
        print(f"[CodebaseReader] WARN — git diff failed: {e}. Will check for missing sidecars only.")
        return None


def _git_hash(target_repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(target_repo)
        ).stdout.strip()
    except Exception:
        return "unknown"


def _all_sources(source_dir: str, target_repo: Path) -> set[str]:
    patterns = _load_ignore_patterns()
    root = target_repo / source_dir
    if not root.exists():
        print(f"[CodebaseReader] WARN — source directory not found: {root}")
        return set()
    return {
        str(p.relative_to(target_repo)) for p in root.rglob("*")
        if p.suffix in SOURCE_EXTS
        and not any(s in p.parts for s in SKIP_DIRS)
        and not _is_ignored(p, patterns)
    }


# ─────────────────────────────────────────────────────────────
# Pass 1: Tree-sitter extraction
# ─────────────────────────────────────────────────────────────

def _get_parser(suffix: str):
    if not TREE_SITTER_AVAILABLE:
        return None
    if suffix in (".jsx", ".js"):
        return Parser(Language(tsjs.language()))
    if suffix in (".tsx", ".ts"):
        return Parser(Language(tsts.language_typescript()))
    if suffix == ".py" and PYTHON_AVAILABLE:
        return Parser(Language(tspy.language()))
    return None


def _node_text(node, code: bytes) -> str:
    return code[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _classify(path: Path) -> str:
    parts = path.parts
    if "components" in parts:        return "component"
    if "api" in parts:               return "api_route"
    if path.name.startswith("page"): return "page"
    if path.name.startswith("layout"): return "layout"
    return "module"


def _walk(node, code: bytes, r: dict):
    t = node.type

    if t == "function_definition":
        name = node.child_by_field_name("name")
        if name:
            r["exports"].append(_node_text(name, code))

    elif t == "decorated_definition":
        for child in node.children:
            if child.type == "decorator":
                dec = _node_text(child, code)
                if any(m in dec for m in ("get", "post", "put", "delete", "patch")):
                    method = next(
                        (m.upper() for m in ("get","post","put","delete","patch") if m in dec),
                        None
                    )
                    if method:
                        r["api_methods"].append(method)

    elif t in ("import_statement", "import_from_statement"):
        r["local_imports"].append(_node_text(node, code)[:60])

    if t == "expression_statement":
        txt = _node_text(node, code)
        if "'use client'" in txt or '"use client"' in txt:
            r["is_client"] = True
    elif t == "export_default_declaration":
        for child in node.children:
            if child.type in ("function_declaration", "class_declaration"):
                n = child.child_by_field_name("name")
                if n: r["exports"].append(_node_text(n, code))
    elif t == "export_statement":
        decl = node.child_by_field_name("declaration")
        if decl:
            n = decl.child_by_field_name("name")
            if n:
                val = _node_text(n, code)
                if val in ("GET","POST","PUT","DELETE","PATCH"):
                    r["api_methods"].append(val)
                else:
                    r["exports"].append(val)
    elif t == "import_statement":
        src = node.child_by_field_name("source")
        if src:
            s = _node_text(src, code).strip("\"'")
            if s.startswith("."): r["local_imports"].append(s)
    elif t == "call_expression":
        fn = node.child_by_field_name("function")
        if fn:
            name = _node_text(fn, code)
            if name.startswith("use") and len(name) > 3 and name[3].isupper():
                r["hooks"].append(name)
    elif t == "jsx_opening_element":
        n = node.child_by_field_name("name")
        if n:
            el = _node_text(n, code)
            if el[0].isupper(): r["renders"].append(el)

    for child in node.children:
        _walk(child, code, r)


def _extract(path: Path) -> dict:
    r = {
        "file": str(path), "type": _classify(path),
        "exports": [], "local_imports": [], "hooks": [],
        "renders": [], "api_methods": [], "is_client": False
    }
    parser = _get_parser(path.suffix)
    if parser:
        code = path.read_bytes()
        _walk(parser.parse(code).root_node, code, r)
        for k in ("exports","local_imports","hooks","renders","api_methods"):
            r[k] = list(dict.fromkeys(r[k]))
    else:
        r["fallback"] = True
    return r


def _format_extraction(e: dict) -> str:
    lines = [f"File: {e['file']}", f"Type: {e['type']}"]
    if e.get("is_client"):     lines.append("Client component: yes")
    if e["exports"]:           lines.append(f"Exports: {', '.join(e['exports'])}")
    if e["hooks"]:             lines.append(f"Hooks: {', '.join(e['hooks'])}")
    if e["renders"]:           lines.append(f"Renders: {', '.join(e['renders'])}")
    if e["local_imports"]:     lines.append(f"Local imports: {', '.join(e['local_imports'])}")
    if e["api_methods"]:       lines.append(f"HTTP methods: {', '.join(e['api_methods'])}")
    if e.get("fallback"):      lines.append("Note: tree-sitter unavailable, filename only")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Pass 2: Per-file sidecar (one local LLM call per changed file)
# Sidecars live in Repo B under context_sidecars/
# ─────────────────────────────────────────────────────────────

def _sidecar_path(source_file: str, target_repo: Path) -> Path:
    return target_repo / "context_sidecars" / (source_file + ".md")


def _write_sidecar(extraction: dict, llm: LLM, target_repo: Path, git_hash: str):
    sidecar = _sidecar_path(extraction["file"], target_repo)
    sidecar.parent.mkdir(parents=True, exist_ok=True)

    agent = Agent(
        role="Code Documenter",
        goal="Write accurate, concise documentation for a source file",
        backstory="""You write documentation that serves two audiences:
        a human engineer onboarding to the codebase, and an LLM planning
        agent that needs to understand what this file does and how it fits.
        You work only from structural extraction data — never raw source.
        You are precise and do not speculate beyond what the data shows.
        You explicitly note what the file does NOT do when that boundary matters.""",
        llm=llm,
        tools=[],
        verbose=False
    )

    task = Task(
        description=f"""
Write a documentation sidecar from this structural extraction.
Work only from the data below — do not invent details not present.

STRUCTURAL EXTRACTION:
{_format_extraction(extraction)}

Produce markdown with these sections
(omit any section where you have nothing meaningful to say):

## Purpose
What this file does in the application. One or two sentences.

## Exports
What this file makes available to the rest of the codebase.

## Dependencies
Hooks used, components rendered, local files imported.
State explicitly if none detected.

## What it does NOT do
Important boundaries a developer or planning agent should know.

## Notes
Anything else worth knowing when picking up this file cold.

---
_Indexed: {datetime.now().strftime('%Y-%m-%d')} — git: {git_hash}_

Return the markdown text only — no preamble, no explanation.
""",
        expected_output="Markdown documentation for the source file",
        agent=agent
    )

    result = Crew(agents=[agent], tasks=[task], process=Process.sequential).kickoff()
    sidecar.write_text(result.raw)
    return str(sidecar)


# ─────────────────────────────────────────────────────────────
# Pass 3: Aggregation (one local LLM call)
# Output goes to runs/{run-id}/generated/codebase_context.md
# ─────────────────────────────────────────────────────────────

def _aggregate(llm: LLM, target_repo: Path, run_dir: Path):
    sidecar_root  = target_repo / "context_sidecars"
    context_out   = run_dir / "generated" / "codebase_context.md"
    context_out.parent.mkdir(parents=True, exist_ok=True)

    all_sidecars = sorted(sidecar_root.rglob("*.md")) if sidecar_root.exists() else []

    if not all_sidecars:
        context_out.write_text(
            "# Codebase Context\n\nNo source files found. "
            "Greenfield project — plan for full construction from scratch."
        )
        print("[CodebaseReader] No sidecars found — greenfield context written.")
        return

    combined = "\n\n---\n\n".join(
        f"### {p.relative_to(target_repo)}\n{p.read_text()}" for p in all_sidecars
    )

    agent = Agent(
        role="Codebase Synthesiser",
        goal="Synthesise per-file documentation into a whole-system view",
        backstory="""You read per-file documentation sidecars and produce a
        single aggregated context document for a Tech Lead who needs to
        understand the current codebase state before planning tickets.
        You organise by layer and responsibility, surface relationships
        between files, and explicitly flag what is not yet built.""",
        llm=llm,
        tools=[],
        verbose=True
    )

    task = Task(
        description=f"""
Synthesise these per-file documentation sidecars into one aggregated context.

SIDECARS:
{combined}

Produce a markdown document with these sections:

## Codebase Overview
2-3 sentences. What is this codebase and what does it do?

## Component Layer
What UI components exist, what each does, how they relate to each other.

## Page Layer
What pages/routes exist, what each renders, what data each needs.

## API Layer
What API routes exist, what methods they expose, what they return.

## Key Relationships
Important dependencies a planning agent needs to know.
Which components depend on which API routes.
Which pages compose which components.

## What is not yet built
Gaps visible from the sidecars — missing components, stubbed routes,
empty files, TODOs.
""",
        expected_output="Aggregated codebase context markdown",
        agent=agent
    )

    result = Crew(agents=[agent], tasks=[task], process=Process.sequential).kickoff()
    context_out.write_text(result.raw)
    print(f"[CodebaseReader] Written: {context_out}")


# ─────────────────────────────────────────────────────────────
# Flat index (no LLM, written to run generated/ for reference)
# ─────────────────────────────────────────────────────────────

def _write_flat_index(source_dir: str, target_repo: Path, run_dir: Path):
    index_out = run_dir / "generated" / "codebase_index.md"
    index_out.parent.mkdir(parents=True, exist_ok=True)

    extractions = [
        _extract(target_repo / f)
        for f in sorted(_all_sources(source_dir, target_repo))
    ]
    lines = ["# Codebase Index (structural)", ""]
    for e in extractions:
        lines.append(f"- `{e['file']}` ({e['type']})")
        if e["exports"]:
            lines.append(f"  exports: {', '.join(e['exports'])}")
    index_out.write_text("\n".join(lines))
    print(f"[CodebaseReader] Written: {index_out}")


# ─────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────

def run_codebase_reader(source_dir: str, target_repo: Path, run_dir: Path, force: bool = False):
    """
    Full three-pass run.

    Pass 1: Tree-sitter extraction (no LLM)
    Pass 2: Per-file sidecar generation (local LLM, incremental)
             Sidecars written to Repo B's context_sidecars/ — git-tracked there.
    Pass 3: Aggregation → runs/{run-id}/generated/codebase_context.md

    Args:
        source_dir:  directory to scan relative to target_repo (e.g. "frontend")
        target_repo: Path to Repo B
        run_dir:     Path to the active run folder (runs/{run-id}/)
        force:       reprocess all files regardless of git state
    """
    llm = LLM(
        model=f"ollama/{os.getenv('APP_SUMMARISER_MODEL', '').strip()}",
        base_url=os.getenv("OLLAMA_HOST", "").strip()
    )

    git_hash = _git_hash(target_repo)

    if force:
        print("[CodebaseReader] Force mode — reprocessing all files")
        to_process = _all_sources(source_dir, target_repo)
    else:
        changed    = _git_changed(source_dir, target_repo)
        to_process = changed if changed is not None else set()

        all_files = _all_sources(source_dir, target_repo)
        missing_sidecars = {
            f for f in all_files
            if not _sidecar_path(f, target_repo).exists()
        }
        if missing_sidecars:
            print(f"[CodebaseReader] {len(missing_sidecars)} file(s) missing sidecars — adding to queue")
        to_process = to_process | missing_sidecars
        print(f"[CodebaseReader] {len(to_process)} file(s) to process")

    updated = []
    for file_path in sorted(to_process):
        abs_path = target_repo / file_path
        if not abs_path.exists():
            sidecar = _sidecar_path(file_path, target_repo)
            if sidecar.exists():
                sidecar.unlink()
                print(f"[CodebaseReader] Removed sidecar for deleted file: {file_path}")
            continue
        print(f"[CodebaseReader] Indexing: {file_path}")
        try:
            extraction = _extract(abs_path)
            updated.append(_write_sidecar(extraction, llm, target_repo, git_hash))
        except Exception as e:
            print(f"[CodebaseReader] ERROR indexing {file_path}: {e}")

    if not updated:
        print("[CodebaseReader] No changes — sidecars up to date")

    print("[CodebaseReader] Aggregating sidecars...")
    _aggregate(llm, target_repo, run_dir)

    _write_flat_index(source_dir, target_repo, run_dir)