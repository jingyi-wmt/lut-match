"""Image loading and sanity warnings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

ACCEPTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MAX_ANALYSIS_DIM = 1024


@dataclass
class LoadedImage:
    pixels: np.ndarray            # float32, (H, W, 3), [0, 1]
    warnings: list[str] = field(default_factory=list)


def load_image(source: str | Path | bytes) -> LoadedImage:
    if isinstance(source, bytes):
        import io as _io
        pil = Image.open(_io.BytesIO(source))
    else:
        path = Path(source)
        if path.suffix.lower() not in ACCEPTED_SUFFIXES:
            raise ValueError(f"unsupported image type {path.suffix!r} (use JPEG/PNG/TIFF)")
        pil = Image.open(path)

    pil.load()
    if pil.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(pil, dtype=np.float32) / 65535.0
        arr = np.stack([arr] * 3, axis=-1) if arr.ndim == 2 else arr
    else:
        pil = pil.convert("RGB")
        arr = np.asarray(pil, dtype=np.float32) / 255.0
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    arr = arr[..., :3]

    img = LoadedImage(pixels=arr)
    _add_warnings(img)
    return img


def _add_warnings(img: LoadedImage) -> None:
    h, w = img.pixels.shape[:2]
    luma = img.pixels @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    if min(h, w) < 512:
        img.warnings.append(f"Image is small ({w}×{h}); color analysis may be unreliable.")
    if float(luma.mean()) < 0.08:
        img.warnings.append("Image is very dark; shadows dominate the analysis.")
    clipped = float(((luma < 0.004) | (luma > 0.996)).mean())
    if clipped > 0.05:
        img.warnings.append(f"{clipped:.0%} of pixels are clipped; matched blacks/whites may be off.")


def downsample_for_analysis(pixels: np.ndarray, max_dim: int = MAX_ANALYSIS_DIM) -> np.ndarray:
    h, w = pixels.shape[:2]
    scale = max(h, w) / max_dim
    if scale <= 1.0:
        return pixels
    pil = Image.fromarray((np.clip(pixels, 0, 1) * 255).astype(np.uint8))
    pil = pil.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
    return np.asarray(pil, dtype=np.float32) / 255.0
