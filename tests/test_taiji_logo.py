from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageColor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import renderer  # noqa: E402


@pytest.mark.parametrize("size", [50, 64, 72, 76])
def test_landscape_taiji_preserves_background_at_all_title_sizes(size):
    background = (172, 185, 198)
    canvas = Image.new("RGB", (120, 120), background)
    renderer.draw_taiji_logo(canvas, 10, 12, width=size, height=size)

    bounds = ImageChops.difference(canvas, Image.new("RGB", canvas.size, background)).getbbox()
    assert bounds is not None
    left, top, right, bottom = bounds
    assert left >= 10 and top >= 12 and right <= 10 + size and bottom <= 12 + size
    assert canvas.getpixel((11, 13)) == background
    # The landscape retains tonal detail rather than becoming a flat mask.
    assert len(canvas.getcolors(maxcolors=120 * 120)) > 300


@pytest.mark.parametrize("failure", ["missing", "corrupt", "transparent"])
def test_unavailable_landscape_still_draws_a_recognizable_taiji(tmp_path, monkeypatch, failure):
    asset = tmp_path / "taiji.png"
    if failure == "corrupt":
        asset.write_bytes(b"not a PNG")
    elif failure == "transparent":
        Image.new("RGBA", (24, 24), (0, 0, 0, 0)).save(asset)
    monkeypatch.setattr(renderer, "TAIJI_PATH", asset)
    background = (172, 185, 198)
    canvas = Image.new("RGB", (120, 120), background)
    renderer.draw_taiji_logo(canvas, 20, 20, width=72, height=72)

    assert canvas.getpixel((56, 38)) == ImageColor.getrgb(renderer.PAPER)
    assert canvas.getpixel((56, 74)) == ImageColor.getrgb(renderer.JADE)
    assert canvas.getpixel((38, 56)) == ImageColor.getrgb(renderer.JADE)
    assert canvas.getpixel((74, 56)) == ImageColor.getrgb(renderer.PAPER)
    assert canvas.getpixel((20, 20)) == background


def test_explicit_custom_color_remains_supported():
    canvas = Image.new("RGB", (90, 90), renderer.PAPER)
    renderer.draw_taiji_logo(canvas, 9, 9, width=72, height=72, color="#523F6D")
    assert canvas.getpixel((45, 63)) == (82, 63, 109)
