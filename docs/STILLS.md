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

macOS (Apple Silicon, MLX):

```bash
./scripts/setup_flux.sh
```

Windows and Linux (CUDA or ROCm):

```powershell
.\scripts\setup_image.ps1              # reuse an existing GPU torch
.\scripts\setup_image.ps1 -InstallTorch  # or install one into image-venv
```

Both build `image-venv` inside the data directory and download the FLUX.2 [klein]
4B weights (about 16 GB) on first use. Without it, stills fall back to the
deterministic placeholder painter — the reader and the narration are unaffected.

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
