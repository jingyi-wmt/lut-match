import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import server
from app.engine.lut import apply_cube, read_cube
from app.engine.recipe import GradingRecipe


@pytest.fixture()
def client(monkeypatch, tmp_path):
    server.S.reference = None
    server.S.frame = None
    server.S.tweaks = GradingRecipe()
    server.S.correction = None
    server.S.correction_strength = 1.0
    server.S.auto_correct = True
    server.S.keep_luma = False
    server.S.match_transforms = None
    server.S.footage_type = "rec709"
    server.S.warnings = {}
    server.S.reference_name = "reference"
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    return TestClient(server.app)


def png_bytes(color, size=(640, 640)):
    arr = np.zeros((*size, 3), dtype=np.uint8)
    arr[..., :] = color
    rng = np.random.default_rng(7)
    arr = np.clip(arr + rng.integers(-30, 30, arr.shape), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def upload(client, kind, color):
    return client.post(
        f"/upload/{kind}", files={"file": (f"{kind}.png", png_bytes(color), "image/png")}
    )


def analyze(client, **kw):
    return client.post("/analyze", json={"footage_type": "rec709", **kw})


class TestFlow:
    def test_full_flow_and_export(self, client):
        assert upload(client, "reference", (200, 120, 60)).status_code == 200
        assert upload(client, "frame", (100, 110, 140)).status_code == 200

        out = analyze(client).json()
        assert "correction_summary" in out

        prev = client.get("/preview", params={"strength": 1.0})
        assert prev.status_code == 200 and prev.headers["content-type"] == "image/jpeg"

        resp = client.get("/export", params={"strength": 1.0})
        assert resp.status_code == 200
        assert "reference-match.cube" in resp.headers["content-disposition"]
        assert "LUT_3D_SIZE 33" in resp.content.decode().splitlines()[1]

    def test_export_lut_matches_preview_math(self, client, tmp_path):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        analyze(client, auto_correct=False)

        cube_path = tmp_path / "out.cube"
        cube_path.write_bytes(client.get("/export").content)
        table = read_cube(cube_path)

        frame = server.S.frame
        direct = server._grade(frame, 1.0)
        via_lut = apply_cube(table, frame)
        assert float(np.abs(via_lut - direct).mean()) < 0.01

    def test_analyze_requires_both_images(self, client):
        upload(client, "reference", (200, 120, 60))
        r = analyze(client)
        assert r.status_code == 400 and "frame" in r.json()["detail"]

    def test_bad_footage_type(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        assert client.post("/analyze", json={"footage_type": "slog99"}).status_code == 400

    def test_log_footage_export(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (110, 110, 110))
        analyze(client, footage_type="slog3")
        assert client.get("/export").status_code == 200

    def test_status(self, client):
        st = client.get("/status").json()
        assert st["reference"] is False and st["ready"] is False


class TestCorrectionUX:
    def test_dark_frame_reports_fix(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (40, 42, 60))
        out = analyze(client, auto_correct=True).json()
        assert out["correction_summary"].startswith("fixing:")

    def test_live_toggle_changes_preview(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (40, 42, 60))  # dark frame → correction is non-identity
        analyze(client, auto_correct=True)
        with_corr = client.get("/preview").content
        out = client.post("/options", json={"auto_correct": False}).json()
        assert out["correction_summary"] == "auto-correction off"
        without_corr = client.get("/preview").content
        assert with_corr != without_corr

    def test_correction_strength_live(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (40, 42, 60))
        analyze(client, auto_correct=True)
        full = client.get("/preview").content
        client.post("/options", json={"correction_strength": 0.2})
        partial = client.get("/preview").content
        assert full != partial

    def test_auto_correct_off_no_correction(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        out = analyze(client, auto_correct=False).json()
        assert out["correction_summary"] == "auto-correction off"
        assert server.S.correction is None


class TestPanelEndpoints:
    def test_cors_headers_present(self, client):
        r = client.get("/status", headers={"Origin": "null"})
        assert r.headers.get("access-control-allow-origin") == "*"

    def test_content_disposition_exposed_for_cross_origin_fetch(self, client):
        # The CEP panel shell (file:// origin) needs to read this header off
        # a cross-origin fetch to /export to pick a suggested filename; by
        # default Content-Disposition isn't CORS-exposed to script.
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        analyze(client, auto_correct=False)
        r = client.get("/export", headers={"Origin": "null"})
        assert "content-disposition" in r.headers.get("access-control-expose-headers", "").lower()


class TestOptionsAndTweaks:
    def test_keep_luma_changes_preview(self, client):
        upload(client, "reference", (230, 160, 60))
        upload(client, "frame", (60, 80, 120))
        analyze(client, auto_correct=False)
        normal = client.get("/preview").content
        client.post("/options", json={"keep_luma": True})
        kept = client.get("/preview").content
        assert normal != kept

    def test_tweaks_layer_applies(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        analyze(client, auto_correct=False)
        base = client.get("/preview").content
        r = client.post("/tweaks", json={"recipe": {"shadows": 0.8, "highlights": -0.5}})
        assert r.status_code == 200
        assert client.get("/preview").content != base
