# The demo clips

The GIFs in the README, and the MP4s beside them, were recorded from this build with a
headless browser driving the real app - not storyboarded, not composited.

## The clips

| Clip | What it shows | Still | Motion |
|---|---|---|---|
| Reading | The spoken word marked as the voice reads | `reading.gif` | `reading.mp4` |
| Scanned page | OCR'd page marked on the image, no text layer | `scanned-page.gif` | `scanned-page.mp4` |
| Search | Nine passages across three books, matched words shown | `search.gif` | `search.mp4` |
| Manga | A chapter read over a painted still | `manga.gif` | `manga.mp4` |
| Translation | English text read in Japanese | `translation.gif` | `translation.mp4` (with sound) |

The GIFs are what the front page shows inline; the MP4s are the same recordings without GIF
dithering, at the full frame rate. Both are quiet unless the row says otherwise.

## What is being played

`corpus/` holds the three files the clips use, and all three are written for the demo:

| File | Why |
|---|---|
| `the-lantern-keeper.epub` | Three chapters with real headings, so Book view renders its own typography and the highlight runs over chapter text |
| `the-fog-ledger.pdf` | One page saved as an image with no text layer at all, so the OCR path and the on-page word box are the real thing |
| `the-lantern-keeper.txt` | The same prose as plain text, for the simplest reader path |
| `translation-sample.txt` | One English sentence, for the English-to-Japanese clip: the translation bar and the Japanese voice need a document short enough to hear whole |

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

## The one clip with sound

`translation.mp4` carries audio; the rest are silent on purpose. Playwright records picture
only, so the narration is muxed afterwards from the WAV the browser actually fetched - the
app's own cache file at `/api/audio/<key>.wav`, not a second synthesis - and the offset comes
from the video itself: the play button is a fixed white circle, so its triangle becoming two
bars marks the frame where playback started, and the audio is delayed by the difference. That
is also why the clip is 11 seconds for a 7.9-second sentence: the reader, the marked word and
the translation bar are on screen for a second before the voice arrives.

The translation itself is whatever local model the machine offers. The clip was recorded with
`MURMUR_TRANSLATE_URL` pointed at a local server on port 8091; the alternative is Apple's
Translation framework on macOS. Nothing about the picture is retouched - the wording, including
any word a model leaves in English, is exactly what was spoken.

## The still in `manga.gif`

That still was painted by the local stills engine (FLUX.2 [klein] 4B, `balanced` profile) on
the machine that recorded the clip, then played against the narration. Painting one frame
takes on the order of a minute on a current card; the clip therefore starts after the scene
is ready, because watching a progress bar is not what the feature is.
