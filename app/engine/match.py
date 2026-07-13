"""Statistical color match — the app's core look-transfer engine.

Band-wise Monge-Kantorovich linear transfer: shadows, midtones and highlights
each get their own affine map carrying the frame's color distribution onto the
reference's, blended smoothly by each pixel's luma. This captures tonally
split looks (warm highlights / cool shadows) that a single global transform
cannot, while remaining a pure function of input color — so it bakes into a
LUT.

`keep_luma` re-imposes the source brightness after matching: the reference's
palette transfers, the footage's exposure structure stays.
"""

from __future__ import annotations

import numpy as np

_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _band_weights(luma: np.ndarray) -> np.ndarray:
    """(..., 3) smooth shadow/mid/highlight weights summing to 1."""
    shadow = np.clip(1.0 - luma / 0.5, 0.0, 1.0) ** 2
    highlight = np.clip((luma - 0.5) / 0.5, 0.0, 1.0) ** 2
    mid = np.clip(1.0 - shadow - highlight, 0.0, 1.0)
    w = np.stack([shadow, mid, highlight], axis=-1)
    return w / np.maximum(w.sum(axis=-1, keepdims=True), 1e-6)


def _weighted_stats(px: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    wsum = max(float(w.sum()), 1e-6)
    mu = (px * w[:, None]).sum(axis=0) / wsum
    d = px - mu
    cov = (d * w[:, None]).T @ d / wsum
    return mu, cov + np.eye(3) * 1e-6


def _sqrtm(m: np.ndarray) -> np.ndarray:
    vals, vecs = np.linalg.eigh(m)
    vals = np.clip(vals, 0.0, None)
    return vecs @ np.diag(np.sqrt(vals)) @ vecs.T


def _mkl(mu_f, cov_f, mu_r, cov_r) -> tuple[np.ndarray, np.ndarray]:
    sqrt_f = _sqrtm(cov_f)
    inv_sqrt_f = np.linalg.inv(sqrt_f + np.eye(3) * 1e-8)
    A = inv_sqrt_f @ _sqrtm(sqrt_f @ cov_r @ sqrt_f) @ inv_sqrt_f
    b = mu_r - A @ mu_f
    return A, b


def banded_mkl_transform(
    frame: np.ndarray, reference: np.ndarray
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Per-band (A, b) transforms: [shadows, mids, highlights]."""
    f = frame.reshape(-1, 3).astype(np.float64)
    r = reference.reshape(-1, 3).astype(np.float64)
    fw = _band_weights(f @ _LUMA)
    rw = _band_weights(r @ _LUMA)

    transforms = []
    for band in range(3):
        mu_f, cov_f = _weighted_stats(f, fw[:, band])
        mu_r, cov_r = _weighted_stats(r, rw[:, band])
        transforms.append(_mkl(mu_f, cov_f, mu_r, cov_r))
    return transforms


def apply_banded_match(
    pixels: np.ndarray,
    transforms: list[tuple[np.ndarray, np.ndarray]],
    strength: float = 1.0,
    keep_luma: bool = False,
) -> np.ndarray:
    src = pixels.astype(np.float32, copy=False)
    flat = pixels.reshape(-1, 3).astype(np.float64)
    w = _band_weights(flat @ _LUMA)

    out = np.zeros_like(flat)
    for band, (A, b) in enumerate(transforms):
        out += w[:, band : band + 1] * (flat @ A.T + b)
    out = out.reshape(pixels.shape).astype(np.float32)

    if keep_luma:
        delta = (src @ _LUMA) - (out @ _LUMA)
        out = out + delta[..., None]

    if strength < 1.0:
        out = src + (out - src) * np.float32(max(0.0, strength))
    return np.clip(out, 0.0, 1.0)