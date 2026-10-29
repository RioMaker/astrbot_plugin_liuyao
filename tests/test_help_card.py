from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw
from test_permissions import _Event, _make_enabled_plugin

from main import HELP_TEXT
from textcard import LiuyaoTextCardRenderer


@pytest.mark.parametrize("entry", ["command", "alias", "mention"])
def test_help_sends_one_image_without_group_gate_model_or_quota(entry):
    async def run():
        plugin = _make_enabled_plugin()
        plugin.config.update(agent_tools_enabled=False, command_reply_image_mode="never")
        plugin._group_gate = AsyncMock(side_effect=AssertionError("must not check group"))
        plugin._reserve_ai_daily = AsyncMock(side_effect=AssertionError("must not use quota"))
        plugin.context = SimpleNamespace(
            llm_generate=AsyncMock(side_effect=AssertionError("must not call model"))
        )
        event = _Event("member")
        event.get_group_id = lambda: ""
        paths = []

        async def send(message):
            path = Path(message["image"])
            assert path.is_absolute()
            with Image.open(path) as image:
                image.verify()
            paths.append(path)

        event.send = send
        if entry == "command":
            replies = [reply async for reply in plugin.liuyao_command(event, "  ")]
        elif entry == "alias":
            replies = [reply async for reply in plugin.divination_command(event, "六爻")]
        else:
            plugin._should_reply_mention_help = Mock(return_value=True)
            replies = [reply async for reply in plugin.collect_shefu_answer(event)]
        assert replies == []
        assert len(paths) == 1 and not paths[0].exists()
        plugin._group_gate.assert_not_awaited()
        plugin._reserve_ai_daily.assert_not_awaited()
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["render", "missing", "empty", "send"])
def test_help_failure_returns_complete_text_and_cleans_image(tmp_path, failure):
    async def run():
        plugin = _make_enabled_plugin()
        event = _Event("member")
        path = tmp_path / "help.png"
        if failure == "empty":
            path.touch()
        elif failure == "send":
            Image.new("RGB", (20, 20)).save(path)
        render = Mock(return_value=path)
        if failure == "render":
            render.side_effect = RuntimeError("render failed")
        plugin.text_card = SimpleNamespace(render_help=render)
        event.send = AsyncMock(side_effect=OSError("send failed"))

        replies = [reply async for reply in plugin.liuyao_command(event, "")]
        assert replies == [{"text": HELP_TEXT}]
        assert not path.exists()
        if failure == "send":
            event.send.assert_awaited_once()
        else:
            event.send.assert_not_awaited()

    asyncio.run(run())


def test_help_card_contains_all_commands_and_descriptions_without_clipping(tmp_path, monkeypatch):
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        bounds = draw.textbbox(xy, text, font=kwargs["font"], anchor=kwargs["anchor"])
        assert 0 <= bounds[0] < bounds[2] <= draw._image.width
        assert 0 <= bounds[1] < bounds[3] <= draw._image.height
        drawn.append(str(text))
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    path = LiuyaoTextCardRenderer().render_help(HELP_TEXT, output_path=tmp_path / "help.png")
    with Image.open(path) as image:
        assert image.width == 1080 and 1000 < image.height < 2000
    combined = "".join(drawn).replace(" ", "")
    for line in HELP_TEXT.splitlines():
        for part in line.split("：", 1):
            assert part.replace(" ", "") in combined
