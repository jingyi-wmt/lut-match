import numpy as np
import pytest
from PIL import Image

from app.engine.io import downsample_for_analysis, load_image
from app.engine.logspace import FOOTAGE_TYPES, to_display


class TestLogspace:
    def test_rec709_is_identity(self):
        img = np.random.default_rng(1).random((64, 3), dtype=np.float32)
        np.testing.assert_array_equal(to_display(img, "rec709"), img)

    @pytest.mark.parametrize("ftype", [t for t in FOOTAGE_TYPES if t != "rec709"])
    def test_log_decode_monotonic_and_bounded(self, ftype):
        ramp = np.linspace(0.05, 0.9, 50, dtype=np.float32).reshape(-1, 1).repeat(3, axis=1)
        out = to_display(ramp, ftype)
        assert out.min() >= 0.0 and out.max() <= 1.0
        assert np.all(np.diff(out[:, 0]) >= -1e-6)  # monotonic

    def test_slog3_expands_contrast(self):
        # Log stills look flat; display conversion should spread the range.
        flat = np.array([[0.2, 0.2, 0.2], [0.6, 0.6, 0.6]], dtype=np.float32)
        out = to_display(flat, "slog3")
        assert (out[1, 0] - out[0, 0]) > (0.6 - 0.2) * 0.8

    def test_unknown_type_rejected(self):
        with pytest.raises(ValueError, match="unknown footage type"):
            to_display(np.zeros((2, 3), dtype=np.float32), "slog2000")


class TestIO:
    def test_load_and_warnings(self, tmp_path):
        arr = (np.random.default_rng(2).random((100, 100, 3)) * 255).astype(np.uint8)
        p = tmp_path / "small.png"
        Image.fromarray(arr).save(p)
        img = load_image(p)
        assert img.pixels.shape == (100, 100, 3)
        assert img.pixels.dtype == np.float32
        assert any("small" in w for w in img.warnings)

    def test_dark_warning(self, tmp_path):
        arr = np.full((600, 600, 3), 5, dtype=np.uint8)
        p = tmp_path / "dark.jpg"
        Image.fromarray(arr).save(p)
        img = load_image(p)
        assert any("dark" in w for w in img.warnings)

    def test_rejects_unknown_suffix(self, tmp_path):
        p = tmp_path / "clip.mov"
        p.write_bytes(b"nope")
        with pytest.raises(ValueError, match="unsupported image type"):
            load_image(p)

    def test_downsample(self):
        big = np.zeros((2000, 1000, 3), dtype=np.float32)
        small = downsample_for_analysis(big)
        assert max(small.shape[:2]) == 1024
