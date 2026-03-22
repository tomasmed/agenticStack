"""
ArtDirector Crew

Receives structural asset_manifest.json from TeamLead.
Enriches each asset with a complete generation block.
Flow writes the enriched manifest — agent just generates JSON.
"""

import json
from pathlib import Path
from crewai import Agent, Crew, Task, Process, LLM
import os


REQUIRED_GENERATION_FIELDS = [
    "subject", "style", "palette", "negative",
    "sd_weight", "aspect_ratio", "guidance", "composition"
]


def _validate_enriched_manifest(manifest: dict) -> tuple[list, list]:
    missing_blocks = []
    incomplete_blocks = []
    for i, asset in enumerate(manifest.get("assets", [])):
        asset_id = asset.get("id", f"asset_{i}")
        if "generation" not in asset:
            missing_blocks.append(asset_id)
            continue
        missing_fields = [
            f for f in REQUIRED_GENERATION_FIELDS
            if f not in asset["generation"]
        ]
        if missing_fields:
            incomplete_blocks.append(f"{asset_id}: missing {missing_fields}")
    return missing_blocks, incomplete_blocks


def build_art_director_crew(
    manifest_path: str,
    brief_path: str,
    visual_identity_path: str,
) -> Crew:

    llm = LLM(
        model=f"ollama/{os.getenv('ART_DIRECTOR_MODEL', '').strip()}",
        base_url=os.getenv("OLLAMA_HOST", "").strip()
    )

    # manifest = who the agent is
    manifest_text = Path("manifests/ArtDirector.md").read_text()

    # dynamic context = what the agent knows for this run
    brief = Path(brief_path).read_text() if Path(brief_path).exists() else ""
    visual_identity = Path(visual_identity_path).read_text() if Path(visual_identity_path).exists() else ""
    manifest_json = Path(manifest_path).read_text() if Path(manifest_path).exists() else ""

    if not manifest_json:
        raise FileNotFoundError(f"Asset manifest not found: {manifest_path}")

    asset_count = len(json.loads(manifest_json).get("assets", []))
    print(f"[ArtDirectorCrew] Assets to enrich: {asset_count}")

    agent = Agent(
        role="Art Director",
        goal="Enrich every asset in the manifest with a complete generation block",
        backstory=manifest_text,
        llm=llm,
        tools=[],
        verbose=True
    )

    task = Task(
        description=f"""
You are enriching an asset manifest with visual generation instructions.

PRODUCT BRIEF:
{brief}

VISUAL IDENTITY (canonical palette, mood, placement rules):
{visual_identity}

STRUCTURAL MANIFEST TO ENRICH:
{manifest_json}

For every asset in the assets array, add a complete generation block.

Required fields in every generation block:
  subject, style, palette, negative, sd_weight, aspect_ratio, guidance, composition

Rules:
- Do NOT modify any existing fields
- Do NOT add or remove assets
- Add ONLY the generation block to each asset
- Palette values must use canonical names from the visual identity above
- Subject and style are plain English — not SD tag syntax
- Composition notes must account for UI elements sitting on top
- Status field must remain unchanged
- Return the complete enriched manifest as valid JSON only
- No markdown fences, no preamble, no explanation
        """,
        expected_output="Valid JSON — the complete enriched asset manifest",
        agent=agent
    )

    return Crew(agents=[agent], tasks=[task], process=Process.sequential)


def run_art_director(
    manifest_path: str,
    brief_path: str,
    visual_identity_path: str,
    output_path: str,
) -> dict:
    """
    Run ArtDirector crew, extract JSON from result.raw, validate, write to disk.
    Flow calls this — agent just generates JSON text.
    """
    crew = build_art_director_crew(
        manifest_path=manifest_path,
        brief_path=brief_path,
        visual_identity_path=visual_identity_path,
    )
    result = crew.kickoff()
    raw = result.raw

    # extract JSON robustly — local models add prose preamble
    start = raw.find("{")
    end   = raw.rfind("}")
    if start == -1 or end == -1:
        debug_path = Path(output_path).parent / "artdirector_raw.txt"
        Path(debug_path).write_text(raw)
        raise ValueError(
            f"ArtDirector response contains no JSON.\n"
            f"Raw output saved to: {debug_path}"
        )

    try:
        enriched = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        debug_path = Path(output_path).parent / "artdirector_raw.txt"
        Path(debug_path).write_text(raw)
        raise ValueError(
            f"ArtDirector returned invalid JSON: {e}\n"
            f"Raw output saved to: {debug_path}\n"
            "Try a more capable model via ART_DIRECTOR_MODEL in .env"
        )

    # flow writes the file
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(enriched, indent=2))

    # validate
    missing_blocks, incomplete_blocks = _validate_enriched_manifest(enriched)
    asset_count = len(enriched.get("assets", []))
    fully_enriched = asset_count - len(missing_blocks)
    print(f"\n[ArtDirectorCrew] {fully_enriched}/{asset_count} assets fully enriched")

    if missing_blocks:
        print(f"[ArtDirectorCrew] WARN — missing generation blocks: {missing_blocks}")
    if incomplete_blocks:
        print(f"[ArtDirectorCrew] WARN — incomplete blocks:")
        for item in incomplete_blocks:
            print(f"  - {item}")

    if not missing_blocks and not incomplete_blocks:
        print("[ArtDirectorCrew] All generation blocks complete.")

    return enriched