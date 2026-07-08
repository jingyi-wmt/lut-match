"""Footage input transforms: bring log or Rec.709 stills into display space.

The LUT bakes this in, so a LUT built for S-Log3 footage expects S-Log3 input
in Premiere, applies the log→display conversion, then the grade.
"""

from __future__ import annotations

import numpy as np

FOOTAGE_TYPES = ("rec709", "slog3", "vlog", "clog3", "generic_log")

_DISPLAY_GAMMA = 1.0 / 2.4


def _encode_display(linear: np.ndarray) -> np.ndarray:
    """Scene-linear → display with a soft highlight rolloff (Reinhard) + gamma."""
    lin = np.clip(linear, 0.0, None)
    rolled = lin / (1.0 + 0.18 * lin)
    return np.clip(rolled, 0.0, 1.0) ** _DISPLAY_GAMMA


def to_display(img: np.ndarray, footage_type: str) -> np.ndarray:
    """Convert a [0,1] still (as encoded by the camera/NLE) to display space."""
    if footage_type not in FOOTAGE_TYPES:
        raise ValueError(f"unknown footage type {footage_type!r}; expected one of {FOOTAGE_TYPES}")
    if footage_type == "rec709":
        return img.astype(np.float32, copy=False)

    import colour  # deferred: heavy import

    x = img.astype(np.float64, copy=False)
    if footage_type == "slog3":
        linear = colour.models.log_decoding_SLog3(x)
    elif footage_type == "vlog":
        linear = colour.models.log_decoding_VLog(x)
    elif footage_type == "clog3":
        linear = colour.models.log_decoding_CanonLog3(x)
    else:  # generic_log — Cineon is a reasonable neutral stand-in
        linear = colour.models.log_decoding_Cineon(x)
    return _encode_display(linear).astype(np.float32)
