import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import types
flask = types.ModuleType("flask")
class DummyBlueprint:
    def __init__(self, *a, **k): pass
    def route(self, *a, **k):
        def decorator(f):
            return f
        return decorator
flask.Blueprint = DummyBlueprint
flask.request = object()
flask.redirect = lambda *a, **k: None
flask.url_for = lambda *a, **k: ""
flask.render_template = lambda *a, **k: ""
flask.send_from_directory = lambda *a, **k: ""
flask.send_file = lambda *a, **k: ""
flask.jsonify = lambda *a, **k: {}
sys.modules.setdefault("flask", flask)
sys.modules.setdefault("requests", types.ModuleType("requests"))
sys.modules.setdefault("psutil", types.ModuleType("psutil"))
import pytest
from routes import compute_overlay_preview


def test_preview_all_uses_max_resolution():
    overlay_cfg = {"monitor_selection": "All"}
    monitors = {
        "Display0": {"resolution": "1920x1080"},
        "Display1": {"resolution": "1280x720"},
    }
    width, height, _ = compute_overlay_preview(overlay_cfg, monitors)
    scale = 400 / 1920
    assert width == 400
    assert height == int(1080 * scale)


def test_preview_specific_monitor():
    overlay_cfg = {"monitor_selection": "Display1"}
    monitors = {
        "Display1": {"resolution": "1024x768"}
    }
    width, height, _ = compute_overlay_preview(overlay_cfg, monitors)
    scale = 400 / 1024
    assert width == 400
    assert height == int(768 * scale)


def test_preview_unknown_monitor_defaults():
    overlay_cfg = {"monitor_selection": "Unknown"}
    monitors = {"Display0": {"resolution": "800x600"}}
    width, height, _ = compute_overlay_preview(overlay_cfg, monitors)
    scale = 400 / 1920
    assert (width, height) == (400, int(1080 * scale))
