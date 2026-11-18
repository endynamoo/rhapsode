"""Record the demo: one real server, one real browser, one take.

The landing page and the README both need moving evidence, and nobody is going to
screen-record this by hand every release. This script boots the app the way
`tests/ui/test_smoke.py` does (a throwaway data directory, a free port), drives
the journeys a reader takes, records the whole session, and writes:

    <out>/demo.webm        the raw take from playwright
    <out>/demo.mp4         h264, yuv420p, faststart - what the site embeds
    <out>/still-1-library.png
    <out>/still-2-reading.png
    <out>/still-3-search.png
    <out>/still-4-settings.png

It is silent on purpose: playwright records the page, not the sound card. The
voice is already demonstrated by `tools/make_reader_audio.py` on the site, and a
muted video autoplays where a sound-on one does not.

The TTS weights are ~340 MB. Point `--models-from` at a directory that already
has `kokoro-v1.0.onnx` and the script junctions it into the throwaway home
instead of downloading; without it, run `python -m murmur.bootstrap` first.

    python scripts/record_demo.py --models-from "%LOCALAPPDATA%\\Rhapsode\\models"
    set PLAYWRIGHT_CHANNEL=msedge   # reuse Edge instead of downloading Chromium
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TITLE = "The Lantern-Keeper's Ledger"
BODY = (
    "The lantern keeper counted the ships that evening, as she had done every "
    "evening for eleven years. Eleven years of masts and fog and the slow bell "
    "of the harbour, and in all that time the ledger had never once written "
    "back.\n\n"
    "She kept it the way her grandmother had: a page for every night, the "
    "weather in the margin, the ships in the body, and one line left blank at "
    "the foot for whatever the sea decided.\n\n"
    "The ledger answered on the seventh night of the winter storms. She had "
    "gone down to the harbour wall to watch the last of the light, and when "
    "she came back the blank line was filled in her grandmother's hand. It "
    "said: the ships you counted tonight will come home.\n\n"
    "She did not tell anyone. She counted as she always had, and the line "
    "filled itself in, and by spring the harbour was fuller than it had been "
    "in her lifetime.\n"
)

SECOND_TITLE = "Notes on Tidal Clocks"
SECOND_BODY = (
    "A tidal clock keeps two hours at once: the one on the wall and the one the "
    "water keeps. Most of the difficulty is deciding which to believe, and the "
    "rest is deciding what to do when they disagree.\n"
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get_json(url: str, timeout: float = 20.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def post_json(url: str, payload: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def wait_for_health(base: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            body = get_json(base + "/api/health")
            if body.get("ok"):
                return body
        except Exception as exc:  # still starting
            last = exc
        time.sleep(0.4)
    raise SystemExit(f"{base}/api/health never answered: {last}")


def link_models(home: Path, tts_source: Path, assets_source: Path | None = None) -> None:
    """Assemble a models folder from what is already on this machine.

    The voice weights and the FLUX weights rarely live in the same place: a
    source checkout downloads Kokoro into its own home, while the stills engine
    keeps its Hugging Face cache in the installed app's data folder. Hard-link
    the small voice files and junction the model caches, so the take starts in
    seconds and nothing is downloaded twice.
    """
    target = home / "models"
    target.mkdir(parents=True, exist_ok=True)

    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        source = tts_source / name
        destination = target / name
        if destination.exists() or not source.exists():
            continue
        try:
            os.link(source, destination)
        except OSError:
            shutil.copyfile(source, destination)

    for name in ("hf", "polish", "pose"):
        source = (assets_source / name) if assets_source else None
        destination = target / name
        if source is None or destination.exists() or not source.exists():
            continue
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(destination), str(source)],
                check=True, capture_output=True,
            )
        else:
            destination.symlink_to(source, target_is_directory=True)


def link_image_venv(home: Path, source: Path | None) -> bool:
    """Give the throwaway home the stills engine this machine already built."""
    if source is None or not source.exists():
        return False
    target = home / "image-venv"
    if target.exists():
        return True
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(target), str(source)],
            check=True, capture_output=True,
        )
    else:
        target.symlink_to(source, target_is_directory=True)
    return True


def find_assets(explicit: str | None) -> Path | None:
    """The data folder whose `models/hf` holds the FLUX caches."""
    if explicit:
        path = Path(explicit).expanduser()
        if (path / "models" / "hf").exists():
            return path / "models"
        return path if (path / "hf").exists() else None
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Rhapsode" / "models"
        if (candidate / "hf").exists():
            return candidate
    candidate = Path.home() / ".murmur" / "models"
    return candidate if (candidate / "hf").exists() else None


def find_image_venv(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = Path(local) / "Rhapsode" / "image-venv"
        if candidate.exists():
            return candidate
    candidate = Path.home() / ".murmur" / "image-venv"
    return candidate if candidate.exists() else None


def find_models(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        return path if (path / "kokoro-v1.0.onnx").exists() else None
    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Rhapsode" / "models")
    candidates.append(Path.home() / ".murmur" / "models")
    for candidate in candidates:
        weight = candidate / "kokoro-v1.0.onnx"
        if weight.exists() and weight.stat().st_size > 100_000_000:
            return candidate
    return None


def wait_for_index(base: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = get_json(base + "/api/search/status")
        if not status.get("pending") and not (status.get("indexing") or {}).get("running"):
            return
        time.sleep(0.4)
    raise SystemExit("the search index never finished")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "dist" / "demo"))
    parser.add_argument("--models-from", default=None,
                        help="a directory holding kokoro-v1.0.onnx (default: detect)")
    parser.add_argument("--hf-from", default=None,
                        help="a data folder holding hf/ (FLUX caches); default: %%LOCALAPPDATA%%\\Rhapsode")
    parser.add_argument("--image-venv", default=None,
                        help="the stills interpreter (default: detect %LOCALAPPDATA%\\Rhapsode\\image-venv)")
    parser.add_argument("--no-cinema", action="store_true",
                        help="skip the pre-painted still and the Manga view")
    parser.add_argument("--channel", default=os.environ.get("PLAYWRIGHT_CHANNEL", "msedge"))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=980)
    parser.add_argument("--keep-home", action="store_true",
                        help="leave the throwaway data directory in place")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("playwright is not installed: pip install 'playwright>=1.47'")

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    home = Path(tempfile.mkdtemp(prefix="rhapsode-demo-"))
    models = find_models(args.models_from)
    if models is None:
        raise SystemExit(
            "no Kokoro weights found: pass --models-from, or run python -m murmur.bootstrap"
        )
    link_models(home, models, find_assets(args.hf_from))
    engine = find_image_venv(args.image_venv)
    has_engine = False if args.no_cinema else link_image_venv(home, engine)
    if has_engine:
        print(f"stills engine: {engine}")
    else:
        print("stills engine: none, the Manga view will be skipped")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "MURMUR_HOME": str(home),
        "MURMUR_PORT": str(port),
        "MURMUR_SEMANTIC": "off",
        "PYTHONIOENCODING": "utf-8",
    }
    log_path = home / "server.log"
    log = log_path.open("wb")
    server = subprocess.Popen(
        [sys.executable, "-m", "murmur"], cwd=str(ROOT), env=env,
        stdout=log, stderr=subprocess.STDOUT,
    )

    playwright = None
    browser = None
    video_dir = Path(tempfile.mkdtemp(prefix="rhapsode-take-"))
    started = time.time()
    try:
        health = wait_for_health(base)
        print(f"server on {base} · ocr={health.get('ocr')} · voices={health.get('voices')}")

        post_json(base + "/api/docs", {"title": TITLE, "text": BODY})
        post_json(base + "/api/docs", {"title": SECOND_TITLE, "text": SECOND_BODY})
        post_json(base + "/api/search/index", {})

        # The voice model loads on first use; ask for one clip so the demo does
        # not open on "Loading voice model".
        deadline = time.time() + 180
        while time.time() < deadline:
            state = get_json(base + "/api/health")
            if state.get("modelReady"):
                break
            time.sleep(1.0)
        else:
            raise SystemExit("the voice model never became ready")

        post_json(base + "/api/tts", {"text": "The lantern keeper counted the ships that evening.", "voice": "af_heart"})
        wait_for_index(base)
        print(f"library seeded and indexed in {time.time() - started:.1f}s")

        # Paint the first still before the camera rolls. A cold engine load is a
        # minute of honest progress that would wreck a 70 second take, and the
        # result is cached on disk, so the Manga view opens on a finished image.
        if has_engine:
            try:
                opening = get_json(base + "/api/docs")["docs"]
                doc_id = next(doc["id"] for doc in opening if doc["title"] == TITLE)
                began = time.time()
                # The scene endpoint queues and answers; poll it the way the UI
                # does until the still is actually delivered.
                deadline = began + 900
                scene: dict = {}
                while time.time() < deadline:
                    scene = post_json(base + "/api/film/scene",
                                      {"doc": doc_id, "chapter": 0, "index": 0}, timeout=120)
                    if scene.get("ready") or (scene.get("activity") or {}).get("phase") == "failed":
                        break
                    time.sleep(3.0)
                print(f"first still: ready={scene.get('ready')} "
                      f"placeholder={scene.get('placeholder')} in {time.time() - began:.0f}s")
            except Exception as exc:  # noqa: BLE001 - a demo should still record
                print(f"first still failed ({exc}); the take continues")

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=True, channel=args.channel,
                                             args=["--hide-scrollbars"])
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            record_video_dir=str(video_dir),
            record_video_size={"width": args.width, "height": args.height},
            device_scale_factor=1,
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        def beat(seconds: float) -> None:
            page.wait_for_timeout(int(seconds * 1000))

        def still(name: str) -> None:
            path = out / f"{name}.png"
            page.screenshot(path=str(path))
            print(f"  still {path.name}")

        page.goto(base + "/", wait_until="load")
        page.wait_for_selector(".lib-item")
        beat(3.4)
        still("still-1-library")

        page.locator(".lib-item", has_text="Lantern").first.click()
        page.wait_for_selector("#reader .unit, #reader p")
        beat(3.6)

        page.click("#playBtn")
        page.wait_for_selector(".unit.is-current", timeout=60000)
        beat(21.0)
        still("still-2-reading")
        page.click("#playBtn")  # pause; the highlight stays where it stopped
        beat(0.9)

        page.click("#searchBtn")
        page.wait_for_selector("#searchModal:not([hidden])")
        page.fill("#searchInput", "")
        # Type it in so the take shows the query being written; fill() again if
        # the debounce somehow misses the keystrokes, because a recording run is
        # expensive to lose to a cosmetic thing.
        page.locator("#searchInput").press_sequentially("lantern", delay=140)
        try:
            page.wait_for_selector(".search-hit", timeout=8000)
        except Exception:
            page.fill("#searchInput", "lantern")
            page.wait_for_selector(".search-hit", timeout=30000)
        beat(4.2)
        still("still-3-search")
        page.locator(".search-hit").first.click()  # jump to the passage
        beat(4.0)
        still("still-4-jump")

        if has_engine:
            page.click("[data-view='cinema']")
            page.wait_for_selector("#cinemaImage[src]", timeout=120000)
            beat(1.2)
            beat(6.4)
            still("still-5-cinema")
            page.click("[data-view='book']")
            beat(1.0)

        page.click("#settingsBtn")
        page.wait_for_selector("#settingsModal:not([hidden])")
        beat(2.2)
        options = page.locator("#setThemeSelect option")
        values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        current = page.evaluate("document.documentElement.dataset.theme")
        # Show the dropdown doing its job: flip to a light palette so the change is
        # unmistakable on video, then come back to the brand dark for the still.
        light = next((v for v in values if "paper" in v.lower() or "day" in v.lower()), None)
        if light and light != current:
            page.select_option("#setThemeSelect", light)
            beat(2.4)
        if current:
            page.select_option("#setThemeSelect", current)
        beat(1.8)
        still("still-6-settings")
        page.keyboard.press("Escape")
        beat(1.4)

        video = page.video
        context.close()
        recorded = Path(video.path())
        browser.close()
        browser = None
        playwright.stop()
        playwright = None

        raw = out / "demo.webm"
        shutil.copyfile(recorded, raw)
        mp4 = out / "demo.mp4"
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(raw), "-c:v", "libx264", "-preset", "slow",
             "-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             "-an", str(mp4)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(result.stderr[-1500:])
            raise SystemExit("ffmpeg could not encode the take")
        print(f"\n{raw.name}: {raw.stat().st_size / 1e6:.1f} MB")
        print(f"{mp4.name}: {mp4.stat().st_size / 1e6:.1f} MB")
        for png in sorted(out.glob("still-*.png")):
            print(f"{png.name}: {png.stat().st_size / 1e3:.0f} kB")
        return 0
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
        if not args.keep_home:
            shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(video_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
