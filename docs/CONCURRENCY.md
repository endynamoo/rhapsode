# Several agents, one repository

Rhapsode is worked on by more than one session at a time. The branch ladder protects the
*remote* pointers; it does nothing about three processes sharing one working tree. This page
covers the part it cannot.

## The failure that actually happens

Two agents in `C:\Apps\Rhapsode` at once:

- Agent A runs `git switch feat/x` while Agent B has unsaved edits — B's work disappears from
  disk mid-task.
- Agent A runs `git add -A` and commits B's half-finished work under A's message.
- Both edit the same file; the second commit silently reverts the first (this happened on
  2026-11-17 with `AGENTS.md`, caught only because a third session noticed).

None of that shows up as a merge conflict, because it never gets to git's merge machinery.

## Rules

1. **One worktree per agent.**
   ```powershell
   git worktree add ..\rhapsode-feat-name feat/name
   cd ..\rhapsode-feat-name
   ```
   Each worktree has its own index and working files, sharing one object store and one set of
   branches. A branch can only be checked out in one worktree, so git itself enforces the rule
   once you adopt it.
   ```powershell
   git worktree list
   git worktree remove ..\rhapsode-feat-name      # when the branch is merged and gone
   ```

2. **One pusher to `main`.** Other agents open a PR. If two agents must both land work, they
   land it one after the other, and the second one rebases (`git pull --rebase`) before pushing.

3. **`git add -A` is banned in a shared checkout.** Add explicit paths:
   `git add app.py tests/test_jobs.py`. It is fine inside a dedicated worktree.

4. **A PR for anything touching more than one file** — even solo, even for an agent. The diff
   is the only place a cross-file collision becomes visible before it lands.

5. **Check the ladder at the end of a session:**
   ```powershell
   .\scripts\check-ladder.ps1
   ```
   Exit code 1 means someone bypassed the ladder. Do not force anything; find the pointer that
   moved and restore it to the newest `known-good-*` tag.

## What is *not* a problem

- Several agents reading the same clone. Reads are safe; only writes and `git switch` race.
- Dependabot PRs piling up. They are a decision queue, not a collision.
- `main` being ahead of `stable`. That is the ladder doing its job.

## When two agents do collide

1. Do not `git checkout -- .` or reset to "clean up" — that deletes the other agent's work.
2. `git status` and `git diff` first: whose change is on disk?
3. If both changes are needed, keep whichever is in the tree, apply the other from its commit
   (`git show <sha> -- <path>`), then commit both deliberately.
4. If the remote moved under you, `git pull --rebase`, never `git push --force`.

