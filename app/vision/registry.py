"""Build the configured VisionProvider from config; None → literal-match only."""

from __future__ import annotations

import shutil

from .cli_agent import CliAgentProvider
from .ollama_p import OllamaProvider
from .provider import VisionProvider


def build_provider(cfg: dict) -> VisionProvider | None:
    vision = cfg.get("vision", {})
    kind = vision.get("provider", "claude")

    if kind in ("none", "", None):
        return None
    if kind == "claude":
        binary = vision.get("claude_binary") or shutil.which("claude") or "claude"
        return CliAgentProvider.claude(binary)
    if kind == "cli":
        template = vision.get("command_template", "")
        if "{prompt}" not in template:
            return None
        return CliAgentProvider(template, name=vision.get("name", "cli"))
    if kind == "ollama":
        return OllamaProvider(
            model=vision.get("model", "llava"),
            base_url=vision.get("base_url", "http://localhost:11434"),
        )
    return None
