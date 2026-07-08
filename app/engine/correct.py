"""Auto lighting correction — runs BEFORE any look matching.

Fixes exposure/levels/white-balance problems in the footage so the creative
grade starts from a technically sound image ("correction first, match second").

Colorist-grade behavior:
- Levels/exposure only act when the frame is clearly off (deadband), so
  intentionally flat or moody footage isn't "fixed".
- White balance is measured from near-neutral pixels (the whites/grays that
  are actually in the scene), falling back to gray-world only when the scene
  has no neutrals — a sunset keeps its sunset.

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

# Deadbands: inside these ranges the frame is considered fine and untouched.
BLACK_OK_BELOW = 0.06     # p1 luma below this → blacks are fine
WHITE_OK_ABOVE = 0.80     # p99 luma above this → whites are fine
MID_OK_RANGE = (0.32, 0.58)

NEUTRAL_CHROMA = 0.10     # max-min RGB below this counts as a neutral pixel
NEUTRAL_MIN_FRACTION = 0.02


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

    def describe(self) -> str:
        """Human-readable summary of what the correction is doing."""
        if self.is_identity():
            return "frame is technically clean — nothing to fix"
        parts = []
        if abs(self.scale - 1) >= 1e-3 or abs(self.offset) >= 1e-3:
            parts.append(f"levels ×{self.scale:.2f}")
        wb_bits = [
            f"{ch}{(g - 1) * 100:+.0f}%"
            for ch, g in zip("RGB", self.wb_gains)
            if abs(g - 1) >= 0.005
        ]
        if wb_bits:
            parts.append("WB " + " ".join(wb_bits))
        if abs(self.gamma_exp - 1) >= 1e-3:
            direction = "brighter" if self.gamma_exp < 1 else "darker"
            parts.append(f"exposure γ{self.gamma_exp:.2f} ({direction})")
        return "fixing: " + " · ".join(parts)


def _wb_from_neutrals(px: np.ndarray) -> np.ndarray:
    """Gains from near-neutral pixels; gray-world fallback; conservative caps."""
    luma = px @ _LUMA
    chroma = px.max(axis=1) - px.min(axis=1)
    neutral = (chroma < NEUTRAL_CHROMA) & (luma > 0.15) & (luma < 0.95)

    if float(neutral.mean()) >= NEUTRAL_MIN_FRACTION:
        means = px[neutral].mean(axis=0)
        cap = (0.85, 1.20)                # trust real neutrals, but stay sane
    else:
        # No neutrals in scene (sunset, neon, forest): the cast is probably
        # intentional. Gray-world only as a hint, tightly capped.
        means = px.mean(axis=0)
        cap = (0.93, 1.08)

    means = np.clip(means, 1e-3, None)
    gray = float((means * _LUMA).sum())
    return np.clip(gray / means, *cap)


def compute_correction(frame: np.ndarray) -> Correction:
    """Analyze a display-space frame and derive its lighting correction."""
    px = frame.reshape(-1, 3).astype(np.float32)

    # 1. White balance, measured on the untouched source — a levels stretch
    #    amplifies chroma and would hide the scene's true neutrals.
    gains = _wb_from_neutrals(px)
    balanced = np.clip(px * gains, 0.0, 1.0)

    # 2. Levels — only when blacks/whites are clearly off (deadband).
    luma = balanced @ _LUMA
    p1, p99 = np.percentile(luma, [1.0, 99.0])
    if p1 <= BLACK_OK_BELOW and p99 >= WHITE_OK_ABOVE:
        scale, offset = 1.0, 0.0
    else:
        lo = min(float(p1), BLACK_OK_BELOW)
        hi = max(float(p99), WHITE_OK_ABOVE)
        spread = max(hi - lo, 1e-3)
        scale = float(np.clip((TARGET_WHITE - TARGET_BLACK) / spread, 0.9, 2.0))
        offset = float(TARGET_BLACK - lo * scale)

    leveled = np.clip(balanced * scale + offset, 1e-4, 1.0)

    # 3. Exposure — neutral gamma, only when midtones sit clearly wrong.
    mid = float(np.clip((leveled @ _LUMA).mean(), 0.05, 0.95))
    if MID_OK_RANGE[0] <= mid <= MID_OK_RANGE[1]:
        exp = 1.0
    else:
        exp = float(np.clip(np.log(TARGET_MID) / np.log(mid), 0.7, 1.5))

    return Correction(scale, offset, (float(gains[0]), float(gains[1]), float(gains[2])), exp)


def apply_correction(pixels: np.ndarray, c: Correction, strength: float = 1.0) -> np.ndarray:
    src = pixels.astype(np.float32, copy=False)
    out = src * np.asarray(c.wb_gains, dtype=np.float32)          # WB first
    out = out * np.float32(c.scale) + np.float32(c.offset)        # then levels
    out = np.clip(out, 0.0, 1.0) ** np.float32(c.gamma_exp)       # then exposure
    if strength < 1.0:
        out = src + (out - src) * np.float32(max(0.0, strength))
    return np.clip(out, 0.0, 1.0)
