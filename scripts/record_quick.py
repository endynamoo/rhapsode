r"""Cut a short demo out of three clips, not one long take.

`record_demo.py` records the whole journey in a single take, which is honest but
slow: a minute of library, search and settings before anything moves. This
script records the three things a stranger needs to see, separately, and edits
them together with ffmpeg:

    clip 1   title card, 3.2 s      (an HTML page, screenshotted by playwright)
    clip 2   the voice, 13 s        (open the book, press play, let it read)
    clip 3   the manga view, 9 s    (a painted scene, then the scene grid)

Each clip is recorded in its own browser context, so each has its own clock and
its own audio capture; the voice is tapped off the app's own <audio> element
(the same WebAudio hook `record_demo.py` uses) and shifted by the measured delay
so the speech lands on the highlighted sentence. Clips are then normalised to one
size/rate/codec and concatenated by filter, which is what makes the cut clean:
no dropped frames between takes, no drifting audio.

Outputs, in <out>:

    quick.mp4            ~26 s, h264 + aac, the whole cut
    clip-1-title.mp4     the pieces, kept for re-cutting without re-recording
    clip-2-voice.mp4
    clip-3-manga.mp4
    quick-poster.png     a frame from the manga clip, for the site

Scenes are painted before the camera rolls - three stills is three cold-ish
passes - so the manga clip only ever shows finished art:

    python scripts/record_quick.py --models-from "%LOCALAPPDATA%\\Rhapsode\\models"
    set PLAYWRIGHT_CHANNEL=msedge   # reuse Edge instead of downloading Chromium
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import record_demo as demo  # noqa: E402  (helpers, no side effects)

# PLAYWRIGHT_CHANNEL=msedge reuses the installed Edge instead of a download.
CHANNEL = os.environ.get("PLAYWRIGHT_CHANNEL") or None

TITLE = "The Lantern-Keeper's Ledger"

# One chapter, ~640 words, three movements: the count, the storm, the answer.
# plan_chapter splits on estimated reading time (SCENE_SECONDS = 75), so this
# lands on three scenes - one per movement - which is what the manga clip needs.
STORY = """The lantern keeper counted the ships the way her mother had counted them, one for every hour of the watch, and wrote each one in the ledger with the date in the corner. She had done it for eleven years. In that time she had written down eleven winters and eleven summers, four hundred ships with names like Morning Tide and Second Sister, and the fog that came in over the harbour wall every evening at six as though someone had opened a door.

The count began before dawn. Marta took the lantern down from its hook, trimmed the wick, and walked the length of the harbour wall to where the old bell hung green with salt. The water was flat and black and full of small sounds. The first ship came out of the mist without a noise, only a shadow growing until it had masts, and then a name she could just make out in the grey. She wrote it down. The second came behind it, and the third, and by the time the sun had cleared the far pier she had filled a page.

By noon the fog had swallowed the pier entirely. The bell rang twice for a ship nobody could see, and Marta stood at the end of the wall with her hand on the rope and counted the rings instead of the hulls. In the afternoon the wind turned and the water went the colour of old pewter. The keeper of the second light walked out along the breakwater with a lantern nobody would need for hours, and they stood together and said nothing, the way people do when the weather is doing the talking.

At dusk the ledger was still empty for the day. Marta wrote the date in the corner, as she had every evening for eleven years, and sat with the pen in her hand and waited for the harbour to give her something worth the ink. The tide came in over the wall at six. A gull landed on the bell and was gone again before it rang.

That night the storms came early. The bell rang without stopping, and the boats came in one after another, running lights low, crews shouting to be heard over the water. Marta kept the lantern lit and the door open and the kettle on, and wrote each name as they came, her hand faster than it had ever been, until the page was full and she turned it over and kept going.

By three in the morning the last boat was in. The harbour was quiet in the way a room is quiet after an argument. She closed the ledger without looking at it and slept in the chair by the stove with her boots still on.

In the morning she opened it to write the date and found, on the last page, in a hand that was not hers and in ink that was still wet, three words. Marta read them twice. Then she put the pen down, put her coat on, and walked out to the harbour wall to watch the light come up over the water, which is what you do when the sea has finally answered you.

She was still there at six, when the tide came in over the wall, and she counted the ships the way her mother had counted them, one for every hour of the watch, and wrote nothing down at all.

The bell rang once for the last of them and then the harbour was still, and the ledger lay open on the table with its three words drying, and Marta went in to put the kettle on, because the day was beginning whether or not anybody had been told about it."""

CARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Rhapsode</title>
<style>
  html, body {{ margin: 0; height: 100%; }}
  body {{
    background: radial-gradient(120% 90% at 50% 8%, #221b44 0%, #121027 45%, #08070f 100%);
    color: #f4f1ff;
    font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    display: grid; place-items: center; text-align: center;
  }}
  .wrap {{ display: grid; gap: 26px; justify-items: center; }}
  .mark {{ width: 132px; height: 132px; filter: drop-shadow(0 0 42px rgba(139,108,255,.55)); }}
  h1 {{ margin: 0; font-size: 76px; font-weight: 500; letter-spacing: -0.5px; }}
  p {{ margin: 0; font-size: 25px; color: #b9b2d6; font-family: ui-sans-serif, system-ui, sans-serif; }}
  .rule {{ width: 72px; height: 2px; background: #8b6cff; opacity: .8; }}
  .foot {{ font-size: 15px; letter-spacing: .16em; text-transform: uppercase; color: #6f6890; font-family: ui-sans-serif, system-ui, sans-serif; }}
</style></head>
<body><div class="wrap">
  <div class="mark">{mark}</div>
  <h1>Rhapsode</h1>
  <div class="rule"></div>
  <p>reads anything aloud, and paints it — entirely on your machine</p>
  <div class="foot">no cloud · no account · no telemetry</div>
</div></body></html>
"""


def run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=capture, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-1200:]
        raise SystemExit(f"{command[0]} failed:\n{tail}")
    return result


def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise SystemExit("ffmpeg is not on PATH")


def ffprobe_path() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    raise SystemExit("ffprobe is not on PATH")


def probe_seconds(path: Path) -> float:
    result = run([ffprobe_path(), "-v", "error",
                  "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(result.stdout)["format"]["duration"])


def normalise(source: Path, target: Path, *, width: int, height: int, audio: bool) -> None:
    """One size, one rate, one codec - so the concat filter cannot fight itself."""
    command = [ffmpeg_path(), "-y", "-i", str(source)]
    if not audio:
        command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    command += [
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,fps=30,setsar=1",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-shortest", str(target),
    ]
    run(command)


def mux_with_voice(video: Path, audio: Path, start_offset: float, target: Path) -> None:
    """Shift the captured voice onto the video's timeline, then pad it out."""
    run([
        ffmpeg_path(), "-y", "-i", str(video), "-i", str(audio),
        "-filter_complex", f"[1:a]adelay={int(start_offset * 1000)}:all=1,apad[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart", str(target),
    ])


def record_title(playwright, page_html: Path, out: Path, width: int, height: int, seconds: float) -> Path:
    browser = playwright.chromium.launch(headless=True, channel=CHANNEL, args=["--hide-scrollbars"])
    context = browser.new_context(viewport={"width": width, "height": height},
                                  record_video_dir=str(out.parent / "raw"),
                                  record_video_size={"width": width, "height": height},
                                  device_scale_factor=1)
    page = context.new_page()
    page.goto(page_html.as_uri(), wait_until="load")
    page.wait_for_timeout(int(seconds * 1000))
    video = page.video
    context.close()
    recorded = Path(video.path())
    browser.close()
    shutil.move(str(recorded), str(out))
    return out


def record_app_clip(playwright, base: str, out: Path, width: int, height: int, drive) -> tuple[Path, dict | None]:
    """Record one app segment; `drive(page)` does the acting."""
    browser = playwright.chromium.launch(headless=True, channel=CHANNEL, args=["--hide-scrollbars"])
    context = browser.new_context(viewport={"width": width, "height": height},
                                  record_video_dir=str(out.parent / "raw"),
                                  record_video_size={"width": width, "height": height},
                                  device_scale_factor=1)
    started = time.time()
    page = context.new_page()
    page.set_default_timeout(30000)
    page.add_init_script(demo.AUDIO_HOOK)
    page.goto(base + "/", wait_until="load")
    drive(page)
    sound = None
    try:
        sound = page.evaluate("window.__demoAudioStop()")
    except Exception:  # noqa: BLE001 - a silent clip still beats no clip
        pass
    if sound and sound.get("startEpochMs"):
        sound["start_offset"] = max(0.0, sound["startEpochMs"] / 1000.0 - started)
    video = page.video
    context.close()
    recorded = Path(video.path())
    browser.close()
    shutil.move(str(recorded), str(out))
    return out, sound


def paint_scenes(base: str, doc_id: str, wanted: int, post) -> list[dict]:
    """Paint the planned scenes, one at a time, before recording.

    `wanted` of 0 means every scene the plan produced: the review grid must not
    show a "Not painted" tile in a demo.
    """
    plan = demo.get_json(f"{base}/api/film/plan?doc={doc_id}&chapter=0")
    scenes = plan.get("scenes", [])
    print(f"  plan: {len(scenes)} scenes in chapter 1", flush=True)
    count = len(scenes) if wanted <= 0 else min(wanted, len(scenes))
    for index in range(count):
        began = time.time()
        post(f"{base}/api/film/scene", {"doc": doc_id, "chapter": 0, "index": index})
        while time.time() - began < 420:
            scene = post(f"{base}/api/film/scene", {"doc": doc_id, "chapter": 0, "index": index})
            if scene.get("ready") or (scene.get("activity") or {}).get("phase") == "failed":
                break
            time.sleep(3.0)
        activity = scene.get("activity") or {}
        print(f"  scene {index + 1}: ready={scene.get('ready')} in {time.time() - began:.0f}s"
              + (f" FAILED {activity.get('detail')}" if activity.get("phase") == "failed" else ""),
              flush=True)
    return scenes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="output directory (default: dist/demo)")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--models-from", default=None)
    parser.add_argument("--hf-from", default=None)
    parser.add_argument("--image-venv", default=None)
    parser.add_argument("--channel", default=None, help="playwright channel, e.g. msedge")
    parser.add_argument("--profile", default=None,
                        help="stills engine profile: lite, balanced, max or cpu (default: leave as is)")
    parser.add_argument("--scenes", type=int, default=0,
                        help="scenes to paint first (0 = every planned scene)")
    parser.add_argument("--keep-server", action="store_true")
    parser.add_argument("--reuse-home", action="store_true",
                        help="keep the generated data directory from a previous run")
    args = parser.parse_args()

    out = Path(args.out) if args.out else ROOT / "dist" / "demo"
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw").mkdir(exist_ok=True)

    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    home = out / "home"
    if args.reuse_home:
        home.mkdir(exist_ok=True)
    else:
        # Throwaway: `home` is a generated data directory under dist/demo, never
        # the user's. A stale one shows the book twice in the library.
        shutil.rmtree(home, ignore_errors=True)
        home.mkdir(parents=True)
    models = demo.find_models(args.models_from)
    if not models:
        raise SystemExit("no Kokoro weights found: pass --models-from")
    demo.link_models(home, models, demo.find_assets(None))
    demo.link_image_venv(home, demo.find_image_venv(args.image_venv))

    port = demo.free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "MURMUR_HOME": str(home), "MURMUR_PORT": str(port),
           "PYTHONIOENCODING": "utf-8"}
    if args.hf_from:
        env["HF_HOME"] = args.hf_from
    server = subprocess.Popen([sys.executable, "-m", "murmur"], cwd=str(ROOT), env=env)
    print(f"server on {base} (pid {server.pid})", flush=True)
    try:
        health = demo.wait_for_health(base)
        print(f"  ocr={health.get('ocr')} voices={health.get('voices')}", flush=True)
        doc = demo.post_json(f"{base}/api/docs", {"title": TITLE, "text": STORY})
        doc_id = doc.get("id") or (doc.get("doc") or {}).get("id")
        print(f"  document {doc_id} ({len(STORY.split())} words)", flush=True)
        if args.profile:
            chosen = demo.post_json(f"{base}/api/engine/profile", {"profile": args.profile})
            print(f"  profile: {json.dumps(chosen)[:160]}", flush=True)
        scenes = paint_scenes(base, doc_id, args.scenes, lambda url, body: demo.post_json(url, body))

        card = out / "title-card.html"
        card.write_text(
            CARD.format(mark=(ROOT / "web" / "logo.svg").read_text("utf-8")), "utf-8"
        )

        with sync_playwright() as playwright:
            # 1 - title card
            clip1 = record_title(playwright, card, out / "clip-1-title.mp4", args.width, args.height, 3.4)
            print(f"  clip 1: {clip1.stat().st_size / 1e6:.1f} MB", flush=True)

            # 2 - the voice: open the book and let it read
            def drive_voice(page) -> None:
                page.wait_for_selector(".lib-item")
                page.wait_for_timeout(500)
                page.click(".lib-item")
                page.wait_for_selector("#reader .unit")
                page.wait_for_timeout(400)
                page.click("#playBtn")
                page.wait_for_timeout(13000)

            clip2, sound = record_app_clip(playwright, base, out / "clip-2-voice-raw.mp4",
                                           args.width, args.height, drive_voice)
            if not (sound and sound.get("base64")):
                raise SystemExit("no audio captured from the reading clip")
            voice = out / "clip-2-voice.webm"
            voice.write_bytes(base64.b64decode(sound["base64"]))
            mux_with_voice(clip2, voice, sound.get("start_offset", 0.0), out / "clip-2-voice.mp4")
            print(f"  clip 2: voice {sound['bytes'] / 1e3:.0f} kB, "
                  f"starts {sound.get('start_offset', 0):.2f}s into the clip", flush=True)

            # 3 - the manga view: one finished still, then the grid
            def drive_manga(page) -> None:
                page.wait_for_selector(".lib-item")
                page.click(".lib-item")
                page.wait_for_selector("#reader .unit")
                page.wait_for_timeout(500)
                page.click("[data-view='cinema']")
                page.wait_for_selector("#cinemaImage[src]", timeout=60000)
                page.wait_for_timeout(4200)
                page.click("#cinemaReview")
                page.wait_for_selector("#sceneGrid:not([hidden])", timeout=30000)
                page.wait_for_selector(".scene-tile img", timeout=30000)
                page.click(".scene-tile[data-index='0']")
                page.wait_for_timeout(4600)

            clip3, _ = record_app_clip(playwright, base, out / "clip-3-manga.mp4",
                                       args.width, args.height, drive_manga)
            print(f"  clip 3: {clip3.stat().st_size / 1e6:.1f} MB", flush=True)

        parts = []
        for name, has_voice in (("clip-1-title.mp4", False), ("clip-2-voice.mp4", True),
                                ("clip-3-manga.mp4", False)):
            source = out / name
            normal = out / f"norm-{name}"
            normalise(source, normal, width=args.width, height=args.height, audio=has_voice)
            parts.append(normal)

        joined = out / "quick-joined.mp4"
        filters = "".join(f"[{i}:v][{i}:a]" for i in range(len(parts)))
        inputs: list[str] = []
        for part in parts:
            inputs += ["-i", str(part)]
        run([
            ffmpeg_path(), "-y", *inputs,
            "-filter_complex", f"{filters}concat=n={len(parts)}:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", str(joined),
        ])

        total = probe_seconds(joined)
        quick = out / "quick.mp4"
        run([
            ffmpeg_path(), "-y", "-i", str(joined),
            "-vf", f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0.0, total - 0.7):.2f}:d=0.7",
            "-af", "afade=t=in:st=0:d=0.3,"
                   f"afade=t=out:st={max(0.0, total - 0.9):.2f}:d=0.9",
            "-c:v", "libx264", "-preset", "slow", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-movflags", "+faststart", str(quick),
        ])
        joined.unlink(missing_ok=True)

        print(f"\n{quick.name}: {probe_seconds(quick):.1f}s, "
              f"{quick.stat().st_size / 1e6:.1f} MB", flush=True)

        poster = out / "quick-poster.png"
        run([ffmpeg_path(), "-y", "-ss", "21", "-i", str(quick), "-frames:v", "1", str(poster)])
        print(f"{poster.name}: {poster.stat().st_size / 1e3:.0f} kB", flush=True)
        for png in sorted(out.glob("clip-*.mp4")):
            print(f"  kept {png.name}: {png.stat().st_size / 1e6:.1f} MB", flush=True)
        return 0
    finally:
        if not args.keep_server:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
