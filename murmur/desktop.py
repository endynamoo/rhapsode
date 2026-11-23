"""Native macOS window for Rhapsode.

Runs the existing FastAPI server inside the app process and shows the UI in a
WKWebView via pywebview. This is the entry point used by the packaged `.app`.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import urllib.request

from . import config, lifecycle, netinfo, runtime


def _healthy(port: int, timeout: float = 0.6) -> bool:
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _choose_port(host: str, preferred: int) -> int:
    # Reuse an already-running Rhapsode (dev server or another window).
    if _healthy(preferred):
        return preferred
    # Match uvicorn's bind semantics (SO_REUSEADDR) or a TIME_WAIT preferred
    # port (right after quitting the app) falsely looks occupied and we fall
    # to an ephemeral port that uvicorn then fails to re-acquire.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _serve(port: int) -> None:
    import traceback

    import uvicorn

    from .server import app

    try:
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=config.HOST,
                port=port,
                log_level="warning",
                access_log=False,
            )
        )
        server.run()
    except Exception:  # windowed builds have no console: persist thread crashes
        traceback.print_exc()
        try:
            with open(config.HOME / "server_error.log", "a", encoding="utf-8") as fh:
                fh.write(f"\n=== port {port} ===\n")
                traceback.print_exc(file=fh)
        except OSError:
            pass


def _log_handle():
    """Append-only log in the data directory: a windowed build has no console.

    Without this the process can vanish with nothing written anywhere, which is
    exactly what happened twice during chapter renders (no WER report, no event,
    no log). faulthandler additionally dumps the Python stack on a hard crash.
    """
    import time

    try:
        path = config.HOME / "app.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a", encoding="utf-8", buffering=1)
        handle.write(
            f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start pid={os.getpid()} "
            f"version={__import__('murmur', fromlist=['__version__']).__version__} ===\n"
        )
        handle.flush()
        return handle
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    log = _log_handle()
    if log is not None:
        try:
            import faulthandler

            faulthandler.enable(file=log, all_threads=True)
        except Exception:
            pass
    if sys.stderr is None:  # windowed builds (console=False / pythonw): stdio
        sys.stderr = log if log is not None else open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log if log is not None else open(os.devnull, "w", encoding="utf-8")

    parser = argparse.ArgumentParser(prog="rhapsode")
    parser.add_argument("--selftest", action="store_true", help="start, check health, exit")
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--host", default=None, help="bind address (use 0.0.0.0 for Tailscale/LAN)")
    args = parser.parse_args(argv)

    host = runtime.resolve_host(args.host)
    port = _choose_port(host, args.port)
    runtime.apply(host, port)

    if not _healthy(port):
        threading.Thread(target=_serve, args=(port,), name="rhapsode-server", daemon=True).start()
        for _ in range(160):
            if _healthy(port):
                break
            time.sleep(0.25)

    if not _healthy(port):
        print("Rhapsode failed to start its local server.", file=sys.stderr)
        return 1

    if config.PUBLIC:
        print(f"Listening on {host}:{port}")
        for item in netinfo.access_urls(port, config.access_token()):
            if item["label"] != netinfo.LOCAL_LABEL:
                print(f"  {item['label']}: {item['url']}")

    if args.selftest:
        print(f"ok http://127.0.0.1:{port} (bind {host})")
        return 0

    try:
        import webview
    except Exception:  # fall back to browser mode (INSTALL.md B3)
        # A scripted start (the fresh-install harness, a service wrapper) should not throw a
        # tab onto whatever screen is attached; MURMUR_NO_BROWSER=1 says so.
        if os.environ.get("MURMUR_NO_BROWSER") != "1":
            import webbrowser

            webbrowser.open(f"http://127.0.0.1:{port}")
        print(f"desktop window unavailable; serving browser mode on {port}")
        threading.Event().wait()  # keep the daemon server thread alive
        return 0

    webview.create_window(
        "Rhapsode",
        f"http://127.0.0.1:{port}",
        width=1240,
        height=860,
        min_size=(760, 560),
        background_color="#07070d",
        text_select=True,
    )
    webview.start()
    # The window is gone: stop the painter and drop the voice model. atexit would
    # eventually do the same, but "eventually" is what left the card pinned when the
    # interpreter waited on a thread.
    lifecycle.release("window closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
