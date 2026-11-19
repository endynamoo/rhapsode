# The demo clips

The GIFs in the README, and the MP4s beside them, were recorded from this build with a
headless browser driving the real app - not storyboarded, not composited.

## What is being played

`corpus/` holds the three files the clips use, and all three are written for the demo:

| File | Why |
|---|---|
| `the-lantern-keeper.epub` | Three chapters with real headings, so Book view renders its own typography and the highlight runs over chapter text |
| `the-fog-ledger.pdf` | One page saved as an image with no text layer at all, so the OCR path and the on-page word box are the real thing |
| `the-lantern-keeper.txt` | The same prose as plain text, for the simplest reader path |

They are small and public-domain-in-spirit (written for this repository), which is why they
can live here: a clip of someone's real library would publish their reading.

## How a clip is made

1. Serve the app against a throwaway data directory and import `corpus/`:
   `$env:MURMUR_PORT="8891"; $env:MURMUR_HOME="$env:TEMP\rh-demo"; python -m murmur`
2. Drive it with Playwright (Edge on Windows, Chromium elsewhere), recording the viewport:
   viewport 1200x760 at device scale 2, `record_video_dir` into a scratch folder,
   `--autoplay-policy=no-user-gesture-required` so a scripted click may start audio, and a
   real `click()` on Play rather than `evaluate()`, because the browser only honours the
   gesture when it is trusted.
3. Trim and convert with ffmpeg - the palettes matter for text:

```bash
ffmpeg -ss 0.9 -t 9 -i clip.webm \
  -vf "fps=11,scale=940:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  -loop 0 clip.gif

ffmpeg -ss 0.9 -t 9 -i clip.webm -vf "scale=940:-2:flags=lanczos" \
  -c:v libx264 -preset slow -crf 25 -pix_fmt yuv420p -movflags +faststart -an clip.mp4
```

The GIF is what GitHub renders inline; the MP4 is the same clip without dithering, for
anyone who wants to see the motion properly. Both are silent on purpose - the point is the
mark following the voice, not the voice.

`manga.gif` is encoded harder than the other three - 760 px, 6 fps, 48 colours - because a
painted still is photographic: full dithering pushed the same six seconds past 26 MB. The
MP4 beside it keeps the image intact.

## The still in `manga.gif`

That still was painted by the local stills engine (FLUX.2 [klein] 4B, `balanced` profile) on
the machine that recorded the clip, then played against the narration. Painting one frame
takes on the order of a minute on a current card; the clip therefore starts after the scene
is ready, because watching a progress bar is not what the feature is.
