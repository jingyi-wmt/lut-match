"""Vision via authenticated CLI coding agents — no API keys needed.

The agent CLI (Claude Code, code puppy, gemini-cli, ...) is already logged in
on this machine; we shell out to it in headless mode with a prompt that names
the two image files on disk and demands strict JSON back.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from app.engine.recipe import GradingRecipe

from .provider import PROMPT_TEMPLATE, REPAIR_SUFFIX, VisionError, VisionProvider, parse_recipe

DEFAULT_TIMEOUT = 120


class CliAgentProvider(VisionProvider):
    """Runs `command_template` with {prompt} substituted (shlex-split).

    Presets:
      claude:  claude -p {prompt} --output-format json
      generic: any template from config, e.g. `code-puppy --headless {prompt}`
    """

    def __init__(self, command_template: str, name: str = "cli", timeout: int = DEFAULT_TIMEOUT):
        self.command_template = command_template
        self.name = name
        self.timeout = timeout

    @classmethod
    def claude(cls, binary: str = "claude") -> "CliAgentProvider":
        return cls(f"{binary} -p {{prompt}} --output-format json", name="claude")

    def extract_dna(self, ref_path: Path, frame_path: Path) -> GradingRecipe:
        prompt = PROMPT_TEMPLATE.format(ref_path=ref_path, frame_path=frame_path)
        try:
            return parse_recipe(self._run(prompt))
        except VisionError:
            return parse_recipe(self._run(prompt + REPAIR_SUFFIX))

    def _run(self, prompt: str) -> str:
        argv = [
            (part if part != "{prompt}" else prompt)
            for part in shlex.split(self.command_template)
        ]
        if prompt not in argv:
            raise VisionError(f"command template {self.command_template!r} lacks {{prompt}}")
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout
            )
        except FileNotFoundError as e:
            raise VisionError(f"CLI agent not found: {argv[0]!r} — check settings") from e
        except subprocess.TimeoutExpired as e:
            raise VisionError(f"CLI agent timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            raise VisionError(
                f"CLI agent exited {proc.returncode}: {proc.stderr.strip()[:400]}"
            )
        return _unwrap_agent_output(proc.stdout)


def _unwrap_agent_output(stdout: str) -> str:
    """Claude Code's --output-format json wraps the answer in an envelope;
    other CLIs print the answer directly. Handle both."""
    text = stdout.strip()
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(envelope, dict):
        for key in ("result", "content", "text", "response"):
            if isinstance(envelope.get(key), str):
                return envelope[key]
    return text
