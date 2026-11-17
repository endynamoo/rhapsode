# Branching

Three long-lived branches, one direction of travel:

```
feature/*  ──PR──▶  main  ──ff──▶  testing  ──ff──▶  stable
                     │                                │
                     └── every commit builds ─────────┘  (known-good-<date> tag)
```

| branch | what lives there | moves when |
|---|---|---|
| `main` | the integration trunk. Every push runs `windows.yml` (`python -m unittest discover -s tests`). Nothing lands here broken. | a feature branch merges |
| `testing` | a candidate cut from `main`, being run against real books and real GPUs. | `main` looks ready to try out |
| `stable` | the last build known to work end to end. This is what you install. | `testing` survives QA |

Promotion is **fast-forward only**. `testing` never contains anything `main` doesn't, and
`stable` never contains anything `testing` doesn't — so a promotion can always be undone by
moving the branch back, and `git log stable..main` always answers "what is coming".

## Everyday work

```powershell
git switch -c feat/hand-gate main
# ... commits ...
git switch main
git merge --ff-only feat/hand-gate      # or open a PR and squash it
git push origin main
```

## Cut a candidate

```powershell
git switch testing
git merge --ff-only origin/main
git push origin testing
```

CI runs on `testing` too, so the candidate is tested at the commit it will ship as.

## Promote to stable

```powershell
git switch stable
git merge --ff-only origin/testing
git push origin stable
git tag -a known-good-2026.11.17 -m "stable promoted from testing: <what changed>"
git push origin known-good-2026.11.17
```

The tag is the point of the ladder: `stable` is a moving pointer, the tag is an immutable
anchor. Rollback is a checkout, not archaeology. Use `known-good-YYYY.MM.DD`, and `-2`, `-3`…
if you promote twice in a day.

`scripts/promote.ps1` does the cut, promote and tag steps with the guards below; run it with
`-DryRun` first.

## Hotfix

A bug in `stable` that `main` should not ship yet:

```powershell
git switch -c hotfix/film-crash stable
# ... fix + test ...
git switch stable && git merge --ff-only hotfix/film-crash && git push origin stable
git tag -a known-good-2026.11.18 -m "hotfix: film crash"
git push origin known-good-2026.11.18
git switch main && git merge --ff-only hotfix/film-crash && git push origin main
```

Then carry it through `testing` the next time you cut one, so the fix is never missing from a
later promotion.

## Rolling back

```powershell
git tag --list 'known-good-*' --sort=-creatordate | Select-Object -First 5
git switch stable
git reset --hard known-good-2026.11.10
git push --force-with-lease origin stable
```

`--force-with-lease` (never plain `--force`): it refuses if someone else moved the branch since
your last fetch.
