"""LUT Match local server — single user, in-memory session, pure math engine."""

from __future__ import annotations

import io as _io
import os
import signal
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel

from app.engine.correct import Correction, apply_correction, compute_correction
from app.engine.io import load_image
from app.engine.logspace import FOOTAGE_TYPES, to_display
from app.engine.lut import bake_lut, write_cube
from app.engine.match import apply_banded_match, banded_mkl_transform
from app.engine.recipe import GradingRecipe
from app.engine.render import apply_recipe, soft_clip

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
PREVIEW_MAX = 960

app = FastAPI(title="LUT Match")


class Session:
    reference: np.ndarray | None = None
    reference_name: str = "reference"
    frame: np.ndarray | None = None
    footage_type: str = "rec709"
    tweaks: GradingRecipe = GradingRecipe()
    correction: Correction | None = None
    correction_strength: float = 1.0
    auto_correct: bool = True
    keep_luma: bool = False
    match_transforms: list | None = None
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


def _recompute(display: np.ndarray | None = None) -> None:
    """Refresh correction + match transforms from current session settings.

    The match is calibrated against the FULLY corrected frame. The strength
    slider then modulates the actual input at grade time — so dialing the
    correction down visibly lets the footage's original lighting show
    through, instead of the match re-normalizing it away.
    """
    if display is None:
        display = _display_frame()
    S.correction = compute_correction(display) if S.auto_correct else None
    corrected = (
        apply_correction(display, S.correction, 1.0) if S.correction else display
    )
    S.match_transforms = banded_mkl_transform(corrected, S.reference)


def _grade(pixels_display: np.ndarray, strength: float) -> np.ndarray:
    """Correction first (fix lighting), match second, user fine-tune last."""
    out = pixels_display
    if S.correction is not None:
        out = apply_correction(out, S.correction, S.correction_strength)
    _require(S.match_transforms is not None, "Run Match colors first")
    out = apply_banded_match(out, S.match_transforms, strength, keep_luma=S.keep_luma)
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
    S.match_transforms = None
    S.correction = None
    S.warnings[kind] = img.warnings
    return {"ok": True, "warnings": img.warnings}


class AnalyzeRequest(BaseModel):
    footage_type: str = "rec709"
    auto_correct: bool = True


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    _require(S.reference is not None, "Upload a reference image first")
    _require(S.frame is not None, "Provide a footage frame first")
    _require(req.footage_type in FOOTAGE_TYPES, f"footage_type must be one of {FOOTAGE_TYPES}")
    S.footage_type = req.footage_type
    S.auto_correct = req.auto_correct
    S.tweaks = GradingRecipe()
    _recompute()
    return {
        "ok": True,
        "correction_summary": S.correction.describe() if S.correction else "auto-correction off",
    }


class RecipeUpdate(BaseModel):
    recipe: GradingRecipe


@app.post("/tweaks")
def update_tweaks(req: RecipeUpdate):
    """User fine-tune layer, applied after the match."""
    S.tweaks = req.recipe
    return {"ok": True}


class OptionsUpdate(BaseModel):
    correction_strength: float | None = None
    auto_correct: bool | None = None
    keep_luma: bool | None = None


@app.post("/options")
def update_options(req: OptionsUpdate):
    if req.correction_strength is not None:
        S.correction_strength = float(min(max(req.correction_strength, 0.0), 1.0))
    if req.keep_luma is not None:
        S.keep_luma = req.keep_luma
    if req.auto_correct is not None:
        S.auto_correct = req.auto_correct
    # Only toggling auto-correct changes what the match is calibrated on;
    # strength changes are applied live at grade time.
    if req.auto_correct is not None and (
        S.frame is not None and S.reference is not None and S.match_transforms is not None
    ):
        _recompute()
    return {
        "ok": True,
        "correction_summary": S.correction.describe() if S.correction else "auto-correction off",
    }


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
def preview(strength: float = 1.0):
    _require(S.frame is not None, "no frame")
    graded = _grade(_display_frame(), strength)
    return Response(_to_jpeg(graded), media_type="image/jpeg")


@app.get("/export")
def export(strength: float = 1.0, size: int = 33):
    _require(S.frame is not None, "no frame")
    _require(size in (17, 33, 65), "size must be 17, 33 or 65")
    footage_type = S.footage_type

    def pipeline(lattice: np.ndarray) -> np.ndarray:
        return _grade(to_display(lattice, footage_type), strength)

    table = bake_lut(pipeline, size=size)
    OUTPUT_DIR.mkdir(exist_ok=True)
    name = f"{S.reference_name}-match.cube"
    path = write_cube(table, OUTPUT_DIR / name, title=f"LUT Match — {S.reference_name}")
    return FileResponse(path, filename=name, media_type="application/octet-stream")


@app.get("/status")
def status():
    return {
        "reference": S.reference is not None,
        "frame": S.frame is not None,
        "ready": S.match_transforms is not None,
        "footage_type": S.footage_type,
        "auto_correct": S.auto_correct,
        "keep_luma": S.keep_luma,
        "correction_strength": S.correction_strength,
        "correction_summary": (
            S.correction.describe()
            if S.correction
            else ("auto-correction off" if not S.auto_correct else None)
        ),
        "warnings": S.warnings,
        "footage_types": FOOTAGE_TYPES,
    }


@app.post("/shutdown")
def shutdown():
    """Quit button: stop the server after this response is sent."""
    import threading

    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return {"ok": True}
