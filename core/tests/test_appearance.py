"""外观：主题切换与自定义背景。"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

from oryx import appearance
from oryx.config import Config, DEFAULT_CONFIG


@pytest.fixture()
def config(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("ORYX_HOME", str(tmp_path))
    data = dict(DEFAULT_CONFIG)
    cfg = Config(data)
    monkeypatch.setattr(Config, "save", lambda self: None)
    return cfg


def _png_data_url(color=(91, 140, 255)) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


def test_save_background_writes_file(config):
    result = appearance.save_background(_png_data_url(), config)
    path = Path(result["path"])
    assert path.exists()
    assert result["bytes"] > 0
    assert path.suffix == ".png"


def test_background_is_readable_back(config):
    appearance.save_background(_png_data_url(), config)
    path, mime = appearance.read_background()
    assert path is not None
    assert mime == "image/png"


def test_clear_background(config):
    appearance.save_background(_png_data_url(), config)
    assert appearance.clear_background(config) is True
    assert appearance.read_background()[0] is None


def test_rejects_non_image_data_url(config):
    with pytest.raises(ValueError):
        appearance.save_background("data:text/html;base64,PGh0bWw+", config)


def test_rejects_garbage_payload(config):
    with pytest.raises(ValueError):
        appearance.save_background("data:image/png;base64,!!!not-base64!!!", config)


def test_rejects_oversized(config, monkeypatch):
    monkeypatch.setattr(appearance, "MAX_BYTES", 10)
    with pytest.raises(ValueError):
        appearance.save_background(_png_data_url(), config)


def test_jpeg_extension_normalised(config):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    result = appearance.save_background(f"data:image/jpeg;base64,{encoded}", config)
    assert result["path"].endswith(".jpg")


def test_current_reports_state(config):
    assert appearance.current()["background_exists"] is False
    appearance.save_background(_png_data_url(), config)
    assert appearance.current()["background_exists"] is True
