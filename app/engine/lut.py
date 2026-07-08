"""Bake a grading pipeline into a Premiere-ready .cube 3D LUT."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np

LUT_SIZE = 33

Pipeline = Callable[[np.ndarray], np.ndarray]
"""Maps an (N, 3) float32 array of input colors in [0,1] to graded colors."""


def bake_lut(pipeline: Pipeline, size: int = LUT_SIZE) -> np.ndarray:
    """Sample the pipeline on a size³ lattice. Returns (size³, 3), red fastest."""
    axis = np.linspace(0.0, 1.0, size, dtype=np.float32)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    lattice = np.stack([r.ravel(), g.ravel(), b.ravel()], axis=-1)
    out = pipeline(lattice).astype(np.float32)
    if out.shape != lattice.shape:
        raise ValueError(f"pipeline returned shape {out.shape}, expected {lattice.shape}")
    out = np.clip(out, 0.0, 1.0)
    if not np.isfinite(out).all():
        raise ValueError("pipeline produced NaN/Inf values; refusing to write LUT")
    return out


def write_cube(table: np.ndarray, path: str | Path, title: str = "LUT Match") -> Path:
    path = Path(path)
    size = round(len(table) ** (1 / 3))
    if size**3 != len(table):
        raise ValueError(f"table length {len(table)} is not a perfect cube")
    lines = [
        f'TITLE "{title}"',
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
    ]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table]
    path.write_text("\n".join(lines) + "\n")
    return path


def read_cube(path: str | Path) -> np.ndarray:
    """Parse a .cube file back into a (size³, 3) table (for tests/round-trips)."""
    rows = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line[0].isalpha():
            continue
        rows.append([float(v) for v in line.split()])
    return np.asarray(rows, dtype=np.float32)


def apply_cube(table: np.ndarray, pixels: np.ndarray) -> np.ndarray:
    """Trilinear-interpolate pixels through the LUT (verification helper)."""
    size = round(len(table) ** (1 / 3))
    grid = table.reshape(size, size, size, 3)  # [b, g, r]
    p = np.clip(pixels.reshape(-1, 3).astype(np.float64), 0.0, 1.0) * (size - 1)
    idx = np.floor(p).astype(int)
    idx = np.minimum(idx, size - 2)
    frac = p - idx
    r0, g0, b0 = idx[:, 0], idx[:, 1], idx[:, 2]
    fr, fg, fb = frac[:, 0:1], frac[:, 1:2], frac[:, 2:3]

    def corner(dr, dg, db):
        return grid[b0 + db, g0 + dg, r0 + dr]

    out = (
        corner(0, 0, 0) * (1 - fr) * (1 - fg) * (1 - fb)
        + corner(1, 0, 0) * fr * (1 - fg) * (1 - fb)
        + corner(0, 1, 0) * (1 - fr) * fg * (1 - fb)
        + corner(0, 0, 1) * (1 - fr) * (1 - fg) * fb
        + corner(1, 1, 0) * fr * fg * (1 - fb)
        + corner(1, 0, 1) * fr * (1 - fg) * fb
        + corner(0, 1, 1) * (1 - fr) * fg * fb
        + corner(1, 1, 1) * fr * fg * fb
    )
    return out.reshape(pixels.shape).astype(np.float32)
