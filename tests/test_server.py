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
    # Fresh session and no vision provider (deterministic literal-match path).
    server.S.__dict__.clear()
    server.S.reference = None
    server.S.frame = None
    server.S.recipe = None
    server.S.tweaks = GradingRecipe()
    server.S.correction = None
    server.S.match_params = None
    server.S.footage_type = "rec709"
    server.S.warnings = {}
    server.S.reference_name = "reference"
    monkeypatch.setattr(server, "build_provider", lambda cfg: None)
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


class TestFlow:
    def test_full_match_flow_and_export(self, client):
        assert upload(client, "reference", (200, 120, 60)).status_code == 200  # warm ref
        assert upload(client, "frame", (100, 110, 140)).status_code == 200     # cool frame

        out = client.post("/analyze", json={"footage_type": "rec709"}).json()
        assert out["mode"] == "match"  # no provider configured in tests

        prev = client.get("/preview", params={"strength": 1.0, "mode": "match"})
        assert prev.status_code == 200 and prev.headers["content-type"] == "image/jpeg"

        resp = client.get("/export", params={"strength": 1.0, "mode": "match"})
        assert resp.status_code == 200
        assert "reference-match.cube" in resp.headers["content-disposition"]
        lines = resp.content.decode().splitlines()
        assert "LUT_3D_SIZE 33" in lines[1]

    def test_export_lut_matches_preview_math(self, client, tmp_path):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        client.post("/analyze", json={"footage_type": "rec709", "auto_correct": False})

        cube_path = tmp_path / "out.cube"
        cube_path.write_bytes(client.get("/export", params={"mode": "match"}).content)
        table = read_cube(cube_path)

        frame = server.S.frame
        from app.engine.match import apply_match
        A, b = server.S.match_params
        direct = apply_match(frame, A, b, 1.0)
        via_lut = apply_cube(table, frame)
        assert float(np.abs(via_lut - direct).mean()) < 0.01

    def test_dna_mode_via_recipe_endpoint(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        client.post("/analyze", json={"footage_type": "rec709"})
        recipe = GradingRecipe(temperature=0.4, contrast=1.3)
        r = client.post("/recipe", json={"recipe": recipe.model_dump()})
        assert r.status_code == 200
        assert client.get("/preview", params={"mode": "dna"}).status_code == 200
        assert client.get("/export", params={"mode": "dna"}).status_code == 200

    def test_analyze_requires_both_images(self, client):
        upload(client, "reference", (200, 120, 60))
        r = client.post("/analyze", json={"footage_type": "rec709"})
        assert r.status_code == 400 and "frame" in r.json()["detail"]

    def test_bad_footage_type(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        assert client.post("/analyze", json={"footage_type": "slog99"}).status_code == 400

    def test_log_footage_export(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (110, 110, 110))
        client.post("/analyze", json={"footage_type": "slog3"})
        assert client.get("/export", params={"mode": "match"}).status_code == 200

    def test_status(self, client):
        st = client.get("/status").json()
        assert st["reference"] is False and st["provider"] is None

    def test_auto_correct_fixes_dark_frame(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (40, 42, 60))  # underexposed, blue-cast frame
        out = client.post("/analyze", json={"footage_type": "rec709", "auto_correct": True}).json()
        assert out["mode"] == "match"
        assert server.S.correction is not None and not server.S.correction.is_identity()

    def test_auto_correct_off(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        client.post("/analyze", json={"footage_type": "rec709", "auto_correct": False})
        assert server.S.correction is None

    def test_tweaks_layer_applies_in_match_mode(self, client):
        upload(client, "reference", (200, 120, 60))
        upload(client, "frame", (100, 110, 140))
        client.post("/analyze", json={"footage_type": "rec709", "auto_correct": False})
        base = client.get("/preview", params={"mode": "match"}).content
        r = client.post("/tweaks", json={"recipe": {"shadows": 0.8, "highlights": -0.5}})
        assert r.status_code == 200
        tweaked = client.get("/preview", params={"mode": "match"}).content
        assert tweaked != base
