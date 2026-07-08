"""Grab the frame at the Premiere playhead via the premiere-pro-mcp server.

Spawns the existing MCP server (stdio) that JZ already uses, calls its
`capture_frame` tool, and returns PNG bytes. Every failure mode maps to a
clear, user-facing message so the UI can point at the manual drop zone.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path

DEFAULT_MCP_JSON = Path("/Volumes/WMT_FY27/Library/AI/assistant-video-editor/.mcp.json")
TIMEOUT_S = 45


class PremiereError(RuntimeError):
    """User-facing failure reason for the Grab Frame flow."""


def _load_server_command(mcp_json_path: Path) -> tuple[str, list[str]]:
    if not mcp_json_path.exists():
        if not Path("/Volumes/WMT_FY27").exists():
            raise PremiereError(
                "The WMT_FY27 drive isn't mounted — connect it, or drop a still frame manually."
            )
        raise PremiereError(f"Premiere MCP config not found at {mcp_json_path}.")
    try:
        cfg = json.loads(mcp_json_path.read_text())
        server = cfg["mcpServers"]["premiere"]
        return server["command"], server.get("args", [])
    except (json.JSONDecodeError, KeyError) as e:
        raise PremiereError(f"Could not read premiere server from {mcp_json_path}: {e}") from e


async def _capture_async(mcp_json_path: Path) -> bytes:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command, args = _load_server_command(mcp_json_path)
    if not Path(command).exists():
        raise PremiereError(f"Premiere MCP launcher missing: {command}")

    params = StdioServerParameters(command=command, args=args)
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await asyncio.wait_for(
                    session.call_tool("capture_frame", {}), timeout=TIMEOUT_S
                )
    except asyncio.TimeoutError:
        raise PremiereError(
            "Premiere didn't answer in time. Is Premiere open with the MCP Bridge "
            "panel started (Window → Extensions → MCP Bridge → Start Bridge)?"
        ) from None
    except PremiereError:
        raise
    except Exception as e:
        raise PremiereError(f"Could not talk to the Premiere MCP server: {e}") from e

    return _extract_png(result)


def _extract_png(result) -> bytes:
    texts = []
    for block in result.content:
        if getattr(block, "type", "") == "image" and getattr(block, "data", None):
            return base64.b64decode(block.data)
        if getattr(block, "type", "") == "text":
            texts.append(block.text)

    joined = "\n".join(texts)
    m = re.search(r'"base64"\s*:\s*"([A-Za-z0-9+/=]+)"', joined)
    if m:
        return base64.b64decode(m.group(1))

    lowered = joined.lower()
    if "no active sequence" in lowered:
        raise PremiereError("No active sequence in Premiere — open a sequence first.")
    if "error" in lowered or getattr(result, "isError", False):
        raise PremiereError(f"Premiere reported: {joined[:300]}")
    raise PremiereError("Premiere responded but no frame image came back.")


def capture_frame(mcp_json_path: Path = DEFAULT_MCP_JSON) -> bytes:
    """Synchronous wrapper: returns PNG bytes of the frame at the playhead."""
    return asyncio.run(_capture_async(mcp_json_path))
