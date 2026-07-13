import numpy as np
import pytest

from app.engine.lut import apply_cube, bake_lut, read_cube, write_cube
from app.engine.match import apply_banded_match, banded_mkl_transform
from app.engine.recipe import RGB, CurvePoint, GradingRecipe, SplitTone
from app.engine.render import apply_recipe

rng = np.random.default_rng(42)


def random_image(n=4096):
    return rng.random((n, 3), dtype=np.float32)


class TestRecipeRender:
    def test_identity_recipe_is_noop(self):
        img = random_image()
        out = apply_recipe(img, GradingRecipe())
        np.testing.assert_allclose(out, img, atol=1e-5)

    def test_strength_zero_is_noop(self):
        img = random_image()
        recipe = GradingRecipe(temperature=0.8, contrast=1.5, saturation=0.3)
        out = apply_recipe(img, recipe, strength=0.0)
        np.testing.assert_allclose(out, img, atol=1e-5)

    def test_warm_temperature_raises_red_lowers_blue(self):
        img = np.full((100, 3), 0.5, dtype=np.float32)
        out = apply_recipe(img, GradingRecipe(temperature=0.5))
        assert out[0, 0] > 0.5 and out[0, 2] < 0.5

    def test_lift_raises_blacks_not_whites(self):
        img = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]], dtype=np.float32)
        recipe = GradingRecipe(lift=RGB(r=0.1, g=0.1, b=0.1))
        out = apply_recipe(img, recipe)
        assert np.all(out[0] > 0.05)
        np.testing.assert_allclose(out[1], 1.0, atol=1e-4)

    def test_contrast_spreads_around_pivot(self):
        img = np.array([[0.2, 0.2, 0.2], [0.7, 0.7, 0.7]], dtype=np.float32)
        out = apply_recipe(img, GradingRecipe(contrast=1.5))
        assert out[0, 0] < 0.2 and out[1, 0] > 0.7

    def test_desaturation_moves_toward_luma(self):
        img = np.array([[0.9, 0.2, 0.1]], dtype=np.float32)
        out = apply_recipe(img, GradingRecipe(saturation=0.0))
        assert np.ptp(out) < 1e-4  # fully desaturated → gray

    def test_per_hue_saturation_targets_band(self):
        red = np.array([[0.8, 0.15, 0.15]], dtype=np.float32)
        blue = np.array([[0.15, 0.15, 0.8]], dtype=np.float32)
        recipe = GradingRecipe(hue_saturation=[0.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        red_out = apply_recipe(red, recipe)
        blue_out = apply_recipe(blue, recipe)
        assert np.ptp(red_out) < 0.02          # red band desaturated
        assert np.ptp(blue_out) > 0.4          # blue band untouched

    def test_tone_curve_lifts_midtones(self):
        img = np.full((10, 3), 0.4, dtype=np.float32)
        recipe = GradingRecipe(tone_curve=[CurvePoint(x=0.4, y=0.55)])
        out = apply_recipe(img, recipe)
        assert np.all(out > 0.5)

    def test_split_tone_warms_highlights_cools_shadows(self):
        shadows = np.full((5, 3), 0.08, dtype=np.float32)
        highlights = np.full((5, 3), 0.92, dtype=np.float32)
        recipe = GradingRecipe(
            split_tone=SplitTone(shadow=RGB(b=0.15), highlight=RGB(r=0.15), amount=1.0)
        )
        s, h = apply_recipe(shadows, recipe), apply_recipe(highlights, recipe)
        assert s[0, 2] > s[0, 0]  # blue-pushed shadows
        assert h[0, 0] > h[0, 2]  # red-pushed highlights

    def test_output_always_in_range(self):
        img = random_image()
        recipe = GradingRecipe(
            temperature=1.0, tint=-1.0, contrast=3.0, saturation=3.0,
            gain=RGB(r=2.0, g=0.5, b=2.0), lift=RGB(r=-0.25, g=0.25, b=0.0),
        )
        out = apply_recipe(img, recipe)
        assert out.min() >= 0.0 and out.max() <= 1.0

    def test_bad_hue_saturation_length_rejected(self):
        with pytest.raises(Exception):
            GradingRecipe(hue_saturation=[1.0, 1.0])


class TestBandedMatch:
    def test_banded_self_match_is_near_identity(self):
        img = random_image(20000)
        transforms = banded_mkl_transform(img, img)
        out = apply_banded_match(img, transforms)
        np.testing.assert_allclose(out, img, atol=0.02)

    def test_split_tone_reference_shifts_bands_oppositely(self):
        # Reference: warm highlights, cool shadows. A global transform can't
        # push the frame's shadows and highlights in opposite directions.
        n = 20000
        luma = rng.uniform(0, 1, n).astype(np.float32)
        warm_cool = np.stack([
            0.25 + 0.7 * luma + 0.15 * luma,          # red rises with luma
            0.25 + 0.7 * luma,
            0.25 + 0.7 * luma + 0.15 * (1 - luma),    # blue rises in shadows
        ], axis=1)
        ref = np.clip(warm_cool + rng.normal(0, 0.02, (n, 3)), 0, 1).astype(np.float32)

        gray = np.clip(
            np.repeat(rng.uniform(0.05, 0.95, (n, 1)), 3, axis=1)
            + rng.normal(0, 0.01, (n, 3)), 0, 1
        ).astype(np.float32)

        transforms = banded_mkl_transform(gray, ref)
        out = apply_banded_match(gray, transforms)
        l = gray @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        shadows = out[l < 0.25]
        highlights = out[l > 0.75]
        assert float((shadows[:, 2] - shadows[:, 0]).mean()) > 0.02   # blue-shifted shadows
        assert float((highlights[:, 0] - highlights[:, 2]).mean()) > 0.02  # warm highlights

    def test_keep_luma_preserves_brightness(self):
        frame = random_image(20000)
        ref = np.clip(rng.normal(0.7, 0.1, (20000, 3)), 0, 1).astype(np.float32)  # much brighter ref
        transforms = banded_mkl_transform(frame, ref)
        out = apply_banded_match(frame, transforms, keep_luma=True)
        luma_vec = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        # Compare away from the clip boundaries, where luma is exactly preserved.
        inner = (frame @ luma_vec > 0.2) & (frame @ luma_vec < 0.8)
        np.testing.assert_allclose(
            (out @ luma_vec)[inner], (frame @ luma_vec)[inner], atol=0.05
        )

    def test_strength_zero_is_noop(self):
        frame = random_image(5000)
        ref = np.clip(rng.normal(0.7, 0.2, (5000, 3)), 0, 1).astype(np.float32)
        transforms = banded_mkl_transform(frame, ref)
        out = apply_banded_match(frame, transforms, strength=0.0)
        np.testing.assert_allclose(out, frame, atol=1e-5)


class TestLut:
    def test_identity_lut_round_trips(self, tmp_path):
        table = bake_lut(lambda x: x)
        path = write_cube(table, tmp_path / "identity.cube")
        loaded = read_cube(path)
        np.testing.assert_allclose(loaded, table, atol=1e-5)
        img = random_image()
        np.testing.assert_allclose(apply_cube(loaded, img), img, atol=1e-3)

    def test_lut_matches_direct_render(self, tmp_path):
        recipe = GradingRecipe(temperature=0.3, contrast=1.3, saturation=0.8)
        table = bake_lut(lambda x: apply_recipe(x, recipe))
        img = random_image()
        via_lut = apply_cube(table, img)
        direct = apply_recipe(img, recipe)
        np.testing.assert_allclose(via_lut, direct, atol=0.02)

    def test_nan_pipeline_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            bake_lut(lambda x: x * np.nan)

    def test_cube_header(self, tmp_path):
        path = write_cube(bake_lut(lambda x: x), tmp_path / "t.cube", title="My Look")
        head = path.read_text().splitlines()[:2]
        assert head[0] == 'TITLE "My Look"'
        assert head[1] == "LUT_3D_SIZE 33"
