import numpy as np

from app.engine.correct import apply_correction, compute_correction
from app.engine.recipe import GradingRecipe
from app.engine.render import apply_recipe

_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
rng = np.random.default_rng(9)


class TestCorrection:
    def test_well_exposed_frame_is_near_identity(self):
        # Full-range, neutral frame: correction should barely touch it.
        px = rng.random((20000, 3), dtype=np.float32)
        c = compute_correction(px)
        out = apply_correction(px, c)
        assert abs(float(out.mean()) - float(px.mean())) < 0.08

    def test_dark_frame_gets_brightened(self):
        px = np.clip(rng.normal(0.15, 0.05, (20000, 3)), 0, 1).astype(np.float32)
        c = compute_correction(px)
        out = apply_correction(px, c)
        assert float((out @ _LUMA).mean()) > float((px @ _LUMA).mean()) + 0.1

    def test_color_cast_gets_neutralized(self):
        px = np.clip(rng.normal(0.45, 0.1, (20000, 3)), 0, 1).astype(np.float32)
        px[:, 2] *= 1.3  # blue cast
        px = np.clip(px, 0, 1)
        c = compute_correction(px)
        out = apply_correction(px, c)
        means_in = px.mean(axis=0)
        means_out = out.mean(axis=0)
        assert float(np.ptp(means_out)) < float(np.ptp(means_in))

    def test_flat_frame_gets_stretched(self):
        px = np.clip(rng.normal(0.5, 0.06, (20000, 3)), 0, 1).astype(np.float32)
        c = compute_correction(px)
        out = apply_correction(px, c)
        assert float(np.std(out @ _LUMA)) > float(np.std(px @ _LUMA))

    def test_output_in_range(self):
        px = np.clip(rng.normal(0.2, 0.2, (5000, 3)), 0, 1).astype(np.float32)
        out = apply_correction(px, compute_correction(px))
        assert out.min() >= 0.0 and out.max() <= 1.0


class TestShadowsHighlights:
    def test_shadows_slider_lifts_darks_only(self):
        darks = np.full((10, 3), 0.08, dtype=np.float32)
        brights = np.full((10, 3), 0.92, dtype=np.float32)
        recipe = GradingRecipe(shadows=1.0)
        assert float(apply_recipe(darks, recipe).mean()) > 0.2
        assert abs(float(apply_recipe(brights, recipe).mean()) - 0.92) < 0.02

    def test_highlights_slider_recovers_brights_only(self):
        darks = np.full((10, 3), 0.08, dtype=np.float32)
        brights = np.full((10, 3), 0.92, dtype=np.float32)
        recipe = GradingRecipe(highlights=-1.0)
        assert float(apply_recipe(brights, recipe).mean()) < 0.75
        assert abs(float(apply_recipe(darks, recipe).mean()) - 0.08) < 0.02

    def test_zero_sliders_are_noop(self):
        img = rng.random((1000, 3), dtype=np.float32)
        out = apply_recipe(img, GradingRecipe(shadows=0.0, highlights=0.0))
        np.testing.assert_allclose(out, img, atol=1e-5)
