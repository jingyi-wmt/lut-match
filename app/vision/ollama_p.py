"""Optional keyless local vision via Ollama (llava, qwen2.5-vl, ...)."""

from __future__ import annotations

import base64
from pathlib import Path

import httpx

from app.engine.recipe import GradingRecipe

from .provider import PROMPT_TEMPLATE, REPAIR_SUFFIX, VisionError, VisionProvider, parse_recipe


class OllamaProvider(VisionProvider):
    name = "ollama"

    def __init__(self, model: str = "llava", base_url: str = "http://localhost:11434", timeout: int = 300):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def extract_dna(self, ref_path: Path, frame_path: Path) -> GradingRecipe:
        prompt = PROMPT_TEMPLATE.format(
            ref_path="(first image)", frame_path="(second image)"
        )
        images = [
            base64.b64encode(Path(p).read_bytes()).decode()
            for p in (ref_path, frame_path)
        ]
        try:
            return parse_recipe(self._generate(prompt, images))
        except VisionError:
            return parse_recipe(self._generate(prompt + REPAIR_SUFFIX, images))

    def _generate(self, prompt: str, images: list[str]) -> str:
        try:
            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "images": images, "stream": False},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise VisionError(f"Ollama request failed: {e}") from e
        return resp.json().get("response", "")
