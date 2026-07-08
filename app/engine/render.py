"""Apply a GradingRecipe to an image.

Single source of truth for the grade: the live preview and the baked LUT both
run pixels through `apply_recipe`, so what you see is exactly what exports.

All operations work on float32 RGB arrays of shape (..., 3) in [0, 1] display
space (Rec.709-ish). Out-of-range intermediates are allowed; the final result
is clamped by the caller (preview/LUT bake).
"""

from __future__ import annotations

import numpy as np

from .recipe import HUE_BAND_CENTERS, GradingRecipe

_CONTRAST_PIVOT = 0.435
_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

# Skin-tone line: the narrow hue band all human skin sits along (vectorscope
# convention), regardless of complexion. Effects near it get attenuated.
_SKIN_HUE_CENTER = 25.0
_SKIN_HUE_HALFWIDTH = 22.0


def _hue_degrees(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel (hue in degrees, chroma) — chroma is max-min."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mx = np.max(img, axis=-1)
    mn = np.min(img, axis=-1)
    diff = mx - mn
    hue = np.zeros_like(mx)
    mask = diff > 1e-6
    rm = mask & (mx == r)
    gm = mask & (mx == g) & ~rm
    bm = mask & ~rm & ~gm
    hue[rm] = (60.0 * ((g - b)[rm] / diff[rm])) % 360.0
    hue[gm] = 60.0 * ((b - r)[gm] / diff[gm]) + 120.0
    hue[bm] = 60.0 * ((r - g)[bm] / diff[bm]) + 240.0
    return hue, diff


def _skin_weight(img: np.ndarray) -> np.ndarray:
    """1.0 where a color is plausibly skin, falling to 0 away from it."""
    hue, chroma = _hue_degrees(img)
    luma = img @ _LUMA
    hdist = np.abs(hue - _SKIN_HUE_CENTER)
    hdist = np.minimum(hdist, 360.0 - hdist)
    hue_w = np.clip(1.0 - hdist / _SKIN_HUE_HALFWIDTH, 0.0, 1.0)
    sat_w = np.clip(1.0 - np.abs(chroma - 0.25) / 0.25, 0.0, 1.0)   # skin chroma band
    luma_w = np.clip((luma - 0.12) / 0.1, 0.0, 1.0) * np.clip((0.95 - luma) / 0.1, 0.0, 1.0)
    return hue_w * sat_w * luma_w


def soft_clip(img: np.ndarray) -> np.ndarray:
    """Final-stage clamp with a soft knee: identity inside [0.03, 0.97],
    smooth rolloff outside, so pushed highlights/shadows compress instead of
    clipping flat. Monotonic and bounded to [0, 1]."""
    out = img.astype(np.float32, copy=True)
    hi = out > 0.97
    out[hi] = 0.97 + 0.03 * np.tanh((out[hi] - 0.97) / 0.03)
    lo = out < 0.03
    out[lo] = 0.03 - 0.03 * np.tanh((0.03 - out[lo]) / 0.03)
    return np.clip(out, 0.0, 1.0)


def _white_balance(img: np.ndarray, temperature: float, tint: float) -> np.ndarray:
    # Simple channel-gain model: warm raises R / lowers B; magenta tint lowers G.
    r_gain = 1.0 + 0.25 * temperature - 0.05 * tint
    g_gain = 1.0 - 0.12 * tint
    b_gain = 1.0 - 0.25 * temperature - 0.05 * tint
    return img * np.array([r_gain, g_gain, b_gain], dtype=np.float32)


def _lift_gamma_gain(img: np.ndarray, recipe: GradingRecipe) -> np.ndarray:
    lift = np.array(recipe.lift.as_tuple(), dtype=np.float32)
    gamma = np.clip(np.array(recipe.gamma.as_tuple(), dtype=np.float32), 0.1, 5.0)
    gain = np.array(recipe.gain.as_tuple(), dtype=np.float32)
    out = img + lift * (1.0 - img)          # lift raises blacks, leaves whites
    out = np.clip(out, 0.0, None)
    out = out ** (1.0 / gamma)
    return out * gain


def _contrast(img: np.ndarray, contrast: float) -> np.ndarray:
    return (img - _CONTRAST_PIVOT) * contrast + _CONTRAST_PIVOT


def _tone_curve(img: np.ndarray, recipe: GradingRecipe) -> np.ndarray:
    if not recipe.tone_curve:
        return img
    pts = sorted(recipe.tone_curve, key=lambda p: p.x)
    xs = [0.0] + [p.x for p in pts] + [1.0]
    ys = [0.0] + [p.y for p in pts] + [1.0]
    # Curve is defined on luma; apply the same delta to all channels so hue holds.
    luma = np.clip(img @ _LUMA, 0.0, 1.0)
    delta = np.interp(luma, xs, ys).astype(np.float32) - luma
    return img + delta[..., None]


def _shadows_highlights(img: np.ndarray, recipe: GradingRecipe) -> np.ndarray:
    if recipe.shadows == 0.0 and recipe.highlights == 0.0:
        return img
    luma = np.clip(img @ _LUMA, 0.0, 1.0)
    # Separated bands: shadows die out by mid-gray, highlights start there,
    # so the two sliders don't fight over the midtones.
    shadow_w = (np.clip(1.0 - luma / 0.5, 0.0, 1.0) ** 2)[..., None]
    highlight_w = (np.clip((luma - 0.5) / 0.5, 0.0, 1.0) ** 2)[..., None]
    return img + 0.25 * (recipe.shadows * shadow_w + recipe.highlights * highlight_w)


def _split_tone(img: np.ndarray, recipe: GradingRecipe) -> np.ndarray:
    st = recipe.split_tone
    if st.amount <= 0.0:
        return img
    luma = np.clip(img @ _LUMA, 0.0, 1.0)
    shadow_w = ((1.0 - luma) ** 2)[..., None]
    highlight_w = (luma ** 2)[..., None]
    shadow = np.array(st.shadow.as_tuple(), dtype=np.float32)
    highlight = np.array(st.highlight.as_tuple(), dtype=np.float32)
    bias = st.amount * (shadow_w * shadow + highlight_w * highlight)
    if recipe.skin_protection > 0.0:
        keep = 1.0 - recipe.skin_protection * _skin_weight(img)[..., None]
        bias = bias * keep
    return img + bias


def _saturation(img: np.ndarray, recipe: GradingRecipe) -> np.ndarray:
    luma = (img @ _LUMA)[..., None]
    chroma = img - luma

    hue_sat = np.asarray(recipe.hue_saturation, dtype=np.float32)
    if np.allclose(hue_sat, 1.0):
        sat = np.float32(recipe.saturation)
        return luma + chroma * sat

    # Per-hue: weight each pixel's sat multiplier by proximity to band centers.
    hue, _ = _hue_degrees(img)
    centers = np.asarray(HUE_BAND_CENTERS, dtype=np.float32)
    dist = np.abs(hue[..., None] - centers)
    dist = np.minimum(dist, 360.0 - dist)                      # circular distance
    weights = np.clip(1.0 - dist / 60.0, 0.0, None)            # triangular, 60° halfwidth
    weights /= np.maximum(weights.sum(axis=-1, keepdims=True), 1e-6)
    per_pixel_sat = (weights * hue_sat).sum(axis=-1)

    if recipe.skin_protection > 0.0:
        # Pull skin-hued pixels' multiplier back toward neutral so red/yellow
        # band moves can't drain or fry faces.
        skin_w = recipe.skin_protection * _skin_weight(img)
        per_pixel_sat = per_pixel_sat + (1.0 - per_pixel_sat) * skin_w

    sat = (recipe.saturation * per_pixel_sat)[..., None]
    return luma + chroma * sat


def apply_recipe(img: np.ndarray, recipe: GradingRecipe, strength: float = 1.0) -> np.ndarray:
    """Grade `img` (float32, (...,3), [0,1]) with `recipe`.

    strength blends linearly between the input (0.0) and the full grade (1.0).
    Result is clamped to [0, 1].
    """
    src = img.astype(np.float32, copy=False)
    out = _white_balance(src, recipe.temperature, recipe.tint)
    out = _lift_gamma_gain(out, recipe)
    out = _contrast(out, recipe.contrast)
    out = _shadows_highlights(out, recipe)
    out = _tone_curve(out, recipe)
    out = _split_tone(out, recipe)
    out = _saturation(out, recipe)
    if strength < 1.0:
        out = src + (out - src) * np.float32(max(0.0, strength))
    return np.clip(out, 0.0, 1.0)
