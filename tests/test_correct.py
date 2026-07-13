import numpy as np

from app.engine.correct import apply_correction, compute_correction
from app.engine.recipe import GradingRecipe
from app.engine.render import apply_recipe, soft_clip

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
        # Correction is deliberately conservative; a clear lift is enough.
        assert float((out @ _LUMA).mean()) > float((px @ _LUMA).mean()) + 0.05

    def test_color_cast_on_neutrals_gets_neutralized(self):
        # A real cast shows on the scene's neutrals. Mild global blue cast:
        # the neutrals still register as near-neutral and drive the WB.
        base = rng.normal(0.45, 0.02, (20000, 1)).astype(np.float32)
        px = np.clip(np.repeat(base, 3, axis=1), 0, 1)
        px[:, 2] *= 1.15  # blue cast on everything, including the grays
        px = np.clip(px, 0, 1)
        c = compute_correction(px)
        assert c.wb_gains[2] < 0.95  # blue pulled back toward neutral
        out = apply_correction(px, c)
        means_out = out.mean(axis=0)
        assert float(np.ptp(means_out)) < 0.5 * float(np.ptp(px.mean(axis=0)))

    def test_flat_frame_gets_stretched(self):
        px = np.clip(rng.normal(0.5, 0.06, (20000, 3)), 0, 1).astype(np.float32)
        c = compute_correction(px)
        out = apply_correction(px, c)
        assert float(np.std(out @ _LUMA)) > float(np.std(px @ _LUMA))

    def test_output_in_range(self):
        px = np.clip(rng.normal(0.2, 0.2, (5000, 3)), 0, 1).astype(np.float32)
        out = apply_correction(px, compute_correction(px))
        assert out.min() >= 0.0 and out.max() <= 1.0

    def test_deadband_full_range_frame_untouched(self):
        # Deep blacks, bright whites, mids in range: correction must be identity.
        luma_ramp = np.linspace(0.0, 1.0, 20000, dtype=np.float32)
        px = np.stack([luma_ramp] * 3, axis=1)
        c = compute_correction(px)
        assert c.scale == 1.0 and c.offset == 0.0 and c.gamma_exp == 1.0

    def test_sunset_cast_is_preserved(self):
        # Scene with no neutrals (strong warm gradient): WB stays conservative.
        n = 20000
        px = np.stack([
            rng.uniform(0.5, 0.95, n), rng.uniform(0.25, 0.55, n), rng.uniform(0.05, 0.3, n),
        ], axis=1).astype(np.float32)
        c = compute_correction(px)
        assert all(0.9 <= g <= 1.1 for g in c.wb_gains)  # cast mostly kept

    def test_neutral_patches_drive_wb(self):
        # Colorful scene WITH real gray patches carrying a blue cast:
        # WB should be measured off the grays and pull blue down.
        n = 20000
        colorful = np.stack([
            rng.uniform(0.6, 0.9, n), rng.uniform(0.1, 0.4, n), rng.uniform(0.1, 0.4, n),
        ], axis=1)
        gray_val = rng.uniform(0.3, 0.6, n // 4)
        grays = np.stack([gray_val, gray_val, np.clip(gray_val + 0.08, 0, 1)], axis=1)
        px = np.concatenate([colorful, grays]).astype(np.float32)
        c = compute_correction(px)
        assert c.wb_gains[2] < 0.97  # blue pulled down toward the grays' cast

    def test_strength_blends_toward_source(self):
        px = np.clip(rng.normal(0.15, 0.05, (5000, 3)), 0, 1).astype(np.float32)
        c = compute_correction(px)
        full = apply_correction(px, c, 1.0)
        half = apply_correction(px, c, 0.5)
        zero = apply_correction(px, c, 0.0)
        np.testing.assert_allclose(zero, px, atol=1e-5)
        np.testing.assert_allclose(half, (px + full) / 2, atol=1e-4)


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

    def test_bands_do_not_overlap_at_midgray(self):
        mid = np.full((10, 3), 0.5, dtype=np.float32)
        lifted = apply_recipe(mid, GradingRecipe(shadows=1.0))
        recovered = apply_recipe(mid, GradingRecipe(highlights=-1.0))
        np.testing.assert_allclose(lifted, mid, atol=1e-3)
        np.testing.assert_allclose(recovered, mid, atol=1e-3)


class TestSkinProtection:
    SKIN = np.array([[0.72, 0.55, 0.44]], dtype=np.float32)   # on the skin line
    BLUE = np.array([[0.2, 0.3, 0.7]], dtype=np.float32)

    def test_red_band_kill_spares_skin(self):
        recipe = GradingRecipe(hue_saturation=[0.0, 0.0, 1.0, 1.0, 1.0, 1.0], skin_protection=1.0)
        out = apply_recipe(self.SKIN, recipe)
        assert float(np.ptp(out)) > 0.15  # skin keeps most of its chroma

    def test_no_protection_drains_skin(self):
        recipe = GradingRecipe(hue_saturation=[0.0, 0.0, 1.0, 1.0, 1.0, 1.0], skin_protection=0.0)
        out = apply_recipe(self.SKIN, recipe)
        assert float(np.ptp(out)) < 0.05

    def test_protection_leaves_non_skin_alone(self):
        for protect in (0.0, 1.0):
            recipe = GradingRecipe(hue_saturation=[1.0, 1.0, 1.0, 1.0, 0.3, 1.0], skin_protection=protect)
            out = apply_recipe(self.BLUE, recipe)
            assert float(np.ptp(out)) < float(np.ptp(self.BLUE))  # blue still desaturated


class TestSoftClip:
    def test_identity_in_safe_range(self):
        img = rng.uniform(0.05, 0.95, (1000, 3)).astype(np.float32)
        np.testing.assert_allclose(soft_clip(img), img, atol=1e-6)

    def test_monotonic_and_bounded(self):
        ramp = np.linspace(-0.5, 1.5, 500, dtype=np.float32).reshape(-1, 1).repeat(3, axis=1)
        out = soft_clip(ramp)
        assert out.min() >= 0.0 and out.max() <= 1.0
        assert np.all(np.diff(out[:, 0]) >= -1e-7)

    def test_overshoot_keeps_gradation(self):
        a = soft_clip(np.array([[1.0, 1.0, 1.0]], dtype=np.float32))
        b = soft_clip(np.array([[1.05, 1.05, 1.05]], dtype=np.float32))
        assert float(b[0, 0]) > float(a[0, 0])  # not flattened to the same value
