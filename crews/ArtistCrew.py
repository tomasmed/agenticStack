"""
Artist Crew

Pure tooling layer. Reads an approved asset_manifest.json and generates
each asset using Stable Diffusion via the Forge UI API.

No aesthetic opinions. All content decisions come from the manifest.
Fails loudly on missing fields rather than substituting defaults.

The only technical defaults permitted:
  cfg_scale:  7.0       (universal SD baseline)
  sampler:    Euler a   (stable across checkpoints)
  svg size:   768x768   (vtracer input size)

Idempotent: skips assets whose final output already exists.
Output format determined by file extension in path — no type/format fields needed.
"""

import base64
import json
import os
import re
import time
from pathlib import Path

import requests


# ─────────────────────────────────────────────────────────────
# Config — read at call time so .env override takes effect
# ─────────────────────────────────────────────────────────────

def _sd_host() -> str:
    return os.getenv("SD_HOST", "http://localhost:7860").strip()

def _sd_url() -> str:
    return f"{_sd_host()}/sdapi/v1/txt2img"

def _sd_checkpoint() -> str | None:
    return os.getenv("SD_CHECKPOINT")

TIMEOUT      = 300
WEIGHT_STEPS = {"light": 22, "balanced": 28, "detailed": 35}

REQUIRED_GENERATION_FIELDS = [
    "subject", "style", "palette", "negative", "sd_weight"
]


# ─────────────────────────────────────────────────────────────
# Schema normalisation
# Single source of truth: file extension in path
# ─────────────────────────────────────────────────────────────

def _get_output_block(asset: dict) -> dict:
    """
    Normalise any manifest schema variant to a consistent output block.
    Supports: nested output{}, flat output_path, top-level path.
    Format/type derived from file extension — no type field needed.
    """
    if "output" in asset:
        out  = asset["output"]
        path = out.get("path", "")
        dims = out.get("dimensions")
    else:
        # flat schema — path at top level or output_path
        path = asset.get("path") or asset.get("output_path", "")
        dims = asset.get("dimensions")

    ext    = Path(path).suffix.lstrip(".").lower() or "png"
    is_svg = ext == "svg"

    return {
        "path":       path,
        "ext":        ext,
        "is_svg":     is_svg,
        "dimensions": dims,
    }


def _parse_dimensions(out: dict) -> tuple[int, int]:
    """Parse WxH from output block. Returns safe SD defaults if missing."""
    dim_str = out.get("dimensions")
    if not dim_str:
        return (768, 768) if out.get("is_svg") else (1024, 576)
    nums = re.findall(r"\d+", str(dim_str))
    if len(nums) < 2:
        return 1024, 576
    w = min(max(round(int(nums[0]) / 8) * 8, 512), 1536)
    h = min(max(round(int(nums[1]) / 8) * 8, 512), 1536)
    return w, h


# ─────────────────────────────────────────────────────────────
# Payload builder
# ─────────────────────────────────────────────────────────────

def _build_payload(asset: dict) -> tuple[dict, str]:
    """Build Forge API payload from manifest fields. Fails loudly on missing."""
    asset_id = asset.get("id", "unknown")
    gen      = asset.get("generation")

    if not gen:
        raise KeyError(f"Asset '{asset_id}' has no generation block. Run ArtDirector first.")

    for field in REQUIRED_GENERATION_FIELDS:
        if field not in gen:
            raise KeyError(f"Asset '{asset_id}' generation block missing '{field}'.")

    if gen["sd_weight"] not in WEIGHT_STEPS:
        raise ValueError(
            f"Asset '{asset_id}' unknown sd_weight '{gen['sd_weight']}'. "
            f"Must be one of: {list(WEIGHT_STEPS.keys())}"
        )

    out       = _get_output_block(asset)
    width, height = _parse_dimensions(out)

    prompt_parts = [
        gen["subject"],
        gen["style"],
        ", ".join(gen["palette"]),
        gen.get("guidance", ""),
        gen.get("composition", ""),
    ]
    positive  = ", ".join(p.strip() for p in prompt_parts if p.strip())
    negative  = gen["negative"]
    steps     = gen.get("steps") or WEIGHT_STEPS[gen["sd_weight"]]
    cfg_scale = gen.get("cfg_scale") or 7.0

    payload = {
        "prompt":          positive,
        "negative_prompt": negative,
        "steps":           int(steps),
        "cfg_scale":       float(cfg_scale),
        "width":           width,
        "height":          height,
        "sampler_name":    "Euler a",
        "batch_size":      1,
        "n_iter":          1,
        "send_images":     True,
        "save_images":     False,
    }

    checkpoint = _sd_checkpoint()
    if checkpoint:
        payload["override_settings"] = {"sd_model_checkpoint": checkpoint}
        payload["override_settings_restore_afterwards"] = True

    return payload, positive


# ─────────────────────────────────────────────────────────────
# Forge API
# ─────────────────────────────────────────────────────────────

def _call_forge(payload: dict) -> bytes:
    host = _sd_host()
    url  = _sd_url()
    try:
        resp = requests.post(url, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Forge timed out after {TIMEOUT}s.")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Forge at {host}. "
            "Check SD_HOST in .env and --api --listen on Forge."
        )
    data = resp.json()
    if "images" not in data or not data["images"]:
        raise RuntimeError(f"Forge returned no images. Keys: {list(data.keys())}")
    return base64.b64decode(data["images"][0])


# ─────────────────────────────────────────────────────────────
# SVG conversion
# ─────────────────────────────────────────────────────────────

def _raster_to_svg(png_path: Path, svg_path: Path):
    try:
        import vtracer
    except ImportError:
        raise RuntimeError("vtracer not installed. Run: pip install vtracer")

    print(f"    Converting to SVG via vtracer...")
    vtracer.convert_image_to_svg_py(
        str(png_path), str(svg_path),
        colormode="color",   hierarchical="stacked", mode="spline",
        filter_speckle=4,    color_precision=6,      layer_difference=16,
        corner_threshold=60, length_threshold=4.0,   max_iterations=10,
        splice_threshold=45, path_precision=3,
    )
    if png_path.exists():
        png_path.unlink()


# ─────────────────────────────────────────────────────────────
# Asset processing
# ─────────────────────────────────────────────────────────────

def _process_asset(asset: dict, i: int, total: int, project_root: Path) -> str | None:
    """Returns None on success, error string on failure."""
    asset_id = asset.get("id", f"asset_{i}")
    ticket   = asset.get("referenced_in_ticket", "unknown")
    out      = _get_output_block(asset)

    if not out["path"]:
        return f"no path defined for asset '{asset_id}'"

    final_path = project_root / out["path"]
    png_path   = final_path.with_suffix(".tmp.png") if out["is_svg"] else final_path

    print(f"  [{i}/{total}] {asset_id}  (ticket: {ticket})")
    print(f"    Output: {final_path}")

    # idempotent — skip if already done
    if final_path.exists():
        print(f"    ↷ Already exists, skipping\n")
        return None

    # PNG exists for SVG — vtracer only
    if out["is_svg"] and png_path.exists():
        print(f"    PNG exists — running vtracer only...")
        try:
            _raster_to_svg(png_path, final_path)
            print(f"    ✓ {final_path.name}\n")
            return None
        except RuntimeError as e:
            return str(e)

    # generate from scratch — create output dir here
    final_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload, prompt = _build_payload(asset)
        print(f"    Prompt:  {prompt[:120]}...")
        print(f"    Size:    {payload['width']}x{payload['height']}  "
              f"Steps: {payload['steps']}  CFG: {payload['cfg_scale']}")
        print(f"    Calling Forge at {_sd_host()}...")

        png_bytes = _call_forge(payload)

        if out["is_svg"]:
            png_path.write_bytes(png_bytes)
            _raster_to_svg(png_path, final_path)
        else:
            final_path.write_bytes(png_bytes)

        size_kb = final_path.stat().st_size // 1024
        print(f"    ✓ {final_path.name}  ({size_kb}KB)\n")
        return None

    except (KeyError, ValueError) as e:
        return f"manifest error: {e}"
    except RuntimeError as e:
        return f"forge error: {e}"


# ─────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────

def run_artist(manifest_path: str, project_root: str = "."):
    """
    Process all assets in a manifest.
    Soft-returns if status is not 'approved' — flow manages the gate via
    pipeline_state.json, not via exceptions here.

    Args:
        manifest_path: path to asset_manifest.json
        project_root:  root for resolving output paths (default: cwd)
    """
    manifest = json.loads(Path(manifest_path).read_text())
    root     = Path(project_root)
    status   = manifest.get("status", "draft")

    if status != "approved":
        print(f"[Artist] Manifest status is '{status}' — not approved, skipping.")
        print(f"[Artist] Set \"status\": \"approved\" in {manifest_path} to generate assets.")
        return

    assets = manifest.get("assets", [])
    if not assets:
        print("[Artist] No assets in manifest — nothing to generate.")
        return

    total  = len(assets)
    failed = {}

    print(f"[Artist] SD host:    {_sd_host()}")
    print(f"[Artist] Checkpoint: {_sd_checkpoint() or 'currently loaded in Forge'}")
    print(f"[Artist] Assets:     {total}\n")

    for i, asset in enumerate(assets, 1):
        error = _process_asset(asset, i, total, root)
        if error:
            failed[asset.get("id", f"asset_{i}")] = error
        if i < total:
            time.sleep(2)

    success = total - len(failed)
    print(f"\n[Artist] {'='*50}")
    print(f"[Artist] Generated: {success}/{total}")

    if failed:
        print(f"[Artist] Failures:")
        for asset_id, reason in failed.items():
            print(f"  ✗ {asset_id}: {reason}")
        print(f"\n[Artist] Fix failed assets and re-run.")
        print(f"[Artist] Successful assets will be skipped on re-run.")
    else:
        print(f"[Artist] All assets generated successfully.")