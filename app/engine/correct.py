"""Auto lighting correction — runs BEFORE any look matching.

Fixes exposure/levels/white-balance problems in the footage so the creative
grade starts from a technically sound image ("correction first, match second").

The correction is a global color transform (per-channel affine + neutral
gamma), so it bakes into the LUT along with everything else.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

TARGET_BLACK = 0.02
TARGET_WHITE = 0.95
TARGET_MID = 0.45


@dataclass
class Correction:
    scale: float = 1.0                     # levels stretch (luma-uniform)
    offset: float = 0.0
    wb_gains: tuple[float, float, float] = (1.0, 1.0, 1.0)
    gamma_exp: float = 1.0                 # neutral exposure gamma

    def is_identity(self) -> bool:
        return (
            abs(self.scale - 1) < 1e-3
            and abs(self.offset) < 1e-3
            and all(abs(g - 1) < 1e-3 for g in self.wb_gains)
            and abs(self.gamma_exp - 1) < 1e-3
        )


def compute_correction(frame: np.ndarray) -> Correction:
    """Analyze a display-space frame and derive its lighting correction."""
    px = frame.reshape(-1, 3).astype(np.float32)
    luma = px @ _LUMA

    # 1. Levels: stretch 1st..99th percentile to the target range.
    p1, p99 = np.percentile(luma, [1.0, 99.0])
    spread = max(float(p99 - p1), 1e-3)
    scale = float(np.clip((TARGET_WHITE - TARGET_BLACK) / spread, 0.75, 2.5))
    offset = float(TARGET_BLACK - p1 * scale)

    leveled = px * scale + offset

    # 2. Gray-world white balance on the leveled image.
    means = np.clip(leveled.mean(axis=0), 1e-3, None)
    gray = float((means * _LUMA).sum())
    gains = np.clip(gray / means, 0.75, 1.35)

    balanced = np.clip(leveled * gains, 1e-4, 1.0)

    # 3. Exposure: neutral gamma pulling mean luma toward middle gray.
    mid = float(np.clip((balanced @ _LUMA).mean(), 0.05, 0.95))
    exp = float(np.clip(np.log(TARGET_MID) / np.log(mid), 0.65, 1.55))

    return Correction(scale, offset, (float(gains[0]), float(gains[1]), float(gains[2])), exp)


def apply_correction(pixels: np.ndarray, c: Correction) -> np.ndarray:
    out = pixels.astype(np.float32, copy=False) * np.float32(c.scale) + np.float32(c.offset)
    out = out * np.asarray(c.wb_gains, dtype=np.float32)
    out = np.clip(out, 0.0, 1.0) ** np.float32(c.gamma_exp)
    return out
