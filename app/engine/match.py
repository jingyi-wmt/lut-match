"""Literal statistical color match (secondary mode / vision fallback).

Monge-Kantorovich linear transfer: the affine map that carries the frame's
color distribution (mean + covariance) onto the reference's. Best when the
reference and footage share similar content; the DNA recipe mode is better
for pure vibe references.
"""

from __future__ import annotations

import numpy as np


def mkl_transform(frame: np.ndarray, reference: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (matrix A, offset b) so that graded = pixels @ A.T + b."""
    f = frame.reshape(-1, 3).astype(np.float64)
    r = reference.reshape(-1, 3).astype(np.float64)

    mu_f, mu_r = f.mean(axis=0), r.mean(axis=0)
    cov_f = np.cov(f, rowvar=False) + np.eye(3) * 1e-8
    cov_r = np.cov(r, rowvar=False) + np.eye(3) * 1e-8

    sqrt_f = _sqrtm(cov_f)
    inv_sqrt_f = np.linalg.inv(sqrt_f)
    middle = _sqrtm(sqrt_f @ cov_r @ sqrt_f)
    A = inv_sqrt_f @ middle @ inv_sqrt_f
    b = mu_r - A @ mu_f
    return A, b


def apply_match(pixels: np.ndarray, A: np.ndarray, b: np.ndarray, strength: float = 1.0) -> np.ndarray:
    src = pixels.astype(np.float32, copy=False)
    out = (pixels.reshape(-1, 3).astype(np.float64) @ A.T + b).reshape(pixels.shape)
    out = out.astype(np.float32)
    if strength < 1.0:
        out = src + (out - src) * np.float32(max(0.0, strength))
    return np.clip(out, 0.0, 1.0)


def _sqrtm(m: np.ndarray) -> np.ndarray:
    vals, vecs = np.linalg.eigh(m)
    vals = np.clip(vals, 0.0, None)
    return vecs @ np.diag(np.sqrt(vals)) @ vecs.T
