---
description: Launch the Centella orchestrator on a task. Use when the user asks to autonomously decompose and execute an engineering task with centella.
argument-hint: <task description>
---

# Launch Centella

The user wants to run the Centella orchestrator on this task:

```
$ARGUMENTS
```

Centella is a deterministic Python orchestrator (it does not run inside this
session — it spawns its own `claude -p` workers). Launch it and relay the
clarification step if one occurs.

## Steps

1. Run the orchestrator from the current repository root:

   ```
   bash "${CLAUDE_PLUGIN_ROOT}/centella" "$ARGUMENTS"
   ```

2. **If it exits with code 10**, the orchestrator needs the user to answer
   clarification questions before it can continue. Read
   `.centella/pending-questions.json`, present each question to the user
   verbatim (and the source-of-truth choice if `source_of_truth` is `true`),
   and collect their answers.

3. Write the answers as a JSON object to `.centella/answers.json`, keyed by each
   question's `id`, plus `source_of_truth` set to either `existing-patterns` or
   `researched-standards` if it was asked. Then resume:

   ```
   bash "${CLAUDE_PLUGIN_ROOT}/centella" --resume --answers .centella/answers.json
   ```

   (If `--resume` reports the run had not reached scheduling, re-run without
   `--resume`, passing the original task and `--answers .centella/answers.json`.)

4. Relay the orchestrator's final summary to the user. On any non-zero, non-10
   exit, show the error and point them at `.centella/state.json`.

For long runs, prefer telling the user to run `centella` directly in a terminal —
this session's context fills with orchestrator output otherwise.
