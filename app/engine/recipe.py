"""GradingRecipe — the user-editable fine-tune layer applied after a match.

All fields have identity defaults, so a default-constructed recipe is a no-op.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Band centers for hue_saturation, in degrees (red, yellow, green, cyan, blue, magenta).
HUE_BAND_CENTERS = (0.0, 60.0, 120.0, 180.0, 240.0, 300.0)


class RGB(BaseModel):
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.r, self.g, self.b)


class CurvePoint(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class SplitTone(BaseModel):
    """Push shadows and highlights toward chosen hues."""

    shadow: RGB = RGB()      # additive color bias in shadows, each ~[-0.2, 0.2]
    highlight: RGB = RGB()   # additive color bias in highlights
    amount: float = Field(default=0.0, ge=0.0, le=1.0)


class GradingRecipe(BaseModel):
    """Identity defaults: applying a fresh GradingRecipe() changes nothing."""

    temperature: float = Field(default=0.0, ge=-1.0, le=1.0)  # + warm / - cool
    tint: float = Field(default=0.0, ge=-1.0, le=1.0)         # + magenta / - green

    lift: RGB = RGB()                                # black-level offsets, ~[-0.25, 0.25]
    gamma: RGB = RGB(r=1.0, g=1.0, b=1.0)            # midtone power, ~[0.5, 2.0]
    gain: RGB = RGB(r=1.0, g=1.0, b=1.0)             # highlight multipliers, ~[0.5, 2.0]

    contrast: float = Field(default=1.0, ge=0.25, le=3.0)     # around pivot 0.435
    saturation: float = Field(default=1.0, ge=0.0, le=3.0)

    shadows: float = Field(default=0.0, ge=-1.0, le=1.0)      # + lift / - crush shadow region
    highlights: float = Field(default=0.0, ge=-1.0, le=1.0)   # + boost / - recover highlights

    # Per-hue saturation multipliers, one per band centered on HUE_BAND_CENTERS
    # (red, yellow, green, cyan, blue, magenta).
    hue_saturation: list[float] = Field(default_factory=lambda: [1.0] * 6)

    tone_curve: list[CurvePoint] = Field(default_factory=list)  # luma curve, optional
    split_tone: SplitTone = SplitTone()

    # How strongly to shield skin tones from hue-sat and split-tone moves.
    # 0 = no protection, 1 = skin fully pinned. Identity-safe at any value.
    skin_protection: float = Field(default=0.7, ge=0.0, le=1.0)

    def model_post_init(self, __context) -> None:
        if len(self.hue_saturation) != 6:
            raise ValueError("hue_saturation must have exactly 6 values (R,Y,G,C,B,M)")
