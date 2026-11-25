import pytest
from PIL import Image
from PyQt6.QtWidgets import QApplication

from screenlens.ui.panel import Panel, escape_markdown


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_panel_never_loads_images_named_by_the_model(app, tmp_path):
    """Regression: Qt opens the path itself if loadResource returns nothing."""
    path = tmp_path / "probe.png"
    Image.new("RGB", (120, 120), (0, 255, 0)).save(path)
    panel = Panel()
    panel.add_chunk(f"x ![a]({path.as_posix()}) ![b](file:///{path.as_posix()}) y")
    panel.finish()
    shot = panel.grab().toImage()
    colors = {shot.pixelColor(x, y).getRgb()[:3]
              for x in range(0, shot.width(), 5) for y in range(0, shot.height(), 5)}
    assert (0, 255, 0) not in colors


def test_question_is_shown_literally(app):
    panel = Panel()
    panel.begin("why *this* <b> # 1?")
    assert "why *this* <b> # 1?" in panel.view.toPlainText()


def test_retry_discards_partial_answer(app):
    panel = Panel()
    panel.begin()
    panel.add_chunk("partial")
    panel.fail("boom")
    assert panel.error.text() == "boom"
    panel.begin()
    panel.add_chunk("full")
    panel.finish()
    assert panel.view.toPlainText() == "full"


def test_escape_markdown():
    assert escape_markdown("a*b_[c]") == r"a\*b\_\[c\]"
