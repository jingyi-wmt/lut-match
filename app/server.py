"""LUT Match local server — single user, in-memory session."""

from __future__ import annotations

import io as _io
import tomllib
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.engine.correct import Correction, apply_correction, compute_correction
from app.engine.io import downsample_for_analysis, load_image
from app.engine.logspace import FOOTAGE_TYPES, to_display
from app.engine.lut import bake_lut, write_cube
from app.engine.match import apply_match, mkl_transform
from app.engine.recipe import GradingRecipe
from app.engine.render import apply_recipe, soft_clip
from app.vision.provider import VisionError
from app.vision.registry import build_provider

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
OUTPUT_DIR = ROOT / "output"
PREVIEW_MAX = 960

app = FastAPI(title="LUT Match")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return tomllib.loads(CONFIG_PATH.read_text())
    return {}


class Session:
    reference: np.ndarray | None = None
    reference_name: str = "reference"
    frame: np.ndarray | None = None
    footage_type: str = "rec709"
    recipe: GradingRecipe | None = None
    tweaks: GradingRecipe = GradingRecipe()
    correction: Correction | None = None
    correction_strength: float = 1.0
    match_params: tuple[np.ndarray, np.ndarray] | None = None
    warnings: dict[str, list[str]] = {}


S = Session()


def _to_jpeg(pixels: np.ndarray) -> bytes:
    h, w = pixels.shape[:2]
    scale = max(h, w) / PREVIEW_MAX
    pil = Image.fromarray((np.clip(pixels, 0, 1) * 255).astype(np.uint8))
    if scale > 1:
        pil = pil.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
    buf = _io.BytesIO()
    pil.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _require(cond, msg):
    if not cond:
        raise HTTPException(400, msg)


def _display_frame() -> np.ndarray:
    return to_display(S.frame, S.footage_type)


def _grade(pixels_display: np.ndarray, mode: str, strength: float) -> np.ndarray:
    """Correction first (fix lighting), match second, user fine-tune last."""
    out = pixels_display
    if S.correction is not None:
        out = apply_correction(out, S.correction, S.correction_strength)
    if mode == "match":
        _require(S.match_params is not None, "Literal match not computed yet")
        A, b = S.match_params
        out = apply_match(out, A, b, strength)
    else:
        _require(S.recipe is not None, "No recipe yet — run Analyze first")
        out = apply_recipe(out, S.recipe, strength)
    return soft_clip(apply_recipe(out, S.tweaks))


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.post("/upload/{kind}")
async def upload(kind: str, file: UploadFile):
    _require(kind in ("reference", "frame"), "kind must be reference|frame")
    data = await file.read()
    try:
        img = load_image(data)
    except Exception as e:
        raise HTTPException(400, f"Could not read image: {e}")
    if kind == "reference":
        S.reference = img.pixels
        S.reference_name = Path(file.filename or "reference").stem
    else:
        S.frame = img.pixels
    S.match_params = None
    S.warnings[kind] = img.warnings
    return {"ok": True, "warnings": img.warnings}


class AnalyzeRequest(BaseModel):
    footage_type: str = "rec709"
    auto_correct: bool = True


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    _require(S.reference is not None, "Upload a reference image first")
    _require(S.frame is not None, "Provide a footage frame first")
    _require(req.footage_type in FOOTAGE_TYPES, f"footage_type must be one of {FOOTAGE_TYPES}")
    S.footage_type = req.footage_type
    S.tweaks = GradingRecipe()

    display = _display_frame()

    # Correction first: fix the footage's lighting, then match the look on top.
    S.correction = compute_correction(display) if req.auto_correct else None
    corrected = apply_correction(display, S.correction) if S.correction else display

    # Literal match is always computed (cheap) — used as mode and fallback.
    A, b = mkl_transform(corrected, S.reference)
    S.match_params = (A, b)

    provider = build_provider(load_config())
    if provider is None:
        return {"mode": "match", "note": "No vision provider configured — using literal match."}

    tmp = OUTPUT_DIR / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    ref_path, frame_path = tmp / "reference.png", tmp / "frame.png"
    for path, pixels in ((ref_path, S.reference), (frame_path, corrected)):
        small = downsample_for_analysis(pixels)
        Image.fromarray((np.clip(small, 0, 1) * 255).astype(np.uint8)).save(path)

    try:
        recipe = await run_in_threadpool(provider.extract_dna, ref_path, frame_path)
    except VisionError as e:
        return {"mode": "match", "note": f"Vision analysis failed ({e}) — using literal match."}
    S.recipe = recipe
    return {"mode": "dna", "recipe": recipe.model_dump(), "provider": provider.name}


class RecipeUpdate(BaseModel):
    recipe: GradingRecipe


@app.post("/recipe")
def update_recipe(req: RecipeUpdate):
    S.recipe = req.recipe
    return {"ok": True}


@app.post("/tweaks")
def update_tweaks(req: RecipeUpdate):
    """User fine-tune layer, applied after the match in every mode."""
    S.tweaks = req.recipe
    return {"ok": True}


class OptionsUpdate(BaseModel):
    correction_strength: float | None = None


@app.post("/options")
def update_options(req: OptionsUpdate):
    if req.correction_strength is not None:
        S.correction_strength = float(min(max(req.correction_strength, 0.0), 1.0))
    return {"ok": True}


@app.get("/image/{which}")
def image(which: str):
    if which == "reference":
        _require(S.reference is not None, "no reference")
        return Response(_to_jpeg(S.reference), media_type="image/jpeg")
    if which == "frame":
        _require(S.frame is not None, "no frame")
        return Response(_to_jpeg(S.frame), media_type="image/jpeg")
    raise HTTPException(404, "unknown image")


@app.get("/preview")
def preview(strength: float = 1.0, mode: str = "dna"):
    _require(S.frame is not None, "no frame")
    graded = _grade(_display_frame(), mode, strength)
    return Response(_to_jpeg(graded), media_type="image/jpeg")


@app.get("/export")
def export(strength: float = 1.0, mode: str = "dna", size: int = 33):
    _require(S.frame is not None, "no frame")
    _require(size in (17, 33, 65), "size must be 17, 33 or 65")
    footage_type = S.footage_type

    def pipeline(lattice: np.ndarray) -> np.ndarray:
        return _grade(to_display(lattice, footage_type), mode, strength)

    table = bake_lut(pipeline, size=size)
    OUTPUT_DIR.mkdir(exist_ok=True)
    suffix = "dna" if mode == "dna" else "match"
    name = f"{S.reference_name}-{suffix}.cube"
    path = write_cube(table, OUTPUT_DIR / name, title=f"LUT Match — {S.reference_name}")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@app.get("/status")
def status():
    cfg = load_config()
    provider = build_provider(cfg)
    return {
        "reference": S.reference is not None,
        "frame": S.frame is not None,
        "footage_type": S.footage_type,
        "recipe": S.recipe.model_dump() if S.recipe else None,
        "auto_correct": S.correction is not None,
        "warnings": S.warnings,
        "provider": provider.name if provider else None,
        "footage_types": FOOTAGE_TYPES,
    }
