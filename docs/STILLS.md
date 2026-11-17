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

## The polish pass

Every painted still gets one **cleanup re-draw** before it is cached: FLUX.2 looks
at its own output and re-draws it in two steps, which sharpens halftone mush,
settles stray linework and tightens faces without moving anything — no mask, so no
seam. It runs only when a still actually contains people (a YuNet face check and
an RTMDet person check decide that), and each still records the outcome in a
`*.polish.json` sidecar keyed to the output hash, so a repaint polishes again and
an unchanged still never does.

- **Cost**: ~10 s per polished still once the painter is warm — the model loads
  once per session, not per image. The first still in a session pays ~75 s for the
  15 GiB transfer and the ROCm warm-up. Turn it off in **Settings → Polish stills**
  to skip the pass. Speed comes from one painter, not more: a single image's steps
  are a chain, so extra workers only time-slice the card.
  the time.
- **What it does not fix on its own**: hands. The one-step global pass tidies ink
  and contrast but leaves an ambiguous pen-hand a blob, so a second, masked pass
  runs over detected hands at 0.75 strength — see below.
- **Never destructive**: the pre-polish still is kept beside it as
  `*.pre-polish.png`, and a failed re-draw leaves the original untouched.

### The hand repair

After the global pass, RTMDet finds the people, RTMPose locates their wrists, and
each wrist becomes a hand box. Those boxes are painted into a feathered mask and
only that region is re-drawn, at 0.75 strength and two steps, with the result
blended back through the mask so no edge shows.

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

| Platform | Stills |
|---|---|
| macOS Apple Silicon | MLX through mflux |
| macOS Intel | CPU only, opt-in |
| Windows / Linux, NVIDIA | diffusers on CUDA |
| Windows / Linux, AMD | diffusers on ROCm |
| Any, CPU | opt-in (`MURMUR_ALLOW_CPU_FILM=1`), hours per still |
| No GPU torch | placeholder painter, clearly labelled in the UI |

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
