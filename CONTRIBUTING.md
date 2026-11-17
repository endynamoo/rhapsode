# Contributing

Rhapsode is one person directing AI agents. These rules exist so that "it works" is a
verifiable claim rather than a hopeful one, and so the build you are running is always
identifiable.

## The loop

```
issue ──▶ branch ──▶ pull request ──▶ CI green ──▶ merge to main
                                                    │
                                    main ──ff──▶ testing ──ff──▶ stable (+ tag)
```

One direction only. `main` receives new work, `testing` freezes it for QA, `stable` is what
ships. Full detail, including hotfix and rollback: [`docs/BRANCHING.md`](docs/BRANCHING.md).

**Never push to `testing` or `stable` by hand.** Use `scripts/promote.ps1`; it enforces a
clean tree and fast-forward-only, which is what keeps every promotion reversible.

## Before you push

```powershell
python -m unittest discover -s tests -v
```

CI (`windows.yml`) runs the same suite on `windows-latest` for every push to `main`, `testing`
and `stable`. If it is red, nothing gets promoted — fix forward, do not force.

The suite deliberately excludes the TTS weights, the film stack, pywebview and `winsdk`, so it
stays a fast unit/integration check. That means green CI does **not** prove the app runs —
install it, read a page, render a still. Say which of those you did.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/): `feat(film): …`,
`fix(ingest): …`, `docs: …`, `chore: …`, `test: …`.

- One logical change per commit. If the message needs an "and", split it.
- The body says **why**, not what — the diff already says what.
- Never commit: `.venv/`, `__pycache__/`, `*.log`, `*_err.txt`, `build/`, `dist/`, model
  weights or TTS voices. They are gitignored; keep them that way.

## Pull requests

Even solo, open a PR: it gives you a diff to read, a CI verdict, and a record of intent.

A PR is mergeable when:

- it does one thing, and the title says which;
- the description states **what you verified** and how (not "tests pass" — which tests, and
  which manual step);
- CI is green on the head commit;
- nothing outside the stated scope changed. A drive-by refactor belongs in its own PR.

## Releases

```powershell
.\scripts\promote.ps1 -To testing
.\scripts\promote.ps1 -To stable -Tag "what you actually verified"
```

Then publish the version:

```powershell
gh release create v0.2.0 --target stable --generate-notes --title "v0.2.0"
```

`v*` tags are the product version (semantic versioning, with `-alpha`/`-beta` suffixes while
a platform is still preview); `known-good-<date>` tags are the QA anchor for every promotion
to `stable`. Both are immutable — annotate them with what was checked, not what you hope.

## Working with agents

An agent may branch, commit and open a PR against `main`. It may **not** promote to `testing`
or `stable` — that is a judgement call about readiness, not correctness.

When reviewing agent work, in this order: diff size → files touched → tests added *or changed*
(an agent quietly deleting a failing test is the most common cheat) → CI → whether the
description matches the diff.

Ask for evidence: the test output, the screenshot, the `git diff --stat`. "Done" without
evidence is not done.

## Where things live

- `AGENTS.md` — install paths, supported platforms, and the rules an agent must not break.
- `docs/BRANCHING.md` — the ladder, hotfixes, rollback.
- `docs/CONCURRENCY.md` — several agents, one repo: worktrees, who pushes, how to untangle.
- `docs/ARCHITECTURE.md` — modules, API, security model.
- `docs/PLATFORMS.md`, `docs/STILLS.md` — GPU/packaging plan and the stills pipeline.
- `THIRD_PARTY.md` — licences for weights and bundled assets.
