import sys
import types
import time

# Provide dummy PySide6 modules so piviewer can be imported without the real Qt deps
qtcore = types.ModuleType("PySide6.QtCore")
class DummyQt:
    AlignCenter = 0
    AlignLeft = 0
    AlignRight = 0
    AlignHCenter = 0
    AlignVCenter = 0
    TextWordWrap = 0
    CompositionMode_Difference = 0
    FramelessWindowHint = 0
    KeepAspectRatio = 0
    FastTransformation = 0
    IgnoreAspectRatio = 0
    SmoothTransformation = 0
    white = 0
    transparent = 0
qtcore.Qt = DummyQt
class DummyTimer:
    def __init__(self, *a, **k):
        pass
    @staticmethod
    def singleShot(ms, func):
        func()
qtcore.QTimer = DummyTimer
qtcore.Slot = lambda *a, **k: (lambda f: f)
qtcore.QSize = object
qtcore.QRect = object
qtcore.QRectF = object

qtgui = types.ModuleType("PySide6.QtGui")
for name in ["QPixmap", "QMovie", "QPainter", "QImage", "QImageReader", "QTransform", "QFont"]:
    setattr(qtgui, name, type(name, (), {}))

qtwidgets = types.ModuleType("PySide6.QtWidgets")
for name in [
    "QApplication",
    "QMainWindow",
    "QWidget",
    "QLabel",
    "QProgressBar",
    "QGraphicsScene",
    "QGraphicsPixmapItem",
    "QGraphicsBlurEffect",
    "QSizePolicy",
]:
    setattr(qtwidgets, name, type(name, (), {}))

spotipy = types.ModuleType("spotipy")
spotipy.Spotify = type("Spotify", (), {})
oauth2 = types.ModuleType("spotipy.oauth2")
oauth2.SpotifyOAuth = type("SpotifyOAuth", (), {})
spotipy.oauth2 = oauth2

sys.modules.setdefault("PySide6", types.ModuleType("PySide6"))
sys.modules.setdefault("PySide6.QtCore", qtcore)
sys.modules.setdefault("PySide6.QtGui", qtgui)
sys.modules.setdefault("PySide6.QtWidgets", qtwidgets)
sys.modules.setdefault("spotipy", spotipy)
sys.modules.setdefault("spotipy.oauth2", oauth2)

import piviewer

DisplayWindow = piviewer.DisplayWindow


def test_spotify_fetch_thread_single():
    dw = DisplayWindow.__new__(DisplayWindow)
    dw.spotify_fetch_thread = None
    dw.spotify_fetch_id = 0

    def fake_fetch():
        time.sleep(0.1)
        return "dummy"

    dw.fetch_spotify_album_art = fake_fetch
    dw.handle_spotify_result = lambda fid, r: None

    dw.start_spotify_fetch()
    first_thread = dw.spotify_fetch_thread
    assert first_thread is not None
    time.sleep(0.02)
    dw.start_spotify_fetch()
    second_thread = dw.spotify_fetch_thread
    assert first_thread is second_thread
    first_thread.join(1)

