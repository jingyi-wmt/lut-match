import base64
import json
from types import SimpleNamespace

import pytest

from app.premiere import PremiereError, _extract_png, _load_server_command

PNG = b"\x89PNG\r\n\x1a\nfakepngdata"


def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


class TestExtractPng:
    def test_image_content_block(self):
        result = SimpleNamespace(
            content=[block("image", data=base64.b64encode(PNG).decode())], isError=False
        )
        assert _extract_png(result) == PNG

    def test_base64_in_text_json(self):
        payload = json.dumps(
            {"success": True, "data": {"base64": base64.b64encode(PNG).decode()}}
        )
        result = SimpleNamespace(content=[block("text", text=payload)], isError=False)
        assert _extract_png(result) == PNG

    def test_no_active_sequence(self):
        result = SimpleNamespace(
            content=[block("text", text='{"success": false, "error": "No active sequence"}')],
            isError=True,
        )
        with pytest.raises(PremiereError, match="open a sequence"):
            _extract_png(result)

    def test_generic_error_surfaced(self):
        result = SimpleNamespace(
            content=[block("text", text='{"success": false, "error": "bridge offline"}')],
            isError=True,
        )
        with pytest.raises(PremiereError, match="bridge offline"):
            _extract_png(result)


class TestServerCommand:
    def test_missing_config(self, tmp_path):
        with pytest.raises(PremiereError):
            _load_server_command(tmp_path / "nope.json")

    def test_reads_command(self, tmp_path):
        p = tmp_path / ".mcp.json"
        p.write_text(json.dumps({"mcpServers": {"premiere": {"command": "/bin/node", "args": ["x.js"]}}}))
        cmd, args = _load_server_command(p)
        assert cmd == "/bin/node" and args == ["x.js"]
