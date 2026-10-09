"""Fast start in app mode (Windows): Teksting's own window opens at once with a loading screen,
while the server code loads beside it. Before, these ran one after the other (~2 s each).

server.py hands over here before importing anything heavy; the server itself is then imported as the
module `server` in a background thread. The loading page (static/loading.html) switches to the app
by itself as soon as the server answers.
"""
import json
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

APP_ID = "Teksting.Teksting"  # same ID on the shortcuts (lag-snarveier.ps1) -> one taskbar button, pinnable
APP_DIR = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("TEKSTING_DATA", APP_DIR / "data"))
PORT = int(os.environ.get("TEKSTING_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}/"


def _port_in_use():
    with socket.socket() as s:
        s.settimeout(0.3)  # on Windows a closed port takes ~2 s to fail; a local server answers at once
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _show_existing():
    """Teksting already running? Bring its window forward (POST /api/show) instead of starting again."""
    if not _port_in_use():
        return False
    try:
        req = urllib.request.Request(URL + "api/show", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return bool(json.load(r).get("ok"))
    except Exception:
        return False


def _ui_lang():
    try:
        return json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("ui_lang", "")
    except (OSError, ValueError):
        return ""


def _dark_mode():
    """Windows app theme, so the empty window starts in the right colour (no white flash in dark mode)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def loading_html():
    html = (APP_DIR / "static" / "loading.html").read_text(encoding="utf-8")
    return html.replace("__URL__", URL).replace("__LANG__", _ui_lang())


def main(t0):
    if sys.stdout is None or "pythonw" in Path(sys.executable).name.lower():
        DATA.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(DATA / "teksting.log", "a", encoding="utf-8", buffering=1)
    print("\n---", time.strftime("%Y-%m-%d %H:%M:%S"), "starting")
    if _show_existing():
        os._exit(0)
    viewer = _port_in_use()  # a Teksting without its own window (browser fallback) is running: just show it

    srv = {}
    failed = threading.Event()

    def boot():
        try:
            import server
            server.T0 = t0
            srv["m"] = server
            server.serve_in_background()
            server.wait_until_ready()
            print(f"  server ready after {time.time() - t0:.1f} s")
        except Exception:
            traceback.print_exc()
            failed.set()

    if not viewer:
        threading.Thread(target=boot, daemon=True).start()

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        import webview
    except Exception as e:  # no pywebview/.NET: use a browser window like before
        print("Own window not available, using a browser:", e)
        _browser_fallback(srv, failed, viewer)
        return

    webview.settings["ALLOW_DOWNLOADS"] = True  # "Last ned SRT" opens a save dialog
    if os.environ.get("TEKSTING_DEBUG_PORT"):  # dev: inspect the window with Chrome DevTools
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(os.environ["TEKSTING_DEBUG_PORT"])
    w = webview.create_window("Teksting", html=loading_html(), width=1440, height=900, min_size=(400, 500),
                              background_color="#10171d" if _dark_mode() else "#edf1f4")  # --paper

    def on_closing():
        # Closing while a video is transcribing hides the window; the job finishes, then the app quits.
        server = srv.get("m")
        if server and server.busy():
            server.WIN["hidden"] = True
            threading.Timer(0.05, w.hide).start()
            return False

    def on_shown():
        w.show()  # Teksting.vbs starts pythonw hidden; Windows applies that to our first window
        print(f"  window shown after {time.time() - t0:.1f} s")

    def attach():  # runs once the window exists: let the server reach it (/api/show, watchdog)
        while "m" not in srv and not failed.is_set() and not viewer:
            time.sleep(0.05)
        if "m" in srv:
            srv["m"].WIN["w"] = w

    w.events.closing += on_closing
    w.events.shown += on_shown
    try:
        webview.start(attach, gui="edgechromium", private_mode=False, storage_path=str(DATA / "webview"),
                      icon=str(APP_DIR / "static" / "teksting.ico"))
    except Exception as e:
        print("Own window failed, using a browser:", e)
        traceback.print_exc()
        _browser_fallback(srv, failed, viewer)
        return
    os._exit(0)  # window closed and nothing running (closing while busy only hides it)


def _browser_fallback(srv, failed, viewer):
    for _ in range(600):
        if viewer or "m" in srv or failed.is_set():
            break
        time.sleep(0.05)
    if "m" in srv:
        srv["m"].open_when_ready(URL)
    else:
        import webbrowser
        webbrowser.open(URL)
    if viewer or failed.is_set():
        os._exit(0)
    while True:  # the server runs in a daemon thread; the watchdog quits when the browser is gone
        time.sleep(3600)
