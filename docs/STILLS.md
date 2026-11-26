# Manga stills and chapter stills

Rhapsode does not generate motion. The **Manga** view turns narration into
monochrome manga key images: each beat of the chapter gets one still, painted
locally, shown with a calm crossfade and a constant slow zoom. **Create chapter
film** assembles those same stills against the narration into a 1920×1080 MP4 with
1.2-second dissolves and subtitles — a stills cut, not an animated sequence. No
video model runs anywhere in the app.

- **Stills, not every sentence.** The planner groups 3–20 sentences into one
  representative beat, so a 7-minute chapter is a handful of stills instead of
  dozens. Subtitles still follow every spoken sentence.
- **Painted ahead, then served.** A background worker renders key stills into
  `film/<doc>/scenes/` inside the data directory. The Manga view serves cached
  frames and shows a "Painting scene…" pill otherwise.
- **A cast per book.** The prompt writer extracts the recurring cast once; each
  character gets a clean full-body portrait on white. The stills cut anchors to the
  portrait of the character named in the beat, using the previous frame only
  between anchors so faces and costumes do not drift.
- **Shot variety.** Framing rotates through establishing wide, medium two-shot,
  close-up, over-the-shoulder, low-angle and detail inserts instead of defaulting to
  a wide low-angle shot every time.
- **Monochrome, cleaned.** Every still is converted to true grayscale, blank panel
  edges are trimmed, and split spreads are collapsed to one panel. If OCR finds
  speech bubbles or lettering, FLUX edits only that frame to remove the text while
  keeping the composition.
- **One engine, two presets.** Every still is **FLUX.2 [klein] 4B** (Apache-2.0),
  distilled to four steps. **High** paints 1920×1080, **Fast** paints 1280×720 in
  two steps. macOS runs it through **mflux** on MLX; Windows and Linux run the same
  weights through diffusers on CUDA or ROCm, in a separate `image-venv` so the
  reader process never loads torch. Switch with **Settings → Manga quality**.
- **Steady motion.** No alternating Ken Burns direction or panning. Each image zooms
  1.00 → 1.04 at the centre, rendered at 2560×1440/30 and downsampled to
  1920×1080/30. Beats dissolve into each other over 1.2 s.
- **Background queue.** **Create chapter film** queues the job and returns
  immediately. You can keep reading, watch Manga, or queue another chapter; each job
  shows Painting → Narrating → Encoding → Ready with its own Download, Cancel and
  Dismiss controls.
- **Watch while painting.** Manga playback starts narration immediately instead of
  waiting for the whole chapter to be painted. The current scene loads on demand and
  the next two scenes are prefetched.
- **Cached re-cuts.** A chapter is reused instantly when its plan, images,
  voice, quality, and render settings are unchanged. Changing one still re-renders
  only that chapter.
- **Encoding.** The MP4 export uses the system `ffmpeg` (`brew install ffmpeg`). If that
  build includes libass, karaoke subtitles are burned in; otherwise they are attached
  as a soft `mov_text` track so the cut still plays. H.264 + AAC, hardware encoded
  where possible.


## What happens when the app quits

The painter is a child process holding most of the card, so its lifetime is the app's
lifetime, enforced twice:

- **The OS enforces it.** The child is spawned through `murmur/proc.py`, which assigns
  it to a job object with kill-on-close. Quitting any way at all — the window, Ctrl+C,
  End task, a crash — closes that handle and Windows kills the painter.
- **The app releases it.** `murmur/lifecycle.py` stops the painter (a `quit` on its
  stdin, then a kill after five seconds), drops the Kokoro voice model, stops the search
  indexer and closes the database. It runs from the lifespan's shutdown, from
  `POST /api/quit` (loopback only; this is the Settings → **Quit Rhapsode** button,
  since browser mode has no window to close) and from the desktop entry once the
  window is gone.
- **On Linux and macOS the child watches its parent.** There is no job object there, and
  no `PR_SET_PDEATHSIG` that behaves the same on both, so `murmur/proc.py` starts every
  long-lived child in its own session and passes it `MURMUR_PARENT_PID`; the painter then
  checks `getppid()` twice a second and exits the moment the app is gone - including the
  case where the app was killed with SIGKILL in the middle of a tile. A painter started
  by hand has no such variable, so closing the terminal does not silently kill a
  measurement run.

Measured on an RX 9070 XT with the stills engine loaded (Kokoro and a resident painter,
`lite` profile): after quitting, no `fluxgen` process remains and dedicated GPU memory
returns to its pre-launch level within a second.

## The activity contract

Cinema used to say "Painting scene…" for the whole pipeline, though a cold model
load can dominate the wait and a placeholder arrives with no explanation. The
engine now reports what it is doing, and the UI renders that instead of guessing.

`GET /api/film/activity[?doc=…&chapter=…]` returns one object, written where the
work happens (`murmur/activity.py`, reported by `film.py` and `imaging.py`):

```json
{
  "phase": "loading", "detail": "Loading the stills engine - first still this session",
  "doc": "a1b2", "chapter": 0, "scene": 3,
  "backend": "flux2-klein-diffusers", "model": "flux2-klein-4b", "profile": "balanced",
  "coldStart": true, "busy": true, "determinate": false,
  "queued": 4, "active": 1, "done": 8, "total": 24, "failed": 0, "error": null
}
```

- **Phases**: `idle`, `planning`, `reference`, `loading`, `painting`, `cleanup`,
  `polishing`, `ready`, `placeholder`, `failed` (plus `queued`, which the API
  derives from the painter's queue for the chapter asked about).
- **`coldStart`** marks the first still of the session, when the model load is
  paid; Cinema says "Loading the stills engine" for it, never "painting".
- **`determinate` is always false**: no backend reports step counts, so the
  progress bar slides instead of showing a percentage it cannot back up.
- **`placeholder`** is a terminal phase: the prompt card is served as the scene
  image, counted as done, and Cinema labels it, so a machine with no stills
  engine no longer re-plans and repaints the same card on every poll.
- A failure keeps its `error` (the backend's message, truncated) and Cinema
  offers **Retry scene** rather than another indistinguishable spinner.

Settings → **Stills engine** reads the same object; it no longer keeps its own
notion of "running", and the reading engine (Kokoro) is named separately so
"engine" is not two products in one window.

## The polish pass

Every painted still gets one **cleanup re-draw** before it is cached: FLUX.2 looks
at its own output and re-draws it in two steps, which sharpens halftone mush,
settles stray linework and tightens faces without moving anything — no mask, so no
seam. It runs only when a still actually contains people (a YuNet face check and
an RTMDet person check decide that), and each still records the outcome in a
`*.polish.json` sidecar keyed to the output hash, so a repaint polishes again and
an unchanged still never does.

- **Cost** (measured on an RX 9070 XT, 1280×720, two steps): **41 s for the first
  still, 2.7 s each after** with the q4 transformer — the first call pages the 8 GiB
  text encoder off disk, everything after is small. bf16 measures 75 s and 10.4 s
  for the same pair. The model loads once per session, not per image: a single
  image's steps are a chain, so extra painters only time-slice the card.
  Turn it off in **Settings → Polish stills** to skip the pass.
  the time.
- **What it does not fix on its own**: hands. The one-step global pass tidies ink
  and contrast but leaves an ambiguous pen-hand a blob, so a second, masked pass
  runs over detected hands at 0.75 strength — see below.
- **Never destructive**: the pre-polish still is kept beside it as
  `*.pre-polish.png`, and a failed re-draw leaves the original untouched.

### The hand repair (experimental, off)

**Not on by default.** Measured on the lab still: at 0.75 strength the repair
replaced the whole forearm with a white blot; at 0.5 with a tighter mask it still
redrew the desk and duplicated the hand. The pass now carries two guards, and still
stays behind `polish.HANDS_ENABLED` because no still has needed it yet:

- **Blob gate** — only a hand box with almost no ink structure (edge energy below
  0.12) is worth repairing. Hands the model already drew score ~0.2 and are left
  alone.
- **Acceptance** — after the repair the region must keep 60% of its ink and 50% of
  its edge energy, or the still is restored from a copy taken beforehand. Both bad
  repairs erased the hand to flat white (ink 0.185 → 0.000), which this catches.

MediaPipe's hand landmarker was tried as the quality signal and rejected: it finds
**zero** hands even on 3× upscaled crops of clearly drawn hands, so it cannot judge
this artwork.

After the global pass, RTMDet finds the people, RTMPose locates their wrists, and
each wrist becomes a hand box. Those boxes are painted into a feathered mask and
only that region is re-drawn, at 0.75 strength and two steps, with the result
blended back through the mask so no edge shows.

### The face repair (off by default)

A face in a 1874×1056 still is about 180×240 px, and the global pass re-draws the
whole frame at 0.32 strength - it can settle a wobbling line there, it cannot
rebuild an eye. So a face is repaired the way a hand is, at the resolution the
face actually needs:

1. **Crop** — YuNet's box is grown 40% on every side (forehead, chin, jaw) and
   squared, then upsampled to **1024×1024**. A 180 px face becomes 1024 px of
   detail budget.
2. **Mask** — an ellipse over the face itself, not over the margin: the margin is
   context for the model, not permission to re-draw hair and background. It is
   blurred before blending, so no seam shows.
3. **Re-draw** — 0.55 strength, two steps, "monochrome manga face, symmetric eyes
   and brows, clean black ink linework, screentone shading, no text, no speech
   bubbles".
4. **Verify** — the painted tile must still contain a face (YuNet at 0.5), and the
   blended region must keep 60% of its ink and 50% of its edge energy. Either check
   failing restores that face from a copy taken before any of them, so one bad
   face cannot spoil a still.
5. **Cap** — `FACE_MAX_DEFAULT = 2` faces per still, largest first, and two boxes
   over one face are collapsed: each face is a separate masked re-draw, and a
   crowd must not turn one still into a two-minute job. `MURMUR_POLISH_FACE_MAX`
   raises it.

**Off by default**, behind **Settings → Repair faces** and `MURMUR_POLISH_FACES=1`
for testing. The reason is identity: a crop re-draw can take a character's face
somewhere the still did not have it, and a repair that damages a frame is worse
than the soft face it was meant to fix - the hand repair taught that lesson
already. The sidecar records `face_repair` per face (action, box, seconds), and
the pass carries a `variant` so switching it on is not mistaken for "cached".

One thing the pass cannot see: with a **quantised** transformer the inpaint
pipeline is unavailable (`Flux2KleinInpaintPipeline` cannot re-dtype a quantised
model), so the painter falls back to *edit mode* - it re-draws the whole tile and
the caller keeps only the masked region. The isolation therefore comes from the
blend, not from the model, and the cost of a face tile is the cost of the whole
tile. Turn the pass off if a still's faces are already fine.

Why this one is allowed to change pixels when the global pass is not: a hand has no
identity to preserve. Any plausible hand beats a mangled one, and it sits at the
edge of the frame where a small change is invisible. Faces are the opposite — they
carry the character — so they only ever get the gentle global pass.

If the pose decode is wrong the geometry check catches it: a wrist outside the
person's box is discarded rather than inpainted, and arms whose segments are out of
proportion or longer than the body are logged in the sidecar as `flags`.

## Install the stills engine once

The app offers this itself on first start (the third card, after the voice and translation),
and **Settings → Stills engine** re-runs it, which is the short path. The commands below are
what that runs, and how to do it by hand.

The script finds a base interpreter itself: `MURMUR_BASE_PYTHON` if set, then `python3.12`,
`python`, and finally `py -3.12`, accepting only one that reports 3.11 or 3.12. That matters
on machines where the `py` launcher lists 3.12 only as the Microsoft Store alias, which it
cannot start. If a GPU torch already lives somewhere else (unsloth, a ROCm build),
`MURMUR_TORCH_PYTHON` points at its interpreter and the venv reuses it instead of
downloading several gigabytes.

When no Python 3.11/3.12 can be started at all, the shipped `uv.exe` (beside `Rhapsode.exe`)
fetches a managed CPython 3.12 and builds the venv with it — no admin rights, no Store
alias. Only that one download is extra; everything after it is the same.

macOS (Apple Silicon, MLX):

```bash
./scripts/setup_flux.sh
```

Windows (CUDA or ROCm):

```powershell
.\scripts\setup_image.ps1              # reuse an existing GPU torch
.\scripts\setup_image.ps1 -InstallTorch  # or install one into image-venv
```

Linux (CUDA or ROCm) - `setup_image.ps1` is Windows-only (PowerShell, `Scripts\python.exe`,
`%LOCALAPPDATA%`), so the interpreter is built by hand. It must be the data directory's
`image-venv`, because that is the only Python the painter runs; installing
the reader's own dependencies into `.venv` does not give Cinema anything to paint with:

```bash
# The Python you point at must already report a GPU:
#   python -c "import torch; print(torch.cuda.is_available())"
data="${XDG_DATA_HOME:-$HOME/.local/share}/rhapsode"
python3.12 -m venv --system-site-packages "$data/image-venv"
"$data/image-venv/bin/python" -m pip install --quiet "diffusers>=0.42" "transformers>=4.49" \
  "accelerate>=1.3" "safetensors>=0.5" "huggingface_hub>=0.28" "pillow>=10.4"
```

Both build `image-venv` inside the data directory. On Windows the app's first-run card
runs the same script and also fetches the q4 transformer (2.4 GB) and the text encoder,
VAE and tokenizer (8.2 GB measured) in that one step, so the first still does not have to
download anything; the manual commands above leave those to the first still, which is
why a hand-built engine is slower the first time. Without an engine, stills fall back to
the deterministic placeholder painter - the reader and the narration are unaffected.

Rendering is a short batch: a 30-minute chapter is roughly 20–30 stills. On an
RX 9070 XT, 1080p High takes about two minutes per still including model load, and
2 minutes per still afterwards; an unchanged chapter re-cuts instantly.

## GPU support

| Card | Profile | What runs | Peak VRAM |
|---|---|---|---|
| 24 GB+ | `max` | bf16 transformer and text encoder | ~16 GB |
| **12–22 GB** | **`balanced`** | q4 transformer, text encoder on the GPU | ~13 GB |
| **6–10 GB (5060/4060/9060)** | **`lite`** | q4 transformer, text encoder on the CPU | **~5 GB** |
| no usable GPU | `cpu` | placeholder painter, reader unaffected | — |

The profile is a file, not a heuristic baked into the code:
`%LOCALAPPDATA%\Rhapsode\engine.json`, written by `scripts\setup.ps1` from the card
it finds and editable afterwards. `scripts\setup.ps1 -Profile lite` is the switch;
`/api/health` reports the active one under `profile`.

```json
{ "profile": "lite", "precision": "q4", "text_device": "cpu",
  "reserve_mib": 2000, "quality": "fast", "polish": false }
```

`reserve_mib` is headroom the painter refuses to touch. `balanced` on a 16 GB card
keeps 2.5 GB back and measures ~13 GB resident — which is why `lite` is the right
choice when the machine is shared (a remote-desktop encoder, another agent on the
same GPU): it costs ~10 s per prompt and buys 8 GB of headroom.

Precision is chosen from free VRAM at load time, not from the model name: the q4
transformer (2.4 GB on disk, ~2.6 GiB resident) plus the VAE is enough that an 8 GB
card keeps its desktop. The text encoder is the other half — Qwen3 is ~8 GiB in
bf16 — so it stays on the CPU below ~14 GB of free VRAM; encoding a prompt there
costs seconds, once per prompt.

`MURMUR_FLUX_RESERVE_MB` (default 2500) is the headroom the painter refuses to
touch. Without it, a 15.5 GiB bf16 load on a 16 GB card leaves ~1 GB for Windows
and the remote-desktop encoder, and the whole session stalls — which is exactly
what happened here before the guard existed.

## Murmur engine sidecar

The stills pipeline can run as a standalone local service, separate from the Rhapsode
window:

```bash
./scripts/run_engine.sh
# or: MURMUR_ENGINE_PORT=8931 .venv/bin/python -m murmur.engine_api
```

It exposes the same contract as the app (the routes are still named `film`):

- `GET /health`, `GET /version`
- `GET /api/film/plan`, `GET /api/film/images`
- `POST /api/film/scene`, `POST /api/film/scene/regenerate`
- `POST /api/film/prefetch`, `GET /api/film/progress`
- `POST /api/docs/<id>/film`, `GET /api/film/<job>`, `GET /api/film/<job>/video`

The packaged app can also be launched in engine-only mode:

```bash
dist/Rhapsode.app/Contents/MacOS/Rhapsode --engine
```

Rhapsode currently runs the same router in-process by default; the sidecar is the
first step toward a separately packaged Murmur engine for other frontends.

## The warm start: where the first still's time goes

Measured on the reference machine (RX 9070 XT, 16 GB) on 2026-11-19, in the app's
own path, with the painter cold:

| Step | Cost |
| --- | --- |
| `imaging.available()` asking "is torch installed?" by importing it | **6.05 s** |
| spawning the image interpreter and importing torch when asked for a still | 24.7 s |
| painting the 1920x1080 still | ~27 s |
| **first still, end to end** | **58.4 s** |

None of that is the network, and the third row is the only one that is the model
doing what it was asked. Two of the rows were pure overhead and both are gone:

* `imaging._has_packages` reads the interpreter's `site-packages` instead of running
  it. The import probe survives as a fallback for a layout it does not recognise,
  because a wrong "yes" would become a placeholder still and a wrong "no" would
  claim the engine is missing when it is not.
* `film.warm_engine` starts the load when the chapter's plan is requested - the
  gesture that opens Cinema or Review scenes, and always before the first scene
  click - behind an `activity.phase("loading", ...)` so the panel says "starting"
  rather than nothing. `warmStills` (Settings -> Stills engine, on by default)
  turns it off on a machine where the GPU should stay free until asked.

The load is about twenty seconds and the plan sits on screen longer than that, so
the wait after clicking the scene is now the paint and nothing else. If the reader
clicks immediately, the load is already in flight and is shared - `FluxWorker.warm`
and `FluxWorker.run` hold the same lock, so a scene request joins the load instead
of starting a second one.

Warming obeys the same guards a real scene does, so that opening Cinema can never
make anything worse: a placeholder install (no stills engine in this data folder) is
left alone rather than told it failed, and a machine below the RAM floor is not
warmed, because that floor refuses a cold load and a refused load is a visible
failure where nothing used to happen. An engine that is already resident is noticed
and not loaded twice.
