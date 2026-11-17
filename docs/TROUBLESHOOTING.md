# When it will not paint

Three guards stand between a request and the model, and each one says why it refused
instead of failing quietly.

| Guard | Reads | Default | Refuses when |
|---|---|---|---|
| VRAM | `torch.cuda.mem_get_info()` | `MURMUR_FLUX_RESERVE_MB=2500` | the model plus the reserve does not fit |
| System memory | `GlobalMemoryStatusEx` | `MURMUR_FLUX_RAM_FLOOR_MB=6000` | free physical RAM is under the floor |
| Profile | `engine.json` | `balanced` | the profile is `cpu` (placeholder painter) |

The wording matters: a refusal names the number that was too small, for example
*"refusing to paint: only 631 MiB of 31,908 MiB system memory is free (floor 6,000
MiB)"*. If the engine panel in Settings shows **"below the RAM floor - painting will
refuse"**, that is the reason, not a slow model.

## Why RAM matters as much as VRAM

Measured on this machine: a 1080p still with the q4 transformer and the text encoder
on the GPU holds ~13 GiB of VRAM, and loading it needs 14–16 GiB of system RAM on the
way. On a 32 GiB box that shares memory with other work — a diffusion server, a
browser, a game launcher — the machine can stall before Windows has a chance to log
anything. Three hard locks in one day (Kernel-Power 41, no bugcheck dump, no
Resource-Exhaustion event) happened exactly that way.

If something else is holding several gigabytes, the honest choices are: stop it, use
the `lite` profile (encoder on the CPU, ~5 GiB of VRAM), or raise the floor knowingly.

## Where the evidence is

| File | What it holds |
|---|---|
| `%LOCALAPPDATA%\Rhapsode\app.log` | the app's own stdout/stderr plus a session banner, and a Python stack dump from `faulthandler` on a hard crash |
| `%LOCALAPPDATA%\Rhapsode\painter.log` | the resident painter's stderr: pipeline loading, OOM notices, a profile refusal |
| `%LOCALAPPDATA%\Rhapsode\server-<stamp>.log` | written by `run.ps1` (one pair per launch) |
| `%LOCALAPPDATA%\Rhapsode\film\<doc>\scenes\*.polish.json` | what the polish pass found and did on each still |
| `/api/health` | `profile`, `polish`, `painter` (with `last_job`: seconds, size, steps, `peak_gib`, `free_gib`, `ram_free_gib`), `caches` |

## Failure modes we have already hit

- **A still that exists is never repainted.** A cache hit used to run the gutter and
  lettering cleanup, which meant a real generation on *existing* art, a rewritten PNG
  and a destructive crop — and it never converged, because each repaint tripped the
  detector again. Fixed: the cache hit is inert; the detector now needs a near-uniform
  band flanked by ink.
- **An orphaned painter pins the GPU.** Every painter — resident or one-shot — is now
  spawned into a kill-on-close job object, so it dies with the app whichever way the
  app goes.
- **One OOM killed the resident painter.** It now moves the text encoder to the CPU,
  frees the cache and retries the same job, logging the switch to `painter.log`.
- **A windowed build with no console logged nothing.** `desktop.main` opens `app.log`
  and points `faulthandler` at it before anything else runs.

## What is not a bug

- `done=11/29 in 10 s` at the start of a chapter render: those are cached stills,
  counted at about a second each.
- Polish skipping a still with `reason: no people`: the detector looked and declined.
- `hand_repair: disabled`: the masked hand repair is experimental and off by default.
