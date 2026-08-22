---
name: Bug report
about: Report a defect in Leerie
labels: bug
---

## What happened

<!-- What did Leerie do that it shouldn't have? -->

## What you expected

## Reproduction

- **Task you ran:** the exact `leerie "..."` invocation, or slash-command form
- **Repo state:** branch, dirty/clean, any non-default `leerie.toml`
- **Flags used:** `--source-of-truth`, `--model`, `--max-workers`, etc.

## Environment

- OS:
- Container runtime: `colima version` (macOS) or `nerdctl --version` (Linux)
- Image tag: `nerdctl images leerie --format '{{.Repository}}:{{.Tag}}'`
- `claude --version`
- Leerie commit: `git -C ~/.leerie rev-parse HEAD`

## Relevant state

Paste relevant fields from `.leerie/state.json` (redact anything sensitive).

```json
{ }
```
