"""Vision provider contract: reference + frame images in, GradingRecipe out."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path

from app.engine.recipe import GradingRecipe


class VisionError(RuntimeError):
    """Raised when a provider cannot produce a usable recipe."""


class VisionProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def extract_dna(self, ref_path: Path, frame_path: Path) -> GradingRecipe: ...


PROMPT_TEMPLATE = """\
You are a senior film colorist. Two images are provided:
- REFERENCE (the look/vibe to replicate): {ref_path}
- FOOTAGE FRAME (already normalized to Rec.709 display space): {frame_path}

Look at both images. Extract the color-grading DNA of the REFERENCE — its \
white balance bias, black/mid/highlight behavior, contrast character, tone \
curve shape, split-toning, and saturation profile — and express it as \
adjustments that, applied to the FOOTAGE FRAME, would give it the reference's \
vibe. Account for what the frame already has (e.g. if the frame is already \
warm and the reference is warm, temperature should be near 0).

Respond with ONLY a JSON object, no markdown fences, matching exactly:
{{
  "look_description": "<2-3 sentences describing the reference look in colorist terms>",
  "temperature": <float -1..1, + warm>,
  "tint": <float -1..1, + magenta>,
  "lift": {{"r": <float>, "g": <float>, "b": <float>}},        // black offsets, each -0.15..0.15
  "gamma": {{"r": <float>, "g": <float>, "b": <float>}},       // midtone power, each 0.7..1.4
  "gain": {{"r": <float>, "g": <float>, "b": <float>}},        // highlight mult, each 0.7..1.4
  "contrast": <float 0.6..1.8>,
  "saturation": <float 0.2..1.8>,
  "hue_saturation": [<red>, <yellow>, <green>, <cyan>, <blue>, <magenta>],  // mults 0..2
  "tone_curve": [{{"x": <0..1>, "y": <0..1>}}, ...],           // 0-3 points, [] if none
  "split_tone": {{
    "shadow": {{"r": <float>, "g": <float>, "b": <float>}},     // each -0.15..0.15
    "highlight": {{"r": <float>, "g": <float>, "b": <float>}},
    "amount": <float 0..1>
  }}
}}
Be decisive: a distinctive reference deserves distinctive values, a neutral \
reference deserves near-identity values.
"""

REPAIR_SUFFIX = (
    "\n\nYour previous reply was not valid JSON for this schema. "
    "Reply again with ONLY the JSON object, nothing else."
)


def parse_recipe(raw_text: str) -> GradingRecipe:
    """Extract and validate a GradingRecipe from model output text."""
    text = raw_text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise VisionError(f"no JSON object in model output: {raw_text[:200]!r}")
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise VisionError(f"invalid JSON from model: {e}") from e
    try:
        return GradingRecipe.model_validate(data)
    except Exception as e:
        raise VisionError(f"JSON does not match GradingRecipe schema: {e}") from e
