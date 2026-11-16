# Manga view and chapter films

The **Manga** view turns narration into monochrome manga key images. Each 60–90
seconds of narration gets one still (about 4–8 images for a typical chapter), shown
with a calm crossfade and a constant slow zoom. **Create chapter film** renders the
same beats to a 1920×1080 MP4 with 1.2-second dissolves and subtitles.

- **Key images, not every sentence.** The planner groups 3–20 sentences into one
  representative beat, so a 7-minute chapter is a handful of images instead of
  dozens. Subtitles still follow every spoken sentence.
- **Painted ahead, then served.** A background worker renders key stills into
  `film/<doc>/scenes/` inside the data directory. The Manga view serves cached
  frames and shows a "Painting scene…" pill otherwise.
- **A cast per book.** The prompt writer extracts the recurring cast once; each
  character gets a clean full-body portrait on white. The film anchors to the
  portrait of the character named in the beat, using the previous frame only
  between anchors so faces and costumes do not drift.
- **Shot variety.** Framing rotates through establishing wide, medium two-shot,
  close-up, over-the-shoulder, low-angle and detail inserts instead of defaulting to
  a wide low-angle shot every time.
- **Monochrome, cleaned.** Every still is converted to true grayscale, blank panel
  edges are trimmed, and split spreads are collapsed to one panel. If OCR finds
  speech bubbles or lettering, FLUX edits only that frame to remove the text while
  keeping the composition.
- **Local only.** High quality uses **FLUX.2 klein 4B** (Apache-2.0) through
  **mflux** (MIT) at 1920×1080, four steps. Fast uses Sana 0.6B at 1280×720. Switch
  with **Settings → Manga quality**.
- **Steady motion.** No alternating Ken Burns direction or panning. Each image zooms
  1.00 → 1.04 at the centre, rendered at 2560×1440/30 and downsampled to
  1920×1080/30. Beats dissolve into each other over 1.2 s.
- **Background film queue.** **Create chapter film** queues the job and returns
  immediately. You can keep reading, watch Manga, or queue another chapter; each job
  shows Painting → Narrating → Encoding → Ready with its own Download, Cancel and
  Dismiss controls.
- **Watch while painting.** Manga playback starts narration immediately instead of
  waiting for the whole chapter to be painted. The current scene loads on demand and
  the next two scenes are prefetched.
- **Cached re-films.** A chapter film is reused instantly when its plan, images,
  voice, quality, and render settings are unchanged. Changing one still re-renders
  only that chapter.
- **Encoding.** Film export uses the system `ffmpeg` (`brew install ffmpeg`). If that
  build includes libass, karaoke subtitles are burned in; otherwise they are attached
  as a soft `mov_text` track so the film still plays. H.264 + AAC, hardware encoded
  where possible.

## Install the high-quality engine once

```bash
./scripts/setup_flux.sh
```

It creates `image-venv` inside the data directory, installs mflux, and downloads the
4.6 GB FLUX.2 klein q4 model. The Murmur engine falls back to Sana if it is missing.

Rendering is a short batch: a 30-minute chapter is roughly 20–30 key images,
typically 45–90 minutes at High on an M3 Pro including text cleanup. Re-filming an
unchanged chapter is instant.

## GPU support

| Platform | Films |
|---|---|
| macOS Apple Silicon | MPS + MLX, full quality |
| macOS Intel | CPU only, opt-in |
| Windows / Linux, NVIDIA | CUDA |
| Windows / Linux, CPU | opt-in (`MURMUR_ALLOW_CPU_FILM=1`), very slow |
| AMD, Intel | not yet — see [PLATFORMS.md](PLATFORMS.md) |

## Murmur engine sidecar

The film pipeline can run as a standalone local service, separate from the Rhapsode
window:

```bash
./scripts/run_engine.sh
# or: MURMUR_ENGINE_PORT=8931 .venv/bin/python -m murmur.engine_api
```

It exposes the same film contract as the app:

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
