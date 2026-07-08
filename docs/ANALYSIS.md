{
  "preprocess": {
    "path": "orchestrator/leerie.py",
    "sha256": "f503244d2ca15490754a84d224bf122d974311d14322dcf8d448d75719a7df17",
    "lines": 16622,
    "bytes": 778009,
    "module_doc": "Leerie \u2014 deterministic task orchestrator for Claude Code."
  },
  "lex": {
    "total_tokens": 89335,
    "token_class_counts": {
      "OP": 32334,
      "NAME": 26080,
      "NL": 7645,
      "NEWLINE": 6364,
      "STRING": 5443,
      "COMMENT": 3019,
      "INDENT": 2034,
      "DEDENT": 2034,
      "FSTRING_MIDDLE": 1749,
      "FSTRING_START": 1017,
      "FSTRING_END": 1017,
      "NUMBER": 598,
      "ENDMARKER": 1
    },
    "keyword_counts": {
      "if": 1140,
      "in": 736,
      "None": 641,
      "return": 560,
      "for": 549,
      "not": 474,
      "or": 405,
      "def": 320,
      "and": 185,
      "is": 173,
      "await": 156,
      "True": 140,
      "except": 134,
      "continue": 133,
      "else": 125,
      "try": 124,
      "False": 98,
      "async": 84,
      "raise": 57,
      "as": 44,
      "break": 39,
      "elif": 33,
      "pass": 32,
      "import": 26,
      "lambda": 17
    },
    "top_identifiers": {
      "str": 826,
      "get": 815,
      "dict": 444,
      "st": 407,
      "sid": 325,
      "s": 299,
      "list": 291,
      "append": 287,
      "r": 281,
      "repo_root": 279,
      "data": 200,
      "log": 186,
      "len": 184,
      "Path": 175,
      "e": 161,
      "caps": 153,
      "int": 151,
      "out": 137,
      "plans": 127,
      "args": 122,
      "set": 117,
      "plan": 116,
      "die": 109,
      "self": 109,
      "strip": 107,
      "parts": 95,
      "join": 95,
      "entry": 92,
      "f": 89,
      "bool": 86,
      "p": 84,
      "line": 84,
      "c": 81,
      "output": 81,
      "leerie_dir": 72,
      "models": 72,
      "efforts": 72,
      "tag": 70,
      "is_file": 70,
      "name": 68,
      "cli_value": 67,
      "ValueError": 65,
      "run_id": 65,
      "save": 65,
      "v": 64,
      "tuple": 62,
      "w": 62,
      "lines": 62,
      "subtasks": 60,
      "i": 58,
      "json": 57,
      "isinstance": 57,
      "n": 57,
      "result": 57,
      "asyncio": 56,
      "proc": 56,
      "os": 55,
      "text": 55,
      "issues": 55,
      "cwd": 54
    },
    "top_operators": {
      "(": 4776,
      ")": 4776,
      ",": 4232,
      ":": 3926,
      ".": 2958,
      "=": 2929,
      "[": 1928,
      "]": 1928,
      "{": 1718,
      "}": 1718,
      "->": 310,
      "/": 213,
      "|": 175,
      "==": 173,
      "!": 130,
      "+": 97,
      "!=": 58,
      "*": 56,
      "-": 46,
      ">": 43,
      "+=": 41,
      "<": 38,
      ">=": 22,
      "<=": 17,
      "**": 10
    },
    "comment_chars": 174745,
    "string_literal_count": 5443,
    "env_vars": [
      "LEERIE_CAPTURE_DEPS",
      "LEERIE_CLARIFY",
      "LEERIE_CONFIDENCE_ROUNDS",
      "LEERIE_DANGEROUSLY_ALLOW_UNCAPPED",
      "LEERIE_DANGEROUSLY_SKIP_PERMISSIONS",
      "LEERIE_EFFORT",
      "LEERIE_EFFORT_",
      "LEERIE_HEAL_DIR",
      "LEERIE_HEAL_MAX_ROUNDS",
      "LEERIE_HEAL_SUCCESS_THRESHOLD",
      "LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES",
      "LEERIE_INSPECT_DIRS",
      "LEERIE_JUDGE_DIR",
      "LEERIE_JUDGMENT_CHECK_ROUNDS",
      "LEERIE_MAX_PARALLEL",
      "LEERIE_MAX_WORKERS",
      "LEERIE_MODEL",
      "LEERIE_MODEL_",
      "LEERIE_MODEL_HEAL",
      "LEERIE_MODEL_JUDGE",
      "LEERIE_MODEL_PR_WRITER",
      "LEERIE_NO_PUSH",
      "LEERIE_PLANNER_CHECK_ROUNDS",
      "LEERIE_PLANNER_SAMPLES",
      "LEERIE_PR_TEMPLATE",
      "LEERIE_RUNTIME",
      "LEERIE_SKIP_BUDGET_CHECK",
      "LEERIE_SKIP_OVERLAP_JUDGE",
      "LEERIE_SKIP_SATISFIED_CHECK",
      "LEERIE_SOURCE_OF_TRUTH",
      "LEERIE_STATE_DIR",
      "LEERIE_STATE_HOST_DIR",
      "LEERIE_STRICT_CONFORMER",
      "LEERIE_TELEMETRY",
      "LEERIE_TELEMETRY_DIR",
      "LEERIE_VERBOSITY",
      "LEERIE_WORKER_DEBUG",
      "LEERIE_WORKER_MEMORY_MAX"
    ],
    "exit_symbols": [
      "EXIT_BUDGET_INFEASIBLE",
      "EXIT_LOCKED",
      "EXIT_NEEDS_ANSWERS"
    ]
  },
  "ast": {
    "node_class_counts": {
      "Load": 16960,
      "Name": 14643,
      "Constant": 7788,
      "Call": 3963,
      "Store": 3067,
      "Attribute": 2957,
      "Assign": 1933,
      "Expr": 1279,
      "Subscript": 1276,
      "FormattedValue": 1197,
      "If": 1005,
      "arg": 750,
      "Compare": 706,
      "keyword": 696,
      "JoinedStr": 676,
      "List": 570,
      "Tuple": 563,
      "Return": 560,
      "BinOp": 539,
      "BoolOp": 520,
      "Dict": 474,
      "For": 359,
      "Or": 356,
      "arguments": 337,
      "UnaryOp": 300,
      "Not": 271,
      "FunctionDef": 245,
      "AnnAssign": 218,
      "Div": 213,
      "comprehension": 187,
      "BitOr": 176,
      "Eq": 173,
      "And": 164,
      "Await": 156,
      "Add": 138,
      "ExceptHandler": 134,
      "Continue": 133,
      "Try": 124,
      "IsNot": 123,
      "In": 107,
      "ListComp": 82,
      "IfExp": 81,
      "NotIn": 80,
      "AsyncFunctionDef": 75,
      "GeneratorExp": 71,
      "NotEq": 58,
      "Raise": 57,
      "Is": 50,
      "AugAssign": 46,
      "Slice": 45,
      "Gt": 43,
      "Break": 39,
      "Lt": 38,
      "alias": 36,
      "Pass": 32,
      "USub": 29,
      "Mult": 23,
      "GtE": 22,
      "Starred": 20,
      "Import": 19,
      "Sub": 19,
      "SetComp": 17,
      "withitem": 17,
      "Lambda": 17,
      "LtE": 17,
      "While": 16,
      "DictComp": 16,
      "Set": 14,
      "With": 11,
      "ClassDef": 8,
      "ImportFrom": 7,
      "AsyncWith": 6,
      "Pow": 6,
      "FloorDiv": 5,
      "BitAnd": 5,
      "Nonlocal": 4,
      "AsyncFor": 3,
      "Delete": 2,
      "Del": 2,
      "Module": 1,
      "Global": 1,
      "Yield": 1,
      "NamedExpr": 1
    },
    "imports": [
      "from __future__ import annotations",
      "argparse",
      "asyncio",
      "contextlib",
      "copy",
      "fcntl",
      "json",
      "os",
      "re",
      "shutil",
      "signal",
      "socket",
      "subprocess",
      "sys",
      "time",
      "uuid",
      "from collections import deque",
      "from collections.abc import Awaitable, Callable, Iterator",
      "from datetime import datetime, timedelta, timezone",
      "from pathlib import Path",
      "from zoneinfo import ZoneInfo, ZoneInfoNotFoundError",
      "from tenacity import AsyncRetrying, RetryCallState, RetryError, retry_if_result, stop_after_delay, wait_exponential_jitter"
    ],
    "constant_count": 164,
    "function_count": 257,
    "class_count": 8
  },
  "constants": [
    {
      "name": "ROOT",
      "lineno": 53,
      "kind": "expr",
      "expr": "Path(__file__).resolve().parent.parent"
    },
    {
      "name": "PROMPTS",
      "lineno": 54,
      "kind": "expr",
      "expr": "ROOT / 'prompts'"
    },
    {
      "name": "SCRIPTS",
      "lineno": 55,
      "kind": "expr",
      "expr": "ROOT / 'scripts'"
    },
    {
      "name": "_PROMPT_INCLUDE_RE",
      "lineno": 73,
      "kind": "expr",
      "expr": "re.compile('\\\\{\\\\{\\\\s*include:\\\\s*(_[a-z0-9_]+\\\\.md)\\\\s*\\\\}\\\\}')"
    },
    {
      "name": "MIN_CLAUDE_CLI",
      "lineno": 92,
      "kind": "tuple",
      "len": 3,
      "values": [
        2,
        1,
        22
      ]
    },
    {
      "name": "DEFAULT_CAPS",
      "lineno": 95,
      "kind": "dict",
      "len": 17,
      "keys": [
        "max_total_workers",
        "max_parallel",
        "subtask_continuations",
        "failed_retries",
        "conformance_rounds",
        "judgment_check_rounds",
        "planner_check_rounds",
        "implementer_confidence_retries",
        "planner_samples",
        "worker_timeout_sec",
        "worker_idle_warn_sec",
        "confidence_rounds",
        "worker_memory_max_bytes",
        "worker_pids_max",
        "auth_retry_max_sec",
        "subtask_call_estimate",
        "budget_safety_margin"
      ]
    },
    {
      "name": "STATE_FIELDS",
      "lineno": 200,
      "kind": "tuple",
      "len": 38,
      "values": [
        "task",
        "started_at",
        "finished_at",
        "waves",
        "completed_waves",
        "subtask_status",
        "blocked",
        "worker_count",
        "telemetry",
        "categories",
        "classifier_questions",
        "answers",
        "needs_source_of_truth",
        "source_of_truth_pref",
        "clarify",
        "dangerously_skip_permissions",
        "skip_overlap_judge",
        "skip_satisfied_check",
        "skip_budget_check",
        "strict_conformer",
        "cgroup_containment",
        "verbosity",
        "inspect_dirs",
        "integrator_warnings",
        "scope_warnings",
        "conformance",
        "provision",
        "external_preconditions",
        "current_phase",
        "dropped_subtasks",
        "conditional_drops",
        "speculative_collapse_drops",
        "plan_overlap_judge",
        "plan_overlap_applied",
        "no_work_required",
        "no_work_reasons",
        "working_branch",
        "leerie_version"
      ]
    },
    {
      "name": "CATEGORIES",
      "lineno": 285,
      "kind": "list",
      "len": 9,
      "values": [
        "feature-implementation",
        "bug-fixing",
        "refactoring",
        "performance-optimization",
        "testing",
        "dependency-migration",
        "configuration-build",
        "infrastructure",
        "documentation"
      ]
    },
    {
      "name": "CATEGORY_ABBREV",
      "lineno": 294,
      "kind": "dict",
      "len": 9,
      "keys": [
        "feature-implementation",
        "bug-fixing",
        "refactoring",
        "performance-optimization",
        "testing",
        "dependency-migration",
        "configuration-build",
        "infrastructure",
        "documentation"
      ]
    },
    {
      "name": "_PROTECTED_PREFIXES",
      "lineno": 320,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".leerie/",
        ".git/"
      ]
    },
    {
      "name": "_CLAUDE_DELIVERABLE_PREFIXES",
      "lineno": 321,
      "kind": "tuple",
      "len": 3,
      "values": [
        ".claude/agents/",
        ".claude/commands/",
        ".claude/skills/"
      ]
    },
    {
      "name": "_READ_BASE",
      "lineno": 336,
      "kind": "str",
      "value": "Read,Grep,Glob,WebSearch,WebFetch"
    },
    {
      "name": "INSPECT_TOOLS",
      "lineno": 360,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(find:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(tree:*),Bash(pwd),Bash(echo:*),Bash(git log:*),Bash(git s"
    },
    {
      "name": "ACT_TOOLS",
      "lineno": 368,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash,Write,Edit'"
    },
    {
      "name": "SATISFIED_PROBE_TOOLS",
      "lineno": 382,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(pwd),Bash(echo:*),Bash(git show HEAD:*),Bash(git diff:*),Bash(git status)'"
    },
    {
      "name": "DISALLOWED_TOOLS",
      "lineno": 396,
      "kind": "str",
      "value": "Agent,SendMessage,ScheduleWakeup,CronCreate,CronDelete,CronList,RemoteTrigger,PushNotification"
    },
    {
      "name": "INSPECT_DIRS_ENV",
      "lineno": 410,
      "kind": "str",
      "value": "LEERIE_INSPECT_DIRS"
    },
    {
      "name": "INSPECT_DIRS_FILE",
      "lineno": 411,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EXIT_NEEDS_ANSWERS",
      "lineno": 413,
      "kind": "int",
      "value": 10
    },
    {
      "name": "EXIT_BUDGET_INFEASIBLE",
      "lineno": 424,
      "kind": "int",
      "value": 11
    },
    {
      "name": "EXIT_LOCKED",
      "lineno": 432,
      "kind": "int",
      "value": 75
    },
    {
      "name": "SOURCE_OF_TRUTH_VALUES",
      "lineno": 440,
      "kind": "tuple",
      "len": 3,
      "values": [
        "codebase",
        "research",
        "both"
      ]
    },
    {
      "name": "SOURCE_OF_TRUTH_ENV",
      "lineno": 441,
      "kind": "str",
      "value": "LEERIE_SOURCE_OF_TRUTH"
    },
    {
      "name": "SOURCE_OF_TRUTH_FILE",
      "lineno": 442,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "RUNTIME_VALUES",
      "lineno": 447,
      "kind": "tuple",
      "len": 2,
      "values": [
        "local",
        "fly"
      ]
    },
    {
      "name": "RUNTIME_ENV",
      "lineno": 448,
      "kind": "str",
      "value": "LEERIE_RUNTIME"
    },
    {
      "name": "RUNTIME_FILE",
      "lineno": 449,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CONFIDENCE_ROUNDS_ENV",
      "lineno": 456,
      "kind": "str",
      "value": "LEERIE_CONFIDENCE_ROUNDS"
    },
    {
      "name": "CONFIDENCE_ROUNDS_FILE",
      "lineno": 457,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "JUDGMENT_CHECK_ROUNDS_ENV",
      "lineno": 460,
      "kind": "str",
      "value": "LEERIE_JUDGMENT_CHECK_ROUNDS"
    },
    {
      "name": "PLANNER_CHECK_ROUNDS_ENV",
      "lineno": 461,
      "kind": "str",
      "value": "LEERIE_PLANNER_CHECK_ROUNDS"
    },
    {
      "name": "IMPLEMENTER_CONFIDENCE_RETRIES_ENV",
      "lineno": 462,
      "kind": "str",
      "value": "LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES"
    },
    {
      "name": "PLANNER_SAMPLES_ENV",
      "lineno": 463,
      "kind": "str",
      "value": "LEERIE_PLANNER_SAMPLES"
    },
    {
      "name": "MAX_WORKERS_ENV",
      "lineno": 468,
      "kind": "str",
      "value": "LEERIE_MAX_WORKERS"
    },
    {
      "name": "MAX_WORKERS_FILE",
      "lineno": 469,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "MAX_PARALLEL_ENV",
      "lineno": 474,
      "kind": "str",
      "value": "LEERIE_MAX_PARALLEL"
    },
    {
      "name": "MAX_PARALLEL_FILE",
      "lineno": 475,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "WORKER_MEMORY_MAX_ENV",
      "lineno": 482,
      "kind": "str",
      "value": "LEERIE_WORKER_MEMORY_MAX"
    },
    {
      "name": "WORKER_MEMORY_MAX_FILE",
      "lineno": 483,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "NO_PUSH_ENV",
      "lineno": 491,
      "kind": "str",
      "value": "LEERIE_NO_PUSH"
    },
    {
      "name": "NO_PUSH_FILE",
      "lineno": 492,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CLARIFY_ENV",
      "lineno": 500,
      "kind": "str",
      "value": "LEERIE_CLARIFY"
    },
    {
      "name": "CLARIFY_FILE",
      "lineno": 501,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_ENV",
      "lineno": 511,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_SKIP_PERMISSIONS"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_FILE",
      "lineno": 512,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_ENV",
      "lineno": 526,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_ALLOW_UNCAPPED"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_FILE",
      "lineno": 527,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_ENV",
      "lineno": 536,
      "kind": "str",
      "value": "LEERIE_SKIP_OVERLAP_JUDGE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_FILE",
      "lineno": 537,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_BUDGET_CHECK_ENV",
      "lineno": 549,
      "kind": "str",
      "value": "LEERIE_SKIP_BUDGET_CHECK"
    },
    {
      "name": "SKIP_BUDGET_CHECK_FILE",
      "lineno": 550,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "STRICT_CONFORMER_ENV",
      "lineno": 552,
      "kind": "str",
      "value": "LEERIE_STRICT_CONFORMER"
    },
    {
      "name": "STRICT_CONFORMER_FILE",
      "lineno": 553,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CAPTURE_DEPS_ENV",
      "lineno": 559,
      "kind": "str",
      "value": "LEERIE_CAPTURE_DEPS"
    },
    {
      "name": "CAPTURE_DEPS_CONFIG",
      "lineno": 560,
      "kind": "str",
      "value": ".leerie/config.toml"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_ENV",
      "lineno": 571,
      "kind": "str",
      "value": "LEERIE_SKIP_SATISFIED_CHECK"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_FILE",
      "lineno": 572,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "PR_TEMPLATE_ENV",
      "lineno": 581,
      "kind": "str",
      "value": "LEERIE_PR_TEMPLATE"
    },
    {
      "name": "PR_TEMPLATE_FILE",
      "lineno": 582,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "VERBOSITY_VALUES",
      "lineno": 590,
      "kind": "tuple",
      "len": 4,
      "values": [
        "quiet",
        "normal",
        "stream",
        "debug"
      ]
    },
    {
      "name": "VERBOSITY_DEFAULT",
      "lineno": 591,
      "kind": "str",
      "value": "stream"
    },
    {
      "name": "VERBOSITY_ENV",
      "lineno": 592,
      "kind": "str",
      "value": "LEERIE_VERBOSITY"
    },
    {
      "name": "VERBOSITY_FILE",
      "lineno": 593,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "_TERMINAL_STATUSES",
      "lineno": 596,
      "kind": "expr",
      "expr": "frozenset({'complete', 'failed', 'blocked'})"
    },
    {
      "name": "MODEL_VALUES",
      "lineno": 602,
      "kind": "tuple",
      "len": 3,
      "values": [
        "sonnet",
        "opus",
        "haiku"
      ]
    },
    {
      "name": "MODEL_DEFAULT",
      "lineno": 609,
      "kind": "str",
      "value": "opus"
    },
    {
      "name": "MODEL_DEFAULT_PER_WORKER",
      "lineno": 613,
      "kind": "dict",
      "len": 6,
      "keys": [
        "implementer",
        "conformer",
        "judge",
        "heal",
        "pr_writer",
        "satisfied_probe"
      ]
    },
    {
      "name": "MODEL_ENV",
      "lineno": 625,
      "kind": "str",
      "value": "LEERIE_MODEL"
    },
    {
      "name": "MODEL_FILE",
      "lineno": 626,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EFFORT_VALUES",
      "lineno": 634,
      "kind": "tuple",
      "len": 5,
      "values": [
        "low",
        "medium",
        "high",
        "xhigh",
        "max"
      ]
    },
    {
      "name": "EFFORT_DEFAULT",
      "lineno": 635,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "EFFORT_DEFAULT_PER_WORKER",
      "lineno": 636,
      "kind": "dict",
      "len": 7,
      "keys": [
        "classifier",
        "planner",
        "reconciler",
        "plan_overlap_judge",
        "provision",
        "integrator",
        "pr_writer"
      ]
    },
    {
      "name": "EFFORT_ENV",
      "lineno": 645,
      "kind": "str",
      "value": "LEERIE_EFFORT"
    },
    {
      "name": "WORKER_TYPES",
      "lineno": 646,
      "kind": "tuple",
      "len": 9,
      "values": [
        "classifier",
        "planner",
        "reconciler",
        "plan_overlap_judge",
        "satisfied_probe",
        "provision",
        "implementer",
        "integrator",
        "conformer"
      ]
    },
    {
      "name": "MODEL_JUDGE_ENV",
      "lineno": 652,
      "kind": "str",
      "value": "LEERIE_MODEL_JUDGE"
    },
    {
      "name": "MODEL_HEAL_ENV",
      "lineno": 653,
      "kind": "str",
      "value": "LEERIE_MODEL_HEAL"
    },
    {
      "name": "MODEL_PR_WRITER_ENV",
      "lineno": 654,
      "kind": "str",
      "value": "LEERIE_MODEL_PR_WRITER"
    },
    {
      "name": "TELEMETRY_DEFAULT",
      "lineno": 661,
      "kind": "bool",
      "value": true
    },
    {
      "name": "TELEMETRY_ENV",
      "lineno": 662,
      "kind": "str",
      "value": "LEERIE_TELEMETRY"
    },
    {
      "name": "TELEMETRY_FILE",
      "lineno": 663,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "TELEMETRY_SUBDIR_DEFAULT",
      "lineno": 668,
      "kind": "str",
      "value": "events"
    },
    {
      "name": "TELEMETRY_SUBDIR_ENV",
      "lineno": 669,
      "kind": "str",
      "value": "LEERIE_TELEMETRY_DIR"
    },
    {
      "name": "TELEMETRY_SUBDIR_FILE",
      "lineno": 670,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "JUDGE_DIR_DEFAULT",
      "lineno": 675,
      "kind": "str",
      "value": "judge-out"
    },
    {
      "name": "JUDGE_DIR_ENV",
      "lineno": 676,
      "kind": "str",
      "value": "LEERIE_JUDGE_DIR"
    },
    {
      "name": "JUDGE_DIR_FILE",
      "lineno": 677,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_DIR_DEFAULT",
      "lineno": 682,
      "kind": "str",
      "value": "heal-out"
    },
    {
      "name": "HEAL_DIR_ENV",
      "lineno": 683,
      "kind": "str",
      "value": "LEERIE_HEAL_DIR"
    },
    {
      "name": "HEAL_DIR_FILE",
      "lineno": 684,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_MAX_ROUNDS_DEFAULT",
      "lineno": 689,
      "kind": "int",
      "value": 10
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_DEFAULT",
      "lineno": 690,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "HEAL_PLATEAU_WINDOW_DEFAULT",
      "lineno": 691,
      "kind": "int",
      "value": 3
    },
    {
      "name": "HEAL_PLATEAU_DELTA_DEFAULT",
      "lineno": 692,
      "kind": "float",
      "value": 0.03
    },
    {
      "name": "HEAL_N_REPLAYS_DEFAULT",
      "lineno": 693,
      "kind": "int",
      "value": 5
    },
    {
      "name": "HEAL_MAX_ROUNDS_ENV",
      "lineno": 694,
      "kind": "str",
      "value": "LEERIE_HEAL_MAX_ROUNDS"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_ENV",
      "lineno": 695,
      "kind": "str",
      "value": "LEERIE_HEAL_SUCCESS_THRESHOLD"
    },
    {
      "name": "HEAL_MAX_ROUNDS_FILE",
      "lineno": 696,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_FILE",
      "lineno": 697,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "STATE_DIR_ENV",
      "lineno": 705,
      "kind": "str",
      "value": "LEERIE_STATE_DIR"
    },
    {
      "name": "_CONFORMER_BLT_PROP",
      "lineno": 738,
      "kind": "dict",
      "len": 3,
      "keys": [
        "type",
        "required",
        "properties"
      ]
    },
    {
      "name": "_REQUIRES_ITEM",
      "lineno": 757,
      "kind": "dict",
      "len": 3,
      "keys": [
        "type",
        "required",
        "properties"
      ]
    },
    {
      "name": "SCHEMAS",
      "lineno": 793,
      "kind": "dict",
      "len": 12,
      "keys": [
        "classifier",
        "planner",
        "reconciler",
        "implementer",
        "integrator",
        "judge",
        "conformer",
        "patch_generator",
        "pr_writer",
        "provision",
        "plan_overlap_judge",
        "satisfied_probe"
      ]
    },
    {
      "name": "_SESSION_LIMIT_PREFIX",
      "lineno": 1489,
      "kind": "expr",
      "expr": "re.compile(\"you've hit your session limit\", re.IGNORECASE)"
    },
    {
      "name": "_SESSION_LIMIT_RESET",
      "lineno": 1491,
      "kind": "expr",
      "expr": "re.compile('resets?\\\\s+(\\\\d{1,2}):(\\\\d{2})\\\\s*([ap]m)\\\\s*\\\\(([^)]+)\\\\)', re.IGNORECASE)"
    },
    {
      "name": "_RATE_LIMIT_ALLOWED_STATUSES",
      "lineno": 1500,
      "kind": "tuple",
      "len": 2,
      "values": [
        "allowed",
        "allowed_warning"
      ]
    },
    {
      "name": "_PROC_TREE_GRACE_SEC",
      "lineno": 1558,
      "kind": "float",
      "value": 2.0
    },
    {
      "name": "_DESCENDANT_POLL_SEC",
      "lineno": 1653,
      "kind": "float",
      "value": 0.5
    },
    {
      "name": "_PID_REAP_HIGH_WATER",
      "lineno": 1660,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "_PID_REAP_LOW_WATER",
      "lineno": 1661,
      "kind": "float",
      "value": 0.75
    },
    {
      "name": "_PID_REAP_MIN_AGE_SEC",
      "lineno": 1662,
      "kind": "int",
      "value": 60
    },
    {
      "name": "_PID_EXHAUSTION_WINDOW",
      "lineno": 1677,
      "kind": "int",
      "value": 6
    },
    {
      "name": "_PID_EXHAUSTION_ERROR_THRESHOLD",
      "lineno": 1678,
      "kind": "int",
      "value": 3
    },
    {
      "name": "RUN_STATUSES",
      "lineno": 2528,
      "kind": "tuple",
      "len": 12,
      "values": [
        "seed-failed",
        "corrupt-sidecar",
        "in-progress",
        "incomplete",
        "done",
        "done-pushed-no-pr",
        "done-pushed-pr",
        "push-failed",
        "pr-failed",
        "paused",
        "killed",
        "sync-failed"
      ]
    },
    {
      "name": "TASK_FILE_SUFFIXES",
      "lineno": 2732,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".txt",
        ".md"
      ]
    },
    {
      "name": "_MEMORY_SUFFIX_MULTIPLIER",
      "lineno": 2989,
      "kind": "dict",
      "len": 5,
      "keys": [
        "",
        "K",
        "M",
        "G",
        "T"
      ]
    },
    {
      "name": "_ID_PREFIXES",
      "lineno": 3910,
      "kind": "expr",
      "expr": "frozenset((f'{v}-' for v in CATEGORY_ABBREV.values()))"
    },
    {
      "name": "_VALID_EXTENTS",
      "lineno": 3913,
      "kind": "expr",
      "expr": "frozenset({'in_plan', 'external'})"
    },
    {
      "name": "_MIGRATION_SIGNAL_RE",
      "lineno": 3996,
      "kind": "expr",
      "expr": "re.compile('replac(?:es?|ing)\\\\s+(?:direct\\\\s+)?[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|migrat(?:es?|ing)\\\\s+from\\\\s+[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|extract(?:s|ing)\\\\s+[`\\'\\\\\"]?([a-zA"
    },
    {
      "name": "_MIGRATION_SURFACE_THRESHOLD",
      "lineno": 4004,
      "kind": "int",
      "value": 5
    },
    {
      "name": "_GLOB_CHARS",
      "lineno": 4347,
      "kind": "expr",
      "expr": "frozenset('*?[{')"
    },
    {
      "name": "_BRACE_RE",
      "lineno": 4348,
      "kind": "expr",
      "expr": "re.compile('\\\\{([^}]+)\\\\}')"
    },
    {
      "name": "_MAX_COVERAGE_ITEMS",
      "lineno": 4436,
      "kind": "int",
      "value": 50
    },
    {
      "name": "_ENV_TAG_KEYWORDS",
      "lineno": 4630,
      "kind": "expr",
      "expr": "frozenset({'env', 'bootstrap', 'secret', 'config-key', 'credential'})"
    },
    {
      "name": "_PROVISION_ARGV0_ALLOW",
      "lineno": 4910,
      "kind": "expr",
      "expr": "frozenset({'pnpm', 'npm', 'yarn', 'pip', 'pip3', 'uv', 'poetry', 'pipenv', 'go', 'cargo', 'bundle', 'gem', 'mvn', 'gradle', 'gradlew', 'make', 'composer', 'dotnet'})"
    },
    {
      "name": "_PROVISION_SHELL_METACHARS",
      "lineno": 4921,
      "kind": "expr",
      "expr": "frozenset(set('|&;$`><\\n\\r'))"
    },
    {
      "name": "_README_SECTION_RE",
      "lineno": 5118,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(install|getting[\\\\s-]?started|quick[\\\\s-]?start|setup|usage|\\\\brun\\\\b|develop|build(ing)?( from source| instructions)?|compil(e|ing)( from source)?|download|from source|requirement"
    },
    {
      "name": "_HEADER_DECOR_RE",
      "lineno": 5143,
      "kind": "expr",
      "expr": "re.compile('^[^\\\\w]+', flags=re.UNICODE)"
    },
    {
      "name": "_INSTALL_CMD_HINT_RE",
      "lineno": 5148,
      "kind": "expr",
      "expr": "re.compile('\\\\b(pip|pip3|npm|pnpm|yarn|uv|poetry|cargo|brew|apt|apt-get|dnf|yum|pacman|go install|make|bundle install|gem install|mise install)\\\\b')"
    },
    {
      "name": "_APT_INSTALL_RE",
      "lineno": 5154,
      "kind": "expr",
      "expr": "re.compile('(?:sudo\\\\s+(?:-[A-Za-z]+\\\\s+)*)?apt(?:-get)?\\\\s+install\\\\b')"
    },
    {
      "name": "_LANG_INSTALL_RE",
      "lineno": 5159,
      "kind": "expr",
      "expr": "re.compile('^\\\\s*(?:pnpm\\\\s+install|npm\\\\s+(?:ci|install)|yarn\\\\s+install|pip[23]?\\\\s+install|uv\\\\s+sync|poetry\\\\s+install|cargo\\\\s+fetch|bundle\\\\s+install|composer\\\\s+install|dotnet\\\\s+restore|go\\\\s+"
    },
    {
      "name": "_APT_PKG_RE",
      "lineno": 5176,
      "kind": "expr",
      "expr": "re.compile('^[a-z0-9][a-z0-9+._-]*$')"
    },
    {
      "name": "_APT_FLAG_RE",
      "lineno": 5178,
      "kind": "expr",
      "expr": "re.compile('^-')"
    },
    {
      "name": "_README_INTRO_BUDGET",
      "lineno": 5477,
      "kind": "int",
      "value": 1024
    },
    {
      "name": "_README_EXTRACT_BUDGET",
      "lineno": 5478,
      "kind": "int",
      "value": 8192
    },
    {
      "name": "_README_FALLBACK_BUDGET",
      "lineno": 5479,
      "kind": "int",
      "value": 6144
    },
    {
      "name": "_FIXTURE_TOTAL_BUDGET",
      "lineno": 5480,
      "kind": "int",
      "value": 24576
    },
    {
      "name": "_PROVISION_ROOT_MANIFESTS",
      "lineno": 5554,
      "kind": "tuple",
      "len": 9,
      "values": [
        "package.json",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "Gemfile",
        "Makefile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts"
      ]
    },
    {
      "name": "_PROVISION_WORKFLOW_PREFERRED_RE",
      "lineno": 5559,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(ci|test|build|release)\\\\b')"
    },
    {
      "name": "_PROVISION_WORKFLOW_SKIP_RE",
      "lineno": 5560,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(codeql|stale|dependabot)\\\\b')"
    },
    {
      "name": "_CHECKPOINT_SECTIONS",
      "lineno": 5716,
      "kind": "list",
      "len": 7,
      "values": [
        "## Frozen success criteria",
        "## Current status",
        "## Files touched",
        "## Decisions made",
        "## Evidence gate status",
        "## Next action",
        "## Open unknowns"
      ]
    },
    {
      "name": "_CHECKPOINT_SECTIONS_ALLOW_NONE",
      "lineno": 5729,
      "kind": "set",
      "len": 2,
      "values": [
        "## Decisions made",
        "## Open unknowns"
      ]
    },
    {
      "name": "_NOISE_TOKENS",
      "lineno": 5737,
      "kind": "set",
      "len": 12,
      "values": [
        "none",
        "n/a",
        "na",
        "tbd",
        "nothing",
        "unknown",
        "todo",
        "pending",
        "\u2014",
        "--",
        "-",
        "?"
      ]
    },
    {
      "name": "_FORK_EAGAIN_MARKERS",
      "lineno": 6182,
      "kind": "tuple",
      "len": 4,
      "values": [
        "resource temporarily unavailable",
        "cannot fork",
        "cannot allocate memory",
        "fork: retry"
      ]
    },
    {
      "name": "_CGROUP_BROKER_SOCK",
      "lineno": 6570,
      "kind": "str",
      "value": "/run/leerie-cgroup.sock"
    },
    {
      "name": "_CGROUP_PROBE_RESULT",
      "lineno": 6571,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_CGROUP_HIERARCHY",
      "lineno": 6572,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_DOCS_ONLY_CATEGORIES",
      "lineno": 8527,
      "kind": "expr",
      "expr": "frozenset({'documentation'})"
    },
    {
      "name": "_GO_MOD_VERSION_RE",
      "lineno": 8604,
      "kind": "expr",
      "expr": "re.compile('^\\\\s*go\\\\s+(\\\\d+(?:\\\\.\\\\d+){0,2})\\\\s*$', re.MULTILINE)"
    },
    {
      "name": "_LEADING_V_RE",
      "lineno": 8659,
      "kind": "expr",
      "expr": "re.compile('^[vV]+')"
    },
    {
      "name": "_IDIOMATIC_VERSION_FILES",
      "lineno": 8670,
      "kind": "tuple",
      "len": 4,
      "values": [
        "('.nvmrc', 'node', lambda s: _LEADING_V_RE.sub('', s))",
        "('.node-version', 'node', lambda s: _LEADING_V_RE.sub('', s))",
        "('.python-version', 'python', lambda s: s)",
        "('.ruby-version', 'ruby', lambda s: s)"
      ]
    },
    {
      "name": "_ASDF_TOOL_ALIASES",
      "lineno": 8685,
      "kind": "dict",
      "len": 2,
      "keys": [
        "nodejs",
        "python3"
      ]
    },
    {
      "name": "_MISE_SIGNAL_FILES",
      "lineno": 8912,
      "kind": "tuple",
      "len": 9,
      "values": [
        "mise.toml",
        ".mise.toml",
        ".tool-versions",
        ".nvmrc",
        ".node-version",
        ".python-version",
        ".ruby-version",
        "rust-toolchain.toml",
        ".go-version"
      ]
    },
    {
      "name": "_RETRYABLE_FAILURE_KINDS",
      "lineno": 13399,
      "kind": "expr",
      "expr": "frozenset({'no_commits', 'dirty_worktree', 'empty_handoff'})"
    },
    {
      "name": "_RULES_FILE_CANDIDATES",
      "lineno": 13449,
      "kind": "tuple",
      "len": 16,
      "values": [
        "CLAUDE.md",
        "AGENTS.md",
        ".agent.md",
        ".cursorrules",
        ".windsurfrules",
        "docs/CLAUDE.md",
        "docs/AGENTS.md",
        "docs/CONVENTIONS.md",
        "docs/STYLE.md",
        "docs/DESIGN-SYSTEM.md",
        "docs/DESIGN_SYSTEM.md",
        "docs/UI.md",
        "README.md",
        "CONTRIBUTING.md",
        "docs/DESIGN.md",
        "docs/IMPLEMENTATION.md"
      ]
    },
    {
      "name": "_BLT_AXIS_RES",
      "lineno": 13988,
      "kind": "dict",
      "len": 3,
      "keys": [
        "test",
        "build",
        "lint"
      ]
    },
    {
      "name": "_BG_RESULT_PREFIX",
      "lineno": 14012,
      "kind": "str",
      "value": "Command running in background with ID:"
    },
    {
      "name": "_BG_ID_RE",
      "lineno": 14013,
      "kind": "expr",
      "expr": "re.compile('Command running in background with ID:\\\\s*(\\\\w+)')"
    },
    {
      "name": "_PR_TEMPLATE_SINGLE_LOCATIONS",
      "lineno": 15011,
      "kind": "tuple",
      "len": 3,
      "values": [
        ".github/pull_request_template.md",
        "pull_request_template.md",
        "docs/pull_request_template.md"
      ]
    },
    {
      "name": "_PR_TEMPLATE_MULTI_DIRS",
      "lineno": 15019,
      "kind": "tuple",
      "len": 3,
      "values": [
        ".github/PULL_REQUEST_TEMPLATE",
        "PULL_REQUEST_TEMPLATE",
        "docs/PULL_REQUEST_TEMPLATE"
      ]
    },
    {
      "name": "PR_WRITER_COMMIT_LOG_MAX_BYTES",
      "lineno": 15080,
      "kind": "int",
      "value": 80000
    },
    {
      "name": "PR_WRITER_TEMPLATE_MAX_BYTES",
      "lineno": 15081,
      "kind": "int",
      "value": 32000
    },
    {
      "name": "PR_WRITER_DIFF_SAMPLE_MAX_LINES",
      "lineno": 15082,
      "kind": "int",
      "value": 500
    },
    {
      "name": "PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES",
      "lineno": 15088,
      "kind": "int",
      "value": 8000
    },
    {
      "name": "_LEERIE_PREFIX_RE",
      "lineno": 15116,
      "kind": "expr",
      "expr": "re.compile('^leerie:\\\\s*', re.IGNORECASE)"
    }
  ],
  "functions": [
    {
      "name": "_read_version",
      "qualname": "_read_version",
      "async": false,
      "lineno": 58,
      "end_lineno": 67,
      "span": 10,
      "decorators": [],
      "signature": "()",
      "returns": "str",
      "doc": "Single source of truth: `.claude-plugin/plugin.json`'s `version`"
    },
    {
      "name": "load_prompt",
      "qualname": "load_prompt",
      "async": false,
      "lineno": 76,
      "end_lineno": 85,
      "span": 10,
      "decorators": [],
      "signature": "(name: str)",
      "returns": "str",
      "doc": "Read prompts/<name>.md and expand any {{include: _foo.md}}"
    },
    {
      "name": "is_protected_path",
      "qualname": "is_protected_path",
      "async": false,
      "lineno": 326,
      "end_lineno": 334,
      "span": 9,
      "decorators": [],
      "signature": "(path: str)",
      "returns": "bool",
      "doc": "Return True if `path` is a meta-directory the implementer must not"
    },
    {
      "name": "resolve_prompt",
      "qualname": "resolve_prompt",
      "async": false,
      "lineno": 710,
      "end_lineno": 725,
      "span": 16,
      "decorators": [],
      "signature": "(call_type: str)",
      "returns": "tuple[str, str, str]",
      "doc": "Return (source_kind, content, location_hint) for a worker call_type."
    },
    {
      "name": "_confidence_schema",
      "qualname": "_confidence_schema",
      "async": false,
      "lineno": 768,
      "end_lineno": 791,
      "span": 24,
      "decorators": [],
      "signature": "(axes: list[str])",
      "returns": "dict",
      "doc": "Build the \u00a78 confidence sub-schema for the given score axes."
    },
    {
      "name": "now",
      "qualname": "now",
      "async": false,
      "lineno": 1425,
      "end_lineno": 1426,
      "span": 2,
      "decorators": [],
      "signature": "()",
      "returns": "str",
      "doc": null
    },
    {
      "name": "log",
      "qualname": "log",
      "async": false,
      "lineno": 1429,
      "end_lineno": 1432,
      "span": 4,
      "decorators": [],
      "signature": "(msg: str)",
      "returns": "None",
      "doc": null
    },
    {
      "name": "die",
      "qualname": "die",
      "async": false,
      "lineno": 1435,
      "end_lineno": 1437,
      "span": 3,
      "decorators": [],
      "signature": "(msg: str, code: int = 1)",
      "returns": null,
      "doc": null
    },
    {
      "name": "detect_session_limit",
      "qualname": "detect_session_limit",
      "async": false,
      "lineno": 1503,
      "end_lineno": 1542,
      "span": 40,
      "decorators": [],
      "signature": "(text: str)",
      "returns": "RateLimitedExit | None",
      "doc": "Return a RateLimitedExit if `text` matches the Claude Code"
    },
    {
      "name": "_install_signal_handlers",
      "qualname": "_install_signal_handlers",
      "async": false,
      "lineno": 1545,
      "end_lineno": 1555,
      "span": 11,
      "decorators": [],
      "signature": "()",
      "returns": "None",
      "doc": "Install SIGTERM/SIGHUP handlers that raise InterruptedBySignal."
    },
    {
      "name": "_enumerate_descendants",
      "qualname": "_enumerate_descendants",
      "async": false,
      "lineno": 1561,
      "end_lineno": 1600,
      "span": 40,
      "decorators": [],
      "signature": "(root_pid: int)",
      "returns": "set[int]",
      "doc": "Return every PID reachable from `root_pid` via PPID links."
    },
    {
      "name": "_signal_pids",
      "qualname": "_signal_pids",
      "async": false,
      "lineno": 1603,
      "end_lineno": 1612,
      "span": 10,
      "decorators": [],
      "signature": "(pids: set[int], sig: int)",
      "returns": "None",
      "doc": "Best-effort signal delivery to a set of PIDs. Drops ProcessLookupError"
    },
    {
      "name": "_reparented_orphans",
      "qualname": "_reparented_orphans",
      "async": false,
      "lineno": 1615,
      "end_lineno": 1650,
      "span": 36,
      "decorators": [],
      "signature": "(seen: set[int])",
      "returns": "list[int]",
      "doc": "Return PIDs from `seen` that are currently alive, reparented to"
    },
    {
      "name": "_terminate_proc_tree",
      "qualname": "_terminate_proc_tree",
      "async": true,
      "lineno": 1796,
      "end_lineno": 1864,
      "span": 69,
      "decorators": [],
      "signature": "(proc: asyncio.subprocess.Process)",
      "returns": "None",
      "doc": "Terminate a subprocess AND every descendant process, then reap."
    },
    {
      "name": "_cleanup_on_abnormal_exit",
      "qualname": "_cleanup_on_abnormal_exit",
      "async": false,
      "lineno": 1867,
      "end_lineno": 1982,
      "span": 116,
      "decorators": [],
      "signature": "(st: 'State', *, full_purge: bool)",
      "returns": "None",
      "doc": "Clean up after an abnormal exit (signal, exception, WorkerError)."
    },
    {
      "name": "_reset_subtask_worktree",
      "qualname": "_reset_subtask_worktree",
      "async": true,
      "lineno": 1985,
      "end_lineno": 2008,
      "span": 24,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, run_id: str)",
      "returns": "None",
      "doc": "Remove the per-subtask worktree directory and branch so a corrective"
    },
    {
      "name": "_parse_claude_version",
      "qualname": "_parse_claude_version",
      "async": false,
      "lineno": 2011,
      "end_lineno": 2016,
      "span": 6,
      "decorators": [],
      "signature": "(version_output: str | None)",
      "returns": "tuple[int, int, int] | None",
      "doc": "Pull MAJOR.MINOR.PATCH out of `claude --version` output."
    },
    {
      "name": "_check_claude_cli_version",
      "qualname": "_check_claude_cli_version",
      "async": false,
      "lineno": 2019,
      "end_lineno": 2049,
      "span": 31,
      "decorators": [],
      "signature": "()",
      "returns": "None",
      "doc": "die() if `claude` is too old for --json-schema. Without this, a"
    },
    {
      "name": "compute_run_branch",
      "qualname": "compute_run_branch",
      "async": false,
      "lineno": 2063,
      "end_lineno": 2074,
      "span": 12,
      "decorators": [],
      "signature": "(run_id: str)",
      "returns": "str",
      "doc": "The git branch name carrying a run's integrated work."
    },
    {
      "name": "compute_subtask_branch",
      "qualname": "compute_subtask_branch",
      "async": false,
      "lineno": 2077,
      "end_lineno": 2086,
      "span": 10,
      "decorators": [],
      "signature": "(run_id: str, sid: str)",
      "returns": "str",
      "doc": "The git branch name for one subtask's worktree."
    },
    {
      "name": "_validate_run_json",
      "qualname": "_validate_run_json",
      "async": false,
      "lineno": 2091,
      "end_lineno": 2187,
      "span": 97,
      "decorators": [],
      "signature": "(data: dict)",
      "returns": "None",
      "doc": "Enforce the logical invariants on a `run.json` sidecar."
    },
    {
      "name": "_format_run_duration",
      "qualname": "_format_run_duration",
      "async": false,
      "lineno": 2192,
      "end_lineno": 2213,
      "span": 22,
      "decorators": [],
      "signature": "(started_at: str | None, finished_at: str | None)",
      "returns": "str | None",
      "doc": "Return a human-readable elapsed duration like '3m 42s' or '1h 12m'."
    },
    {
      "name": "compose_pr_body",
      "qualname": "compose_pr_body",
      "async": false,
      "lineno": 2216,
      "end_lineno": 2287,
      "span": 72,
      "decorators": [],
      "signature": "(state: dict, run_id: str)",
      "returns": "str",
      "doc": "Generate the deterministic fallback PR body from run state +"
    },
    {
      "name": "_write_run_json",
      "qualname": "_write_run_json",
      "async": false,
      "lineno": 2290,
      "end_lineno": 2315,
      "span": 26,
      "decorators": [],
      "signature": "(run_dir: Path, **fields)",
      "returns": "None",
      "doc": "Merge fields into the run.json sidecar at `run_dir/run.json`,"
    },
    {
      "name": "discover_runs",
      "qualname": "discover_runs",
      "async": false,
      "lineno": 2320,
      "end_lineno": 2407,
      "span": 88,
      "decorators": [],
      "signature": "(leerie_root: Path)",
      "returns": "list[dict]",
      "doc": "Enumerate `<state-root>/runs/*/state.json`, returning one summary"
    },
    {
      "name": "resolve_run_id",
      "qualname": "resolve_run_id",
      "async": false,
      "lineno": 2410,
      "end_lineno": 2444,
      "span": 35,
      "decorators": [],
      "signature": "(leerie_root: Path, cli_run_id: str | None)",
      "returns": "str",
      "doc": "Pick the run_id to operate on. Used by `--resume`."
    },
    {
      "name": "_format_run_for_disambiguation",
      "qualname": "_format_run_for_disambiguation",
      "async": false,
      "lineno": 2447,
      "end_lineno": 2492,
      "span": 46,
      "decorators": [],
      "signature": "(run: dict, leerie_root: Path)",
      "returns": "str",
      "doc": "Build the per-row hint string for `resolve_run_id`'s"
    },
    {
      "name": "_format_age",
      "qualname": "_format_age",
      "async": false,
      "lineno": 2495,
      "end_lineno": 2512,
      "span": 18,
      "decorators": [],
      "signature": "(seconds: float)",
      "returns": "str",
      "doc": "Render a duration in seconds as a short human-friendly age:"
    },
    {
      "name": "_derive_run_status",
      "qualname": "_derive_run_status",
      "async": false,
      "lineno": 2544,
      "end_lineno": 2631,
      "span": 88,
      "decorators": [],
      "signature": "(run_json: dict | None, state_json: dict | None)",
      "returns": "str",
      "doc": "Pure function: derive a run's status from run.json + state.json."
    },
    {
      "name": "_collect_run_rows",
      "qualname": "_collect_run_rows",
      "async": false,
      "lineno": 2634,
      "end_lineno": 2659,
      "span": 26,
      "decorators": [],
      "signature": "(leerie_root: Path)",
      "returns": "list[tuple[str, str, str, str, bool]]",
      "doc": "Build (run_id, started_at, status, branch, is_fly) rows for every"
    },
    {
      "name": "_render_run_table",
      "qualname": "_render_run_table",
      "async": false,
      "lineno": 2662,
      "end_lineno": 2672,
      "span": 11,
      "decorators": [],
      "signature": "(rows: list[tuple[str, str, str, str, bool]])",
      "returns": "None",
      "doc": "Print rows as a columnar table with auto-sized columns."
    },
    {
      "name": "list_runs",
      "qualname": "list_runs",
      "async": false,
      "lineno": 2675,
      "end_lineno": 2709,
      "span": 35,
      "decorators": [],
      "signature": "(leerie_root: Path, status_filter: str | None = None, runtime_filter: str | None = None)",
      "returns": "None",
      "doc": "Render a sortable columnar table of runs to stdout. Used by"
    },
    {
      "name": "_read_toml_key",
      "qualname": "_read_toml_key",
      "async": false,
      "lineno": 2713,
      "end_lineno": 2729,
      "span": 17,
      "decorators": [],
      "signature": "(path: Path, key: str)",
      "returns": "str | None",
      "doc": "Read a single `key = value` from a flat leerie.toml. Returns"
    },
    {
      "name": "resolve_task_argument",
      "qualname": "resolve_task_argument",
      "async": false,
      "lineno": 2735,
      "end_lineno": 2763,
      "span": 29,
      "decorators": [],
      "signature": "(raw: str)",
      "returns": "str",
      "doc": "Resolve the positional `task` argument to the task string."
    },
    {
      "name": "resolve_leerie_root",
      "qualname": "resolve_leerie_root",
      "async": false,
      "lineno": 2766,
      "end_lineno": 2781,
      "span": 16,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "Path",
      "doc": "Resolve the leerie state root directory."
    },
    {
      "name": "_resolve_enum_pref",
      "qualname": "_resolve_enum_pref",
      "async": false,
      "lineno": 2784,
      "end_lineno": 2803,
      "span": 20,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None, *, env_var: str, file_key: str, file_name: str, allowed: frozenset[str] | tuple[str, ...], default: str)",
      "returns": "str",
      "doc": "Shared resolution for enum-valued prefs. CLI > env > file > default."
    },
    {
      "name": "resolve_source_of_truth",
      "qualname": "resolve_source_of_truth",
      "async": false,
      "lineno": 2806,
      "end_lineno": 2818,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None = None)",
      "returns": "str",
      "doc": "Resolve the source-of-truth preference. Order:"
    },
    {
      "name": "resolve_runtime",
      "qualname": "resolve_runtime",
      "async": false,
      "lineno": 2821,
      "end_lineno": 2832,
      "span": 12,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None = None)",
      "returns": "str",
      "doc": "Resolve the runtime mode. Order:"
    },
    {
      "name": "_resolve_str_pref",
      "qualname": "_resolve_str_pref",
      "async": false,
      "lineno": 2835,
      "end_lineno": 2849,
      "span": 15,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None, *, env_var: str, file_key: str, file_name: str, default: str | None)",
      "returns": "str | None",
      "doc": "Shared resolution for unvalidated string prefs. CLI > env > file >"
    },
    {
      "name": "resolve_pr_template",
      "qualname": "resolve_pr_template",
      "async": false,
      "lineno": 2852,
      "end_lineno": 2864,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None = None)",
      "returns": "str | None",
      "doc": "Resolve the --pr-template selector. Order:"
    },
    {
      "name": "_resolve_positive_int_pref",
      "qualname": "_resolve_positive_int_pref",
      "async": false,
      "lineno": 2867,
      "end_lineno": 2893,
      "span": 27,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None, *, env_var: str, file_key: str, file_name: str, default: int)",
      "returns": "int",
      "doc": "Shared resolution for positive-int prefs. CLI > env > file > default."
    },
    {
      "name": "resolve_confidence_rounds",
      "qualname": "resolve_confidence_rounds",
      "async": false,
      "lineno": 2896,
      "end_lineno": 2908,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the confidence-rounds cap. Order:"
    },
    {
      "name": "resolve_max_workers",
      "qualname": "resolve_max_workers",
      "async": false,
      "lineno": 2911,
      "end_lineno": 2923,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the max-workers cap. Order:"
    },
    {
      "name": "resolve_max_parallel",
      "qualname": "resolve_max_parallel",
      "async": false,
      "lineno": 2926,
      "end_lineno": 2938,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the max-parallel cap. Order:"
    },
    {
      "name": "resolve_judgment_check_rounds",
      "qualname": "resolve_judgment_check_rounds",
      "async": false,
      "lineno": 2941,
      "end_lineno": 2950,
      "span": 10,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the judgment-check-rounds cap (CRITIC-pattern re-invocations"
    },
    {
      "name": "resolve_planner_check_rounds",
      "qualname": "resolve_planner_check_rounds",
      "async": false,
      "lineno": 2953,
      "end_lineno": 2962,
      "span": 10,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the planner-check-rounds cap (CRITIC-pattern re-invocations"
    },
    {
      "name": "resolve_implementer_confidence_retries",
      "qualname": "resolve_implementer_confidence_retries",
      "async": false,
      "lineno": 2965,
      "end_lineno": 2975,
      "span": 11,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the implementer-confidence-retries cap (separate from"
    },
    {
      "name": "resolve_planner_samples",
      "qualname": "resolve_planner_samples",
      "async": false,
      "lineno": 2978,
      "end_lineno": 2986,
      "span": 9,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the planner-samples cap (multi-sample; 1 disables)."
    },
    {
      "name": "_parse_memory_size",
      "qualname": "_parse_memory_size",
      "async": false,
      "lineno": 2994,
      "end_lineno": 3018,
      "span": 25,
      "decorators": [],
      "signature": "(value: str, context: str)",
      "returns": "int",
      "doc": "Parse a memory size string like \"4G\", \"512M\", \"1024\" into bytes."
    },
    {
      "name": "_auto_worker_memory_max",
      "qualname": "_auto_worker_memory_max",
      "async": false,
      "lineno": 3021,
      "end_lineno": 3046,
      "span": 26,
      "decorators": [],
      "signature": "(max_parallel: int)",
      "returns": "int",
      "doc": "Auto-derive a per-worker memory cap from /proc/meminfo."
    },
    {
      "name": "resolve_worker_memory_max",
      "qualname": "resolve_worker_memory_max",
      "async": false,
      "lineno": 3049,
      "end_lineno": 3069,
      "span": 21,
      "decorators": [],
      "signature": "(repo_root: Path, max_parallel: int, cli_value: str | None = None)",
      "returns": "int",
      "doc": "Resolve the per-worker cgroup memory cap (bytes). Order:"
    },
    {
      "name": "resolve_inspect_dirs",
      "qualname": "resolve_inspect_dirs",
      "async": false,
      "lineno": 3072,
      "end_lineno": 3110,
      "span": 39,
      "decorators": [],
      "signature": "(repo_root: Path, cli_values: list[str] | None = None)",
      "returns": "list[str]",
      "doc": "Resolve the extra inspection directories for classifier/planner/"
    },
    {
      "name": "_parse_bool_envtoml",
      "qualname": "_parse_bool_envtoml",
      "async": false,
      "lineno": 3113,
      "end_lineno": 3125,
      "span": 13,
      "decorators": [],
      "signature": "(value: str)",
      "returns": "bool | None",
      "doc": "Parse a boolean from an env var or TOML scalar. Returns True/False"
    },
    {
      "name": "_resolve_bool_pref",
      "qualname": "_resolve_bool_pref",
      "async": false,
      "lineno": 3128,
      "end_lineno": 3155,
      "span": 28,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool, *, env_var: str, file_key: str, file_name: str)",
      "returns": "bool",
      "doc": "Shared resolution for `store_true` CLI flags that also have an"
    },
    {
      "name": "resolve_no_push",
      "qualname": "resolve_no_push",
      "async": false,
      "lineno": 3158,
      "end_lineno": 3165,
      "span": 8,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --no-push preference. Order:"
    },
    {
      "name": "push_will_happen",
      "qualname": "push_will_happen",
      "async": false,
      "lineno": 3168,
      "end_lineno": 3192,
      "span": 25,
      "decorators": [],
      "signature": "(no_push: bool, host_no_push: bool | None)",
      "returns": "bool",
      "doc": "Whether the host will push after this orchestrator exits."
    },
    {
      "name": "resolve_clarify",
      "qualname": "resolve_clarify",
      "async": false,
      "lineno": 3195,
      "end_lineno": 3202,
      "span": 8,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --clarify preference. Order:"
    },
    {
      "name": "resolve_dangerously_skip_permissions",
      "qualname": "resolve_dangerously_skip_permissions",
      "async": false,
      "lineno": 3205,
      "end_lineno": 3224,
      "span": 20,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --dangerously-skip-permissions preference. Order:"
    },
    {
      "name": "resolve_dangerously_allow_uncapped",
      "qualname": "resolve_dangerously_allow_uncapped",
      "async": false,
      "lineno": 3227,
      "end_lineno": 3242,
      "span": 16,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --dangerously-allow-uncapped preference. Order:"
    },
    {
      "name": "resolve_skip_overlap_judge",
      "qualname": "resolve_skip_overlap_judge",
      "async": false,
      "lineno": 3245,
      "end_lineno": 3262,
      "span": 18,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --skip-overlap-judge preference. Order:"
    },
    {
      "name": "resolve_skip_budget_check",
      "qualname": "resolve_skip_budget_check",
      "async": false,
      "lineno": 3265,
      "end_lineno": 3284,
      "span": 20,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --skip-budget-check preference. Order:"
    },
    {
      "name": "resolve_skip_satisfied_check",
      "qualname": "resolve_skip_satisfied_check",
      "async": false,
      "lineno": 3287,
      "end_lineno": 3306,
      "span": 20,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --skip-satisfied-check preference. Order:"
    },
    {
      "name": "resolve_strict_conformer",
      "qualname": "resolve_strict_conformer",
      "async": false,
      "lineno": 3309,
      "end_lineno": 3323,
      "span": 15,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --strict-conformer preference. Order:"
    },
    {
      "name": "_positive_int",
      "qualname": "_positive_int",
      "async": false,
      "lineno": 3326,
      "end_lineno": 3335,
      "span": 10,
      "decorators": [],
      "signature": "(s: str)",
      "returns": "int",
      "doc": "argparse `type=` helper. Rejects non-positive integers with the"
    },
    {
      "name": "resolve_verbosity",
      "qualname": "resolve_verbosity",
      "async": false,
      "lineno": 3338,
      "end_lineno": 3357,
      "span": 20,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None = None)",
      "returns": "str",
      "doc": "Resolve the verbosity level. Order:"
    },
    {
      "name": "verbosity_from_shortcuts",
      "qualname": "verbosity_from_shortcuts",
      "async": false,
      "lineno": 3360,
      "end_lineno": 3378,
      "span": 19,
      "decorators": [],
      "signature": "(verbose: int, quiet: int)",
      "returns": "str | None",
      "doc": "Map argparse -v/-vv/-q/-qq counts to a verbosity level."
    },
    {
      "name": "resolve_models",
      "qualname": "resolve_models",
      "async": false,
      "lineno": 3381,
      "end_lineno": 3454,
      "span": 74,
      "decorators": [],
      "signature": "(repo_root: Path, args)",
      "returns": "dict[str, str]",
      "doc": "Resolve the model alias for each worker type. Per-worker"
    },
    {
      "name": "resolve_efforts",
      "qualname": "resolve_efforts",
      "async": false,
      "lineno": 3457,
      "end_lineno": 3515,
      "span": 59,
      "decorators": [],
      "signature": "(repo_root: Path, args)",
      "returns": "dict[str, str | None]",
      "doc": "Resolve the --effort value for each worker type. Mirrors"
    },
    {
      "name": "resolve_telemetry_enabled",
      "qualname": "resolve_telemetry_enabled",
      "async": false,
      "lineno": 3518,
      "end_lineno": 3547,
      "span": 30,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool | None = None)",
      "returns": "bool",
      "doc": "Resolve the telemetry enabled/disabled preference. Order:"
    },
    {
      "name": "resolve_telemetry_subdir",
      "qualname": "resolve_telemetry_subdir",
      "async": false,
      "lineno": 3550,
      "end_lineno": 3560,
      "span": 11,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None = None)",
      "returns": "str",
      "doc": "Resolve the telemetry event subdirectory name. Order:"
    },
    {
      "name": "resolve_judge_dir",
      "qualname": "resolve_judge_dir",
      "async": false,
      "lineno": 3563,
      "end_lineno": 3572,
      "span": 10,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None = None)",
      "returns": "str",
      "doc": "Resolve the judge output directory name. Order:"
    },
    {
      "name": "resolve_heal_dir",
      "qualname": "resolve_heal_dir",
      "async": false,
      "lineno": 3575,
      "end_lineno": 3584,
      "span": 10,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: str | None = None)",
      "returns": "str",
      "doc": "Resolve the heal output directory name. Order:"
    },
    {
      "name": "resolve_heal_max_rounds",
      "qualname": "resolve_heal_max_rounds",
      "async": false,
      "lineno": 3587,
      "end_lineno": 3596,
      "span": 10,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the heal-loop max-iterations cap. Order:"
    },
    {
      "name": "resolve_heal_success_threshold",
      "qualname": "resolve_heal_success_threshold",
      "async": false,
      "lineno": 3599,
      "end_lineno": 3626,
      "span": 28,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: float | None = None)",
      "returns": "float",
      "doc": "Resolve the heal-loop success pass-rate threshold. Order:"
    },
    {
      "name": "run_proc",
      "qualname": "run_proc",
      "async": true,
      "lineno": 3629,
      "end_lineno": 3673,
      "span": 45,
      "decorators": [],
      "signature": "(cmd: list[str], *, cwd: str | None = None, timeout: float | None = None)",
      "returns": "subprocess.CompletedProcess",
      "doc": "Async equivalent of `subprocess.run(cmd, capture_output=True, text=True)`."
    },
    {
      "name": "run_streaming",
      "qualname": "run_streaming",
      "async": true,
      "lineno": 3676,
      "end_lineno": 3799,
      "span": 124,
      "decorators": [],
      "signature": "(cmd: list[str], *, cwd: str | None = None, env: dict[str, str] | None = None, timeout: float | None = None, log_path: Path | None = None, label: str | None = None, verbosity: str = 'stream', line_prefix: str = '  | ', tail_lines: int = 40)",
      "returns": "tuple[int, str]",
      "doc": "Run a subprocess with stdout+stderr streamed live, persisted to a"
    },
    {
      "name": "gather_or_cancel",
      "qualname": "gather_or_cancel",
      "async": true,
      "lineno": 3802,
      "end_lineno": 3823,
      "span": 22,
      "decorators": [],
      "signature": "(*aws)",
      "returns": null,
      "doc": "Like asyncio.gather, but on the first exception cancel every other"
    },
    {
      "name": "run_script",
      "qualname": "run_script",
      "async": true,
      "lineno": 3826,
      "end_lineno": 3828,
      "span": 3,
      "decorators": [],
      "signature": "(name: str, *args: str)",
      "returns": "subprocess.CompletedProcess",
      "doc": "Run one of the bundled git worktree scripts in the target repo."
    },
    {
      "name": "preflight",
      "qualname": "preflight",
      "async": true,
      "lineno": 3841,
      "end_lineno": 3907,
      "span": 67,
      "decorators": [],
      "signature": "(leerie_dir: Path, verbosity: str = VERBOSITY_DEFAULT, skip_smoke: bool = False, no_push: bool = False)",
      "returns": "None",
      "doc": "Hard checks before any LLM work. Fails fast rather than wasting workers."
    },
    {
      "name": "_confidence_issues",
      "qualname": "_confidence_issues",
      "async": false,
      "lineno": 3922,
      "end_lineno": 3940,
      "span": 19,
      "decorators": [],
      "signature": "(conf: dict, axes: list[str], threshold: float = 9.0)",
      "returns": "list[str]",
      "doc": "Return one LOW_CONFIDENCE issue per axis below *threshold*."
    },
    {
      "name": "check_classifier_output",
      "qualname": "check_classifier_output",
      "async": false,
      "lineno": 3943,
      "end_lineno": 3989,
      "span": 47,
      "decorators": [],
      "signature": "(result: dict, repo_root: Path)",
      "returns": "list[str]",
      "doc": "Thin mechanical checks on the classifier's category selection."
    },
    {
      "name": "_grep_old_pattern",
      "qualname": "_grep_old_pattern",
      "async": false,
      "lineno": 4007,
      "end_lineno": 4029,
      "span": 23,
      "decorators": [],
      "signature": "(pattern: str, repo_root: Path)",
      "returns": "set[str]",
      "doc": "Grep *repo_root* for *pattern*, return set of relative file paths."
    },
    {
      "name": "_check_migration_surface",
      "qualname": "_check_migration_surface",
      "async": false,
      "lineno": 4032,
      "end_lineno": 4060,
      "span": 29,
      "decorators": [],
      "signature": "(subtasks: list[dict], repo_root: Path)",
      "returns": "list[str]",
      "doc": "UNCOVERED_MIGRATION_SURFACE check (DESIGN \u00a75)."
    },
    {
      "name": "check_planner_output",
      "qualname": "check_planner_output",
      "async": false,
      "lineno": 4063,
      "end_lineno": 4165,
      "span": 103,
      "decorators": [],
      "signature": "(result: dict, repo_root: Path, domain: str)",
      "returns": "list[str]",
      "doc": "Rich mechanical checks on a single planner domain's output."
    },
    {
      "name": "check_reconciler_output",
      "qualname": "check_reconciler_output",
      "async": false,
      "lineno": 4168,
      "end_lineno": 4199,
      "span": 32,
      "decorators": [],
      "signature": "(output: dict, plans: list[dict])",
      "returns": "list[str]",
      "doc": "Mechanical checks on the reconciler's output beyond the existing"
    },
    {
      "name": "check_overlap_judge_output",
      "qualname": "check_overlap_judge_output",
      "async": false,
      "lineno": 4202,
      "end_lineno": 4263,
      "span": 62,
      "decorators": [],
      "signature": "(output: dict, plans: list[dict], repo_root: Path)",
      "returns": "list[str]",
      "doc": "Mechanical checks on the overlap judge's collision list."
    },
    {
      "name": "check_provision_output",
      "qualname": "check_provision_output",
      "async": false,
      "lineno": 4266,
      "end_lineno": 4305,
      "span": 40,
      "decorators": [],
      "signature": "(result: dict, repo_root: Path)",
      "returns": "list[str]",
      "doc": "Mechanical checks on the provision LLM fallback's recipe."
    },
    {
      "name": "check_integrator_output",
      "qualname": "check_integrator_output",
      "async": false,
      "lineno": 4308,
      "end_lineno": 4311,
      "span": 4,
      "decorators": [],
      "signature": "(result: dict)",
      "returns": "list[str]",
      "doc": "Confidence gate for the integrator."
    },
    {
      "name": "check_implementer_output",
      "qualname": "check_implementer_output",
      "async": false,
      "lineno": 4314,
      "end_lineno": 4336,
      "span": 23,
      "decorators": [],
      "signature": "(result: dict, subtask: dict, actual_files: set[str])",
      "returns": "list[str]",
      "doc": "Mechanical checks on an implementer's complete result."
    },
    {
      "name": "_expand_braces",
      "qualname": "_expand_braces",
      "async": false,
      "lineno": 4351,
      "end_lineno": 4364,
      "span": 14,
      "decorators": [],
      "signature": "(pattern: str)",
      "returns": "list[str]",
      "doc": "Expand shell-style ``{a,b}`` brace groups into multiple patterns."
    },
    {
      "name": "glob_task_references",
      "qualname": "glob_task_references",
      "async": false,
      "lineno": 4367,
      "end_lineno": 4396,
      "span": 30,
      "decorators": [],
      "signature": "(task: str, repo_root: Path)",
      "returns": "list[Path]",
      "doc": "Find file references in the task string via glob expansion."
    },
    {
      "name": "extract_task_file_structure",
      "qualname": "extract_task_file_structure",
      "async": false,
      "lineno": 4399,
      "end_lineno": 4433,
      "span": 35,
      "decorators": [],
      "signature": "(task: str, repo_root: Path)",
      "returns": "list[str] | None",
      "doc": "Extract structural elements from files referenced in the task."
    },
    {
      "name": "check_task_file_coverage",
      "qualname": "check_task_file_coverage",
      "async": false,
      "lineno": 4439,
      "end_lineno": 4470,
      "span": 32,
      "decorators": [],
      "signature": "(extracted: list[str], subtasks: list[dict])",
      "returns": "list[str]",
      "doc": "Check which extracted items are NOT referenced by any subtask."
    },
    {
      "name": "_format_task_file_structure",
      "qualname": "_format_task_file_structure",
      "async": false,
      "lineno": 4473,
      "end_lineno": 4484,
      "span": 12,
      "decorators": [],
      "signature": "(items: list[str])",
      "returns": "str",
      "doc": "Format extracted structure as an external coverage reference"
    },
    {
      "name": "validate_plan",
      "qualname": "validate_plan",
      "async": false,
      "lineno": 4487,
      "end_lineno": 4593,
      "span": 107,
      "decorators": [],
      "signature": "(subtasks: dict)",
      "returns": "None",
      "doc": "Structural validation of the merged plan \u2014 pure Python set operations."
    },
    {
      "name": "warn_cross_planner_file_overlap",
      "qualname": "warn_cross_planner_file_overlap",
      "async": false,
      "lineno": 4596,
      "end_lineno": 4627,
      "span": 32,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "None",
      "doc": "Log a warning when subtasks from different planner outputs both list"
    },
    {
      "name": "warn_layer_gaps",
      "qualname": "warn_layer_gaps",
      "async": false,
      "lineno": 4634,
      "end_lineno": 4672,
      "span": 39,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "None",
      "doc": "Advisory cross-domain layer-gap warnings (DESIGN \u00a75)."
    },
    {
      "name": "_resolves_under",
      "qualname": "_resolves_under",
      "async": false,
      "lineno": 4675,
      "end_lineno": 4687,
      "span": 13,
      "decorators": [],
      "signature": "(path_str: str, root: Path)",
      "returns": "bool",
      "doc": "True iff `path_str` (relative or absolute) resolves under `root`."
    },
    {
      "name": "filter_offtree_subtasks",
      "qualname": "filter_offtree_subtasks",
      "async": false,
      "lineno": 4690,
      "end_lineno": 4747,
      "span": 58,
      "decorators": [],
      "signature": "(plans: list[dict], repo_root: Path, inspect_dirs: list[str], st: 'State')",
      "returns": "None",
      "doc": "Mutate `plans` in place: drop any subtask whose `files_likely_touched`"
    },
    {
      "name": "filter_satisfied_subtasks",
      "qualname": "filter_satisfied_subtasks",
      "async": true,
      "lineno": 4750,
      "end_lineno": 4898,
      "span": 149,
      "decorators": [],
      "signature": "(plans: list[dict], repo_root: Path, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "dict[str, str] | None",
      "doc": "Mutate `plans` in place: drop any subtask whose success criteria"
    },
    {
      "name": "_lockfile_table_entries",
      "qualname": "_lockfile_table_entries",
      "async": false,
      "lineno": 4924,
      "end_lineno": 5037,
      "span": 114,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "list[dict]",
      "doc": "The deterministic lockfile \u2192 install-command table. Returns a list of"
    },
    {
      "name": "detect_recipe_from_lockfiles",
      "qualname": "detect_recipe_from_lockfiles",
      "async": false,
      "lineno": 5040,
      "end_lineno": 5045,
      "span": 6,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "list[dict]",
      "doc": "Public entry point for the deterministic detection layer. Returns"
    },
    {
      "name": "validate_provision_recipe",
      "qualname": "validate_provision_recipe",
      "async": false,
      "lineno": 5048,
      "end_lineno": 5109,
      "span": 62,
      "decorators": [],
      "signature": "(recipe: list[dict])",
      "returns": "None",
      "doc": "Mechanically bound the provision recipe. Raises ValueError on any"
    },
    {
      "name": "_parse_apt_intents",
      "qualname": "_parse_apt_intents",
      "async": false,
      "lineno": 5181,
      "end_lineno": 5213,
      "span": 33,
      "decorators": [],
      "signature": "(command: str)",
      "returns": "list[str]",
      "doc": "Extract apt package names from a shell command string."
    },
    {
      "name": "_merge_setup_packages",
      "qualname": "_merge_setup_packages",
      "async": false,
      "lineno": 5216,
      "end_lineno": 5233,
      "span": 18,
      "decorators": [],
      "signature": "(existing: str, captured: list[str])",
      "returns": "str | None",
      "doc": "Union existing setup_packages with newly-captured packages."
    },
    {
      "name": "_write_config_toml_keys",
      "qualname": "_write_config_toml_keys",
      "async": false,
      "lineno": 5236,
      "end_lineno": 5275,
      "span": 40,
      "decorators": [],
      "signature": "(cfg_path: Path, updates: dict[str, str])",
      "returns": "None",
      "doc": "Minimal deterministic TOML upsert for a set of key-value pairs."
    },
    {
      "name": "_capture_installs_from_logs",
      "qualname": "_capture_installs_from_logs",
      "async": false,
      "lineno": 5278,
      "end_lineno": 5298,
      "span": 21,
      "decorators": [],
      "signature": "(log_dir: Path)",
      "returns": "tuple[list[str], list[str]]",
      "doc": "Scan worker JSONL logs in log_dir and return (apt_packages, lang_cmds)."
    },
    {
      "name": "resolve_capture_deps",
      "qualname": "resolve_capture_deps",
      "async": false,
      "lineno": 5301,
      "end_lineno": 5324,
      "span": 24,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "bool",
      "doc": "Resolve the capture_deps preference (DESIGN \u00a76\u00bd)."
    },
    {
      "name": "capture_repo_deps",
      "qualname": "capture_repo_deps",
      "async": false,
      "lineno": 5327,
      "end_lineno": 5367,
      "span": 41,
      "decorators": [],
      "signature": "(repo_root: Path, st: object)",
      "returns": "None",
      "doc": "Scan this run's logs and write setup_packages to .leerie/config.toml."
    },
    {
      "name": "_split_readme_headers",
      "qualname": "_split_readme_headers",
      "async": false,
      "lineno": 5370,
      "end_lineno": 5419,
      "span": 50,
      "decorators": [],
      "signature": "(text: str)",
      "returns": "list[tuple[int, str, str]]",
      "doc": "Return [(line_index, header_text, body_until_next_header), ...] for"
    },
    {
      "name": "_is_install_section",
      "qualname": "_is_install_section",
      "async": false,
      "lineno": 5422,
      "end_lineno": 5429,
      "span": 8,
      "decorators": [],
      "signature": "(header: str)",
      "returns": "bool",
      "doc": "True if a header (after decoration-strip) matches the section"
    },
    {
      "name": "_slice_code_fences_with_install_hints",
      "qualname": "_slice_code_fences_with_install_hints",
      "async": false,
      "lineno": 5432,
      "end_lineno": 5471,
      "span": 40,
      "decorators": [],
      "signature": "(text: str, ctx_lines: int = 10)",
      "returns": "str",
      "doc": "Fallback layer: scan for fenced code blocks containing recognized"
    },
    {
      "name": "extract_readme_sections",
      "qualname": "extract_readme_sections",
      "async": false,
      "lineno": 5483,
      "end_lineno": 5540,
      "span": 58,
      "decorators": [],
      "signature": "(text: str)",
      "returns": "str",
      "doc": "Extract the install/setup-relevant slice of a README."
    },
    {
      "name": "_read_file_safely",
      "qualname": "_read_file_safely",
      "async": false,
      "lineno": 5543,
      "end_lineno": 5550,
      "span": 8,
      "decorators": [],
      "signature": "(path: Path, budget: int)",
      "returns": "str",
      "doc": "Read a file with a byte ceiling, swallowing missing-file and"
    },
    {
      "name": "_sample_workspace_manifests",
      "qualname": "_sample_workspace_manifests",
      "async": false,
      "lineno": 5563,
      "end_lineno": 5600,
      "span": 38,
      "decorators": [],
      "signature": "(repo_root: Path, pkg_json_text: str, per_file_budget: int, max_files: int)",
      "returns": "list[tuple[str, str]]",
      "doc": "For a monorepo whose root package.json declares `workspaces`,"
    },
    {
      "name": "gather_provision_fixtures",
      "qualname": "gather_provision_fixtures",
      "async": false,
      "lineno": 5603,
      "end_lineno": 5711,
      "span": 109,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "dict",
      "doc": "Assemble the LLM-fallback worker's input set. Returns a dict with"
    },
    {
      "name": "_split_checkpoint_sections",
      "qualname": "_split_checkpoint_sections",
      "async": false,
      "lineno": 5744,
      "end_lineno": 5760,
      "span": 17,
      "decorators": [],
      "signature": "(content: str)",
      "returns": "dict[str, list[str]]",
      "doc": "Split a checkpoint file by `## ` headers into {header: lines}."
    },
    {
      "name": "validate_checkpoint",
      "qualname": "validate_checkpoint",
      "async": false,
      "lineno": 5763,
      "end_lineno": 5812,
      "span": 50,
      "decorators": [],
      "signature": "(path: str, worktree_root: Path | None = None)",
      "returns": "str | None",
      "doc": "Return an error description if the checkpoint is structurally incomplete,"
    },
    {
      "name": "_normalize_for_noise",
      "qualname": "_normalize_for_noise",
      "async": false,
      "lineno": 5815,
      "end_lineno": 5827,
      "span": 13,
      "decorators": [],
      "signature": "(line: str)",
      "returns": "str",
      "doc": "Reduce a checkpoint line to its comparison key for `_NOISE_TOKENS`."
    },
    {
      "name": "_strip_bullet",
      "qualname": "_strip_bullet",
      "async": false,
      "lineno": 5830,
      "end_lineno": 5844,
      "span": 15,
      "decorators": [],
      "signature": "(line: str)",
      "returns": "str",
      "doc": "Strip leading markdown bullet markers (`-`, `*`, `1.`) before noise"
    },
    {
      "name": "_parse_touched_file_line",
      "qualname": "_parse_touched_file_line",
      "async": false,
      "lineno": 5847,
      "end_lineno": 5867,
      "span": 21,
      "decorators": [],
      "signature": "(line: str)",
      "returns": "tuple[str | None, bool]",
      "doc": "Extract a file path from a `## Files touched` line and detect the"
    },
    {
      "name": "validate_result",
      "qualname": "validate_result",
      "async": false,
      "lineno": 5872,
      "end_lineno": 5940,
      "span": 69,
      "decorators": [],
      "signature": "(result: dict)",
      "returns": "tuple[str, str] | None",
      "doc": "Cross-field invariant checks that JSON Schema cannot express."
    },
    {
      "name": "check_diff_scope",
      "qualname": "check_diff_scope",
      "async": true,
      "lineno": 5945,
      "end_lineno": 5988,
      "span": 44,
      "decorators": [],
      "signature": "(sid: str, worktree: str, subtask: dict, st: State)",
      "returns": "str | None",
      "doc": "Check the implementer's diff for violations."
    },
    {
      "name": "check_merge_committed",
      "qualname": "check_merge_committed",
      "async": true,
      "lineno": 5993,
      "end_lineno": 6016,
      "span": 24,
      "decorators": [],
      "signature": "(staging: Path)",
      "returns": "str | None",
      "doc": "Return an error if the staging worktree is still mid-merge."
    },
    {
      "name": "check_integrator_commit",
      "qualname": "check_integrator_commit",
      "async": true,
      "lineno": 6019,
      "end_lineno": 6032,
      "span": 14,
      "decorators": [],
      "signature": "(staging: Path)",
      "returns": "str | None",
      "doc": "Return an error if the integrator's merge commit touched .leerie/ files."
    },
    {
      "name": "check_branch_has_commits",
      "qualname": "check_branch_has_commits",
      "async": true,
      "lineno": 6037,
      "end_lineno": 6062,
      "span": 26,
      "decorators": [],
      "signature": "(sid: str, worktree: str, parent_branch: str)",
      "returns": "tuple[str, str] | None",
      "doc": "Return `(failure_kind, message)` if the implementer's subtask"
    },
    {
      "name": "scan_conflict_markers",
      "qualname": "scan_conflict_markers",
      "async": true,
      "lineno": 6067,
      "end_lineno": 6085,
      "span": 19,
      "decorators": [],
      "signature": "(staging: Path)",
      "returns": "str | None",
      "doc": "Return error if unresolved conflict markers remain in the staging tree."
    },
    {
      "name": "validate_resume_state",
      "qualname": "validate_resume_state",
      "async": false,
      "lineno": 6090,
      "end_lineno": 6115,
      "span": 26,
      "decorators": [],
      "signature": "(data: dict)",
      "returns": "None",
      "doc": "Assert the structure of a loaded state.json before resuming. A corrupt"
    },
    {
      "name": "_is_auth_or_quota_failure",
      "qualname": "_is_auth_or_quota_failure",
      "async": false,
      "lineno": 6125,
      "end_lineno": 6156,
      "span": 32,
      "decorators": [],
      "signature": "(envelope: dict)",
      "returns": "bool",
      "doc": "True if the `claude -p` envelope looks like a 401/429/529/"
    },
    {
      "name": "_extract_tool_result_text",
      "qualname": "_extract_tool_result_text",
      "async": false,
      "lineno": 6159,
      "end_lineno": 6172,
      "span": 14,
      "decorators": [],
      "signature": "(block: dict)",
      "returns": "str",
      "doc": "Tool-result `content` is either a string or a list of content"
    },
    {
      "name": "_is_fork_exhaustion",
      "qualname": "_is_fork_exhaustion",
      "async": false,
      "lineno": 6190,
      "end_lineno": 6194,
      "span": 5,
      "decorators": [],
      "signature": "(text: str)",
      "returns": "bool",
      "doc": "True if `text` carries a shell fork-failure signature. Advisory \u2014"
    },
    {
      "name": "_tool_result_outcome",
      "qualname": "_tool_result_outcome",
      "async": false,
      "lineno": 6197,
      "end_lineno": 6220,
      "span": 24,
      "decorators": [],
      "signature": "(event: dict)",
      "returns": "bool | None",
      "doc": "Classify a stream event for `_read_stream`'s PID-exhaustion window:"
    },
    {
      "name": "_tag_each_line",
      "qualname": "_tag_each_line",
      "async": false,
      "lineno": 6223,
      "end_lineno": 6258,
      "span": 36,
      "decorators": [],
      "signature": "(prefix: str, content: str)",
      "returns": "str",
      "doc": "Prefix the first non-empty line of `content` with `prefix`;"
    },
    {
      "name": "_summarize_tool_use",
      "qualname": "_summarize_tool_use",
      "async": false,
      "lineno": 6261,
      "end_lineno": 6300,
      "span": 40,
      "decorators": [],
      "signature": "(sid: str, block: dict, verbosity: str)",
      "returns": "str",
      "doc": "Map one `tool_use` content block to a one-line inline summary."
    },
    {
      "name": "_summarize_stream_event",
      "qualname": "_summarize_stream_event",
      "async": false,
      "lineno": 6303,
      "end_lineno": 6467,
      "span": 165,
      "decorators": [],
      "signature": "(sid: str, event: dict, verbosity: str)",
      "returns": "str | None",
      "doc": "Return the one-line inline-log summary for one stream event, or"
    },
    {
      "name": "_format_progress_prefix",
      "qualname": "_format_progress_prefix",
      "async": false,
      "lineno": 6470,
      "end_lineno": 6493,
      "span": 24,
      "decorators": [],
      "signature": "(prog: tuple[int, int, int, int, int] | None)",
      "returns": "str",
      "doc": "Render the activity prefix from a `_get_progress` tuple, or \"\" when"
    },
    {
      "name": "_get_progress",
      "qualname": "_get_progress",
      "async": false,
      "lineno": 6496,
      "end_lineno": 6539,
      "span": 44,
      "decorators": [],
      "signature": "(st: 'State')",
      "returns": "tuple[int, int, int, int, int] | None",
      "doc": "Return (running, in_conformer, done, wave_idx, wave_total) for the"
    },
    {
      "name": "_cgroup_request",
      "qualname": "_cgroup_request",
      "async": false,
      "lineno": 6575,
      "end_lineno": 6583,
      "span": 9,
      "decorators": [],
      "signature": "(payload: str, timeout: float = 5.0)",
      "returns": "str",
      "doc": "Send one request to the root broker and return its response line"
    },
    {
      "name": "_cgroup_probe",
      "qualname": "_cgroup_probe",
      "async": false,
      "lineno": 6586,
      "end_lineno": 6615,
      "span": 30,
      "decorators": [],
      "signature": "()",
      "returns": "bool",
      "doc": "Once-per-run probe, memoized in `_CGROUP_PROBE_RESULT`: does the"
    },
    {
      "name": "_cgroup_create",
      "qualname": "_cgroup_create",
      "async": false,
      "lineno": 6618,
      "end_lineno": 6637,
      "span": 20,
      "decorators": [],
      "signature": "(sid: str, memory_max_bytes: int, pids_max: int)",
      "returns": "str | None",
      "doc": "Ask the broker to create a worker cgroup and set its caps. Returns"
    },
    {
      "name": "_cgroup_enroll",
      "qualname": "_cgroup_enroll",
      "async": false,
      "lineno": 6640,
      "end_lineno": 6653,
      "span": 14,
      "decorators": [],
      "signature": "(sid: str, pid: int)",
      "returns": "bool",
      "doc": "Ask the broker to migrate `pid` into the worker cgroup. Called"
    },
    {
      "name": "_cgroup_destroy",
      "qualname": "_cgroup_destroy",
      "async": false,
      "lineno": 6656,
      "end_lineno": 6663,
      "span": 8,
      "decorators": [],
      "signature": "(sid: str | None)",
      "returns": "None",
      "doc": "Ask the broker to tear down the worker cgroup (kill any survivors,"
    },
    {
      "name": "_cgroup_stat",
      "qualname": "_cgroup_stat",
      "async": false,
      "lineno": 6666,
      "end_lineno": 6690,
      "span": 25,
      "decorators": [],
      "signature": "(sid: str | None)",
      "returns": "tuple[int, int, int] | None",
      "doc": "Read-only probe of a worker cgroup's PID counters via the broker's"
    },
    {
      "name": "enforce_and_record_cgroup_containment",
      "qualname": "enforce_and_record_cgroup_containment",
      "async": false,
      "lineno": 6693,
      "end_lineno": 6742,
      "span": 50,
      "decorators": [],
      "signature": "(st: 'State', allow_uncapped: bool)",
      "returns": "None",
      "doc": "Fail-closed containment gate + state recording, run once per run"
    },
    {
      "name": "_invoke",
      "qualname": "_invoke",
      "async": true,
      "lineno": 6745,
      "end_lineno": 7198,
      "span": 454,
      "decorators": [],
      "signature": "(cmd: list[str], cwd: str, timeout: int, sid: str, leerie_dir: Path, verbosity: str, progress: Callable[[], tuple[int, int, int, int] | None] | None = None, idle_warn_sec: float | None = None, worker_memory_max_bytes: int | None = None, worker_pids_max: int | None = None)",
      "returns": "dict",
      "doc": "Run a `claude -p` command, streaming events as they arrive."
    },
    {
      "name": "_capture_call",
      "qualname": "_capture_call",
      "async": false,
      "lineno": 7201,
      "end_lineno": 7211,
      "span": 11,
      "decorators": [],
      "signature": "(run_dir: Path, record: dict)",
      "returns": "None",
      "doc": "Append one NDJSON record to calls.ndjson with fsync-per-line durability."
    },
    {
      "name": "_collect_memory_sample",
      "qualname": "_collect_memory_sample",
      "async": false,
      "lineno": 7214,
      "end_lineno": 7263,
      "span": 50,
      "decorators": [],
      "signature": "(st: 'State')",
      "returns": "dict",
      "doc": "Snapshot orchestrator RSS / current phase / worker count / open FDs /"
    },
    {
      "name": "_memory_sampler",
      "qualname": "_memory_sampler",
      "async": true,
      "lineno": 7266,
      "end_lineno": 7308,
      "span": 43,
      "decorators": [],
      "signature": "(st: 'State', interval_sec: float = 30.0)",
      "returns": "None",
      "doc": "Periodic orchestrator-memory sample for leak detection."
    },
    {
      "name": "claude_p",
      "qualname": "claude_p",
      "async": true,
      "lineno": 7311,
      "end_lineno": 7569,
      "span": 259,
      "decorators": [],
      "signature": "(user_prompt: str, system_prompt: str, *, schema_key: str, cwd: str, allowed_tools: str, max_turns: int, autonomous: bool, caps: dict, st: 'State', model: str, sid: str, add_dirs: list[str] | None = None, effort: str | None = None, _suppress_capture: bool = False)",
      "returns": "dict",
      "doc": "Run one headless Claude Code worker and return its validated"
    },
    {
      "name": "replay_capture",
      "qualname": "replay_capture",
      "async": true,
      "lineno": 7572,
      "end_lineno": 7636,
      "span": 65,
      "decorators": [],
      "signature": "(record: dict, *, override_system_prompt: str | None = None, cwd: str | None = None)",
      "returns": "tuple[dict, dict]",
      "doc": "Replay one captured call from a calls.ndjson record."
    },
    {
      "name": "_accumulate_telemetry",
      "qualname": "_accumulate_telemetry",
      "async": false,
      "lineno": 7639,
      "end_lineno": 7649,
      "span": 11,
      "decorators": [],
      "signature": "(data: dict, envelope: dict)",
      "returns": "None",
      "doc": "Accumulate run-weight signals from a worker envelope into `data`."
    },
    {
      "name": "judge_capture",
      "qualname": "judge_capture",
      "async": true,
      "lineno": 7837,
      "end_lineno": 7885,
      "span": 49,
      "decorators": [],
      "signature": "(record: dict, models: dict[str, str], efforts: dict[str, str | None], caps: dict, st: 'State')",
      "returns": "dict",
      "doc": "Run a judge worker against one captured call record."
    },
    {
      "name": "phase_judge",
      "qualname": "phase_judge",
      "async": true,
      "lineno": 7888,
      "end_lineno": 7956,
      "span": 69,
      "decorators": [],
      "signature": "(run_dir: Path, judge_out_dir: Path, caps: dict, st: 'State', models: dict[str, str], efforts: dict[str, str | None], judge_call_types: list[str] | None = None)",
      "returns": "dict",
      "doc": "Judge all captured call records in run_dir/calls.ndjson."
    },
    {
      "name": "heal_baseline",
      "qualname": "heal_baseline",
      "async": true,
      "lineno": 8011,
      "end_lineno": 8090,
      "span": 80,
      "decorators": [],
      "signature": "(call_type: str, failing_records: list[dict], n: int, heal_dir: Path, caps: dict, st: 'State', models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "HealState",
      "doc": "Run n unpatched replays per failing capture to establish a noise-floor."
    },
    {
      "name": "heal_apply_patch",
      "qualname": "heal_apply_patch",
      "async": false,
      "lineno": 8093,
      "end_lineno": 8116,
      "span": 24,
      "decorators": [],
      "signature": "(call_type: str, iter_n: int, patch_text: str, anchor_match: str, heal_dir: Path, failing_records: list[dict])",
      "returns": "list[Path]",
      "doc": "Materialise per-sample patched prompts under iter-<N>/patched-prompts/."
    },
    {
      "name": "heal_replay_patched",
      "qualname": "heal_replay_patched",
      "async": true,
      "lineno": 8119,
      "end_lineno": 8218,
      "span": 100,
      "decorators": [],
      "signature": "(call_type: str, iter_n: int, n: int, heal_dir: Path, caps: dict, st: 'State', models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "HealState",
      "doc": "Run n patched replays per failing capture and append an iteration record."
    },
    {
      "name": "check_convergence",
      "qualname": "check_convergence",
      "async": false,
      "lineno": 8221,
      "end_lineno": 8281,
      "span": 61,
      "decorators": [],
      "signature": "(state: HealState, config: dict)",
      "returns": "str",
      "doc": "Evaluate whether the heal loop has converged."
    },
    {
      "name": "write_heal_report",
      "qualname": "write_heal_report",
      "async": false,
      "lineno": 8284,
      "end_lineno": 8336,
      "span": 53,
      "decorators": [],
      "signature": "(call_type: str, state: HealState, best_patch_text: str = '')",
      "returns": "Path",
      "doc": "Render a markdown heal report to <heal_dir>/<call_type>/healing-<call_type>.md."
    },
    {
      "name": "request_patch",
      "qualname": "request_patch",
      "async": true,
      "lineno": 8339,
      "end_lineno": 8426,
      "span": 88,
      "decorators": [],
      "signature": "(state: HealState, iter_n: int, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "tuple[str, str]",
      "doc": "Invoke the patch-generator worker to propose a minimal prompt edit."
    },
    {
      "name": "phase_heal",
      "qualname": "phase_heal",
      "async": true,
      "lineno": 8429,
      "end_lineno": 8514,
      "span": 86,
      "decorators": [],
      "signature": "(call_type: str, failing_records: list[dict], heal_dir: Path, caps: dict, st: 'State', models: dict[str, str], efforts: dict[str, str | None], request_patch_fn = None, n: int = HEAL_N_REPLAYS_DEFAULT, config: dict | None = None)",
      "returns": "str",
      "doc": "Drive the full heal loop for one call_type."
    },
    {
      "name": "run_setup_hook",
      "qualname": "run_setup_hook",
      "async": true,
      "lineno": 8530,
      "end_lineno": 8599,
      "span": 70,
      "decorators": [],
      "signature": "(repo_root: Path, log_dir: Path, st: 'State')",
      "returns": "None",
      "doc": "Execute `<repo>/.leerie-setup.sh` if present. Idempotent via"
    },
    {
      "name": "_existing_mise_toml_path",
      "qualname": "_existing_mise_toml_path",
      "async": false,
      "lineno": 8607,
      "end_lineno": 8619,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "Path | None",
      "doc": "Return the path to whichever of `mise.toml` or `.mise.toml`"
    },
    {
      "name": "_go_already_pinned",
      "qualname": "_go_already_pinned",
      "async": false,
      "lineno": 8622,
      "end_lineno": 8654,
      "span": 33,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "bool",
      "doc": "Return True if the repo already specifies a Go version mise would"
    },
    {
      "name": "_existing_mise_toml_tool_keys",
      "qualname": "_existing_mise_toml_tool_keys",
      "async": false,
      "lineno": 8691,
      "end_lineno": 8713,
      "span": 23,
      "decorators": [],
      "signature": "(text: str | None)",
      "returns": "set[str]",
      "doc": "Return the set of tool keys pinned by a `[tools]` section in the"
    },
    {
      "name": "_read_idiomatic_pins",
      "qualname": "_read_idiomatic_pins",
      "async": false,
      "lineno": 8716,
      "end_lineno": 8776,
      "span": 61,
      "decorators": [],
      "signature": "(repo_root: Path, already_pinned: set[str])",
      "returns": "list[tuple[str, str]]",
      "doc": "Return [(tool, version), ...] for every idiomatic version file in"
    },
    {
      "name": "synth_mise_go_override",
      "qualname": "synth_mise_go_override",
      "async": false,
      "lineno": 8779,
      "end_lineno": 8905,
      "span": 127,
      "decorators": [],
      "signature": "(repo_root: Path, run_dir: Path)",
      "returns": "Path | None",
      "doc": "If `go.mod` exists and no other Go pin is in place, write a mise"
    },
    {
      "name": "_repo_has_version_signal",
      "qualname": "_repo_has_version_signal",
      "async": false,
      "lineno": 8923,
      "end_lineno": 8934,
      "span": 12,
      "decorators": [],
      "signature": "(repo_root: Path, override_file: Path | None)",
      "returns": "bool",
      "doc": "Return True if the repo declares any runtime version pin mise"
    },
    {
      "name": "run_mise_install",
      "qualname": "run_mise_install",
      "async": true,
      "lineno": 8937,
      "end_lineno": 9024,
      "span": 88,
      "decorators": [],
      "signature": "(repo_root: Path, log_dir: Path, st: 'State', override_file: Path | None = None)",
      "returns": "None",
      "doc": "Invoke `mise install` at the repo root. If `override_file` is"
    },
    {
      "name": "phase_classify",
      "qualname": "phase_classify",
      "async": true,
      "lineno": 9030,
      "end_lineno": 9083,
      "span": 54,
      "decorators": [],
      "signature": "(task: str, st: State, caps: dict, clarify: bool, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "dict",
      "doc": "Phase 1 (classify), which also produces the Clarify sub-step's"
    },
    {
      "name": "gather_answers",
      "qualname": "gather_answers",
      "async": false,
      "lineno": 9086,
      "end_lineno": 9131,
      "span": 46,
      "decorators": [],
      "signature": "(st: State, supplied: dict | None)",
      "returns": "dict",
      "doc": "Collect clarification answers \u2014 from --answers, from the resolved"
    },
    {
      "name": "absorb_supplied_answers",
      "qualname": "absorb_supplied_answers",
      "async": false,
      "lineno": 9134,
      "end_lineno": 9196,
      "span": 63,
      "decorators": [],
      "signature": "(args, st: State, leerie_dir: Path)",
      "returns": "None",
      "doc": "Merge --answers FILE into st.data['answers'] and propagate the"
    },
    {
      "name": "surface_clarification",
      "qualname": "surface_clarification",
      "async": false,
      "lineno": 9199,
      "end_lineno": 9244,
      "span": 46,
      "decorators": [],
      "signature": "(sid: str, question: dict, checkpoint_path: str, st: State)",
      "returns": "bool",
      "doc": "Surface a mid-execution clarification question to the user"
    },
    {
      "name": "_format_provision_user_prompt",
      "qualname": "_format_provision_user_prompt",
      "async": false,
      "lineno": 9247,
      "end_lineno": 9275,
      "span": 29,
      "decorators": [],
      "signature": "(fixtures: dict, task: str)",
      "returns": "str",
      "doc": "Compose the LLM-fallback user prompt from the assembled fixture"
    },
    {
      "name": "phase_provision",
      "qualname": "phase_provision",
      "async": true,
      "lineno": 9278,
      "end_lineno": 9429,
      "span": 152,
      "decorators": [],
      "signature": "(repo_root: Path, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "Phase 1\u00bd: per-repo dependency *detection*."
    },
    {
      "name": "_select_best_planner_sample",
      "qualname": "_select_best_planner_sample",
      "async": false,
      "lineno": 9432,
      "end_lineno": 9454,
      "span": 23,
      "decorators": [],
      "signature": "(samples: list[dict], repo_root: Path, domain: str)",
      "returns": "dict",
      "doc": "Mechanically select the best planner sample for a domain."
    },
    {
      "name": "phase_plan",
      "qualname": "phase_plan",
      "async": true,
      "lineno": 9457,
      "end_lineno": 9597,
      "span": 141,
      "decorators": [],
      "signature": "(task: str, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "list[dict]",
      "doc": "Phase 2: one planner per category, run in parallel (bounded by"
    },
    {
      "name": "_promote_external_collisions",
      "qualname": "_promote_external_collisions",
      "async": false,
      "lineno": 9600,
      "end_lineno": 9624,
      "span": 25,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "int",
      "doc": "In-place: for every `requires` entry with `extent: external` whose"
    },
    {
      "name": "_collect_external_preconditions",
      "qualname": "_collect_external_preconditions",
      "async": false,
      "lineno": 9627,
      "end_lineno": 9657,
      "span": 31,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "list[dict]",
      "doc": "Walk plans and return the deduped list of planner-declared"
    },
    {
      "name": "_compute_unresolved_requires",
      "qualname": "_compute_unresolved_requires",
      "async": false,
      "lineno": 9660,
      "end_lineno": 9699,
      "span": 40,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "list[dict]",
      "doc": "Pure-Python lookup: every (sid, tag, domain) where a subtask"
    },
    {
      "name": "_prune_dead_subtasks",
      "qualname": "_prune_dead_subtasks",
      "async": false,
      "lineno": 9702,
      "end_lineno": 9757,
      "span": 56,
      "decorators": [],
      "signature": "(plans: list[dict], unresolvable_entries: list[dict])",
      "returns": "list[str]",
      "doc": "Dead-subtask elimination: remove subtasks whose EVERY in_plan"
    },
    {
      "name": "_find_oversized_added_subtasks",
      "qualname": "_find_oversized_added_subtasks",
      "async": false,
      "lineno": 9760,
      "end_lineno": 9779,
      "span": 20,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "list[dict]",
      "doc": "Pure-Python lookup: every reconciler-added subtask (carrying"
    },
    {
      "name": "_apply_reconciler_output",
      "qualname": "_apply_reconciler_output",
      "async": false,
      "lineno": 9782,
      "end_lineno": 10175,
      "span": 394,
      "decorators": [],
      "signature": "(plans: list[dict], output: dict, attempt_1_renames: list[dict] | None = None)",
      "returns": "list[dict]",
      "doc": "Mutate `plans` per the reconciler's output. On success, returns"
    },
    {
      "name": "phase_reconcile",
      "qualname": "phase_reconcile",
      "async": true,
      "lineno": 10178,
      "end_lineno": 10773,
      "span": 596,
      "decorators": [],
      "signature": "(plans: list[dict], task: str, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "list[dict]",
      "doc": "Phase 2\u00bd: reconcile cross-domain capability-tag drift between"
    },
    {
      "name": "_tarjan_sccs",
      "qualname": "_tarjan_sccs",
      "async": false,
      "lineno": 10776,
      "end_lineno": 10844,
      "span": 69,
      "decorators": [],
      "signature": "(nodes: set[str], succ: dict[str, set[str]])",
      "returns": "list[list[str]]",
      "doc": "Tarjan's strongly-connected-components algorithm."
    },
    {
      "name": "_attribute_cycle_edges",
      "qualname": "_attribute_cycle_edges",
      "async": false,
      "lineno": 10847,
      "end_lineno": 10910,
      "span": 64,
      "decorators": [],
      "signature": "(scc: list[str], succ: dict[str, set[str]], edge_sources: dict[tuple[str, str], str], output: dict, subtasks: dict[str, dict])",
      "returns": "list[dict]",
      "doc": "For each edge inside an SCC, attribute it back to the reconciler"
    },
    {
      "name": "_format_cycle_diagnostic",
      "qualname": "_format_cycle_diagnostic",
      "async": false,
      "lineno": 10913,
      "end_lineno": 10953,
      "span": 41,
      "decorators": [],
      "signature": "(sccs: list[list[str]], succ: dict[str, set[str]], edge_sources: dict[tuple[str, str], str], output: dict, subtasks: dict[str, dict])",
      "returns": "str",
      "doc": "Render an SCC list + edge attributions into the multi-line"
    },
    {
      "name": "_shared_files_in_scc",
      "qualname": "_shared_files_in_scc",
      "async": false,
      "lineno": 10956,
      "end_lineno": 10971,
      "span": 16,
      "decorators": [],
      "signature": "(scc: list[str], subtasks: dict[str, dict])",
      "returns": "list[str]",
      "doc": "Files that appear in `files_likely_touched` of \u2265 2 SCC members."
    },
    {
      "name": "_original_tag_for_rename_edge",
      "qualname": "_original_tag_for_rename_edge",
      "async": false,
      "lineno": 10974,
      "end_lineno": 11004,
      "span": 31,
      "decorators": [],
      "signature": "(edge: dict, output: dict)",
      "returns": "str",
      "doc": "Given a cycle-edge dict (from `_attribute_cycle_edges`) and the"
    },
    {
      "name": "_recommend_cycle_resolution",
      "qualname": "_recommend_cycle_resolution",
      "async": false,
      "lineno": 11007,
      "end_lineno": 11202,
      "span": 196,
      "decorators": [],
      "signature": "(scc: list[str], succ: dict[str, set[str]], edge_sources: dict[tuple[str, str], str], subtasks: dict[str, dict], output: dict, pre_providers: dict[str, list[str]])",
      "returns": "dict",
      "doc": "Deterministic recommendation for breaking one SCC."
    },
    {
      "name": "_format_recommendation",
      "qualname": "_format_recommendation",
      "async": false,
      "lineno": 11205,
      "end_lineno": 11225,
      "span": 21,
      "decorators": [],
      "signature": "(rec: dict)",
      "returns": "str",
      "doc": "Render a recommendation dict (from `_recommend_cycle_resolution`)"
    },
    {
      "name": "_format_must_include",
      "qualname": "_format_must_include",
      "async": false,
      "lineno": 11228,
      "end_lineno": 11265,
      "span": 38,
      "decorators": [],
      "signature": "(scc: list[str], edges: list[dict], output: dict)",
      "returns": "list[str]",
      "doc": "For one SCC, list the bounded set of legal cycle-breaking"
    },
    {
      "name": "_build_cycle_retry_prompt",
      "qualname": "_build_cycle_retry_prompt",
      "async": false,
      "lineno": 11268,
      "end_lineno": 11356,
      "span": 89,
      "decorators": [],
      "signature": "(sccs: list[list[str]], succ: dict[str, set[str]], edge_sources: dict[tuple[str, str], str], output: dict, subtasks: dict[str, dict], recommendations: list[dict], original_user_prompt: str)",
      "returns": "str",
      "doc": "Build the retry prompt sent to the reconciler when the"
    },
    {
      "name": "_build_size_retry_prompt",
      "qualname": "_build_size_retry_prompt",
      "async": false,
      "lineno": 11359,
      "end_lineno": 11427,
      "span": 69,
      "decorators": [],
      "signature": "(oversized: list[dict], original_user_prompt: str)",
      "returns": "str",
      "doc": "Build the retry prompt sent to the reconciler when the size gate"
    },
    {
      "name": "_matches_recommendation",
      "qualname": "_matches_recommendation",
      "async": false,
      "lineno": 11430,
      "end_lineno": 11444,
      "span": 15,
      "decorators": [],
      "signature": "(option_str: str, rec: dict)",
      "returns": "bool",
      "doc": "Whether a must-include option string matches the recommendation"
    },
    {
      "name": "_validate_must_include",
      "qualname": "_validate_must_include",
      "async": false,
      "lineno": 11447,
      "end_lineno": 11489,
      "span": 43,
      "decorators": [],
      "signature": "(output: dict, sccs: list[list[str]])",
      "returns": "list[str]",
      "doc": "For each SCC, check that the reconciler's revised output includes"
    },
    {
      "name": "_tag_jaccard",
      "qualname": "_tag_jaccard",
      "async": false,
      "lineno": 11492,
      "end_lineno": 11509,
      "span": 18,
      "decorators": [],
      "signature": "(a: str, b: str)",
      "returns": "float",
      "doc": "Token-set Jaccard similarity over hyphen-split tokens."
    },
    {
      "name": "_recommend_unresolved_resolution",
      "qualname": "_recommend_unresolved_resolution",
      "async": false,
      "lineno": 11512,
      "end_lineno": 11609,
      "span": 98,
      "decorators": [],
      "signature": "(consumer_sid: str, unresolved_tag: str, providers: dict[str, list[str]], output: dict)",
      "returns": "dict | None",
      "doc": "Deterministic recommendation for resolving one unresolved"
    },
    {
      "name": "_build_unresolved_retry_prompt",
      "qualname": "_build_unresolved_retry_prompt",
      "async": false,
      "lineno": 11612,
      "end_lineno": 11765,
      "span": 154,
      "decorators": [],
      "signature": "(unresolved: list[dict], providers: dict[str, list[str]], recommendations: dict[tuple[str, str], dict | None], output: dict, original_user_prompt: str)",
      "returns": "str",
      "doc": "Build the retry prompt sent to the reconciler when the"
    },
    {
      "name": "_validate_unresolved_must_include",
      "qualname": "_validate_unresolved_must_include",
      "async": false,
      "lineno": 11768,
      "end_lineno": 11900,
      "span": 133,
      "decorators": [],
      "signature": "(output: dict, unresolved: list[dict], attempt_1_output: dict | None)",
      "returns": "list[str]",
      "doc": "For each unresolved (sid, tag), check the reconciler's revised"
    },
    {
      "name": "_compute_overlap_anchors",
      "qualname": "_compute_overlap_anchors",
      "async": false,
      "lineno": 11930,
      "end_lineno": 11952,
      "span": 23,
      "decorators": [],
      "signature": "(collisions: list[dict])",
      "returns": "set[str]",
      "doc": "An *anchor* is a sid that appears in two or more non-"
    },
    {
      "name": "_validate_overlap_judge_output",
      "qualname": "_validate_overlap_judge_output",
      "async": false,
      "lineno": 11955,
      "end_lineno": 12052,
      "span": 98,
      "decorators": [],
      "signature": "(output: dict, subtasks_by_id: dict[str, dict])",
      "returns": "None",
      "doc": "Apply the merge-feasibility backstop and structural sanity checks"
    },
    {
      "name": "_apply_overlap_drop",
      "qualname": "_apply_overlap_drop",
      "async": false,
      "lineno": 12055,
      "end_lineno": 12161,
      "span": 107,
      "decorators": [],
      "signature": "(plans: list[dict], dropped_sid: str, surviving_sid: str)",
      "returns": "None",
      "doc": "Remove `dropped_sid` from its plan, union its `provides` tags"
    },
    {
      "name": "_apply_overlap_merge",
      "qualname": "_apply_overlap_merge",
      "async": false,
      "lineno": 12164,
      "end_lineno": 12356,
      "span": 193,
      "decorators": [],
      "signature": "(plans: list[dict], a_sid: str, b_sid: str, artifact: str, merge_feasibility: str, survivor_hint: str | None = None)",
      "returns": "str",
      "doc": "Collapse `a_sid` and `b_sid` into one subtask. Returns the"
    },
    {
      "name": "_would_cycle_after",
      "qualname": "_would_cycle_after",
      "async": false,
      "lineno": 12359,
      "end_lineno": 12389,
      "span": 31,
      "decorators": [],
      "signature": "(plans: list[dict], apply_fn: Callable[[list[dict]], None])",
      "returns": "bool",
      "doc": "Return True iff applying `apply_fn` to `plans` would leave the"
    },
    {
      "name": "_apply_overlap_collisions",
      "qualname": "_apply_overlap_collisions",
      "async": false,
      "lineno": 12392,
      "end_lineno": 12610,
      "span": 219,
      "decorators": [],
      "signature": "(plans: list[dict], collisions: list[dict])",
      "returns": "list[dict]",
      "doc": "Apply a validated list of overlap-judge collisions to `plans`"
    },
    {
      "name": "phase_overlap_judge",
      "qualname": "phase_overlap_judge",
      "async": true,
      "lineno": 12613,
      "end_lineno": 12831,
      "span": 219,
      "decorators": [],
      "signature": "(plans: list[dict], task: str, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "list[dict]",
      "doc": "Phase 2\u00be: detect cross-planner surface-overlap collisions"
    },
    {
      "name": "_build_predecessor_graph",
      "qualname": "_build_predecessor_graph",
      "async": false,
      "lineno": 12834,
      "end_lineno": 12885,
      "span": 52,
      "decorators": [],
      "signature": "(subtasks: dict[str, dict])",
      "returns": "tuple[dict[str, set[str]], dict[str, list[str]], dict[tuple[str, str], str]]",
      "doc": "Build the predecessor graph from a merged subtasks dict."
    },
    {
      "name": "detect_no_work",
      "qualname": "detect_no_work",
      "async": false,
      "lineno": 12888,
      "end_lineno": 12916,
      "span": 29,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "dict[str, str] | None",
      "doc": "Return a `{domain: basis}` map iff *every* plan satisfies"
    },
    {
      "name": "_finish_no_work_run",
      "qualname": "_finish_no_work_run",
      "async": false,
      "lineno": 12919,
      "end_lineno": 12961,
      "span": 43,
      "decorators": [],
      "signature": "(st: State, no_work_map: dict[str, str])",
      "returns": "None",
      "doc": "Terminal-state handler for the cleared-but-empty case"
    },
    {
      "name": "schedule",
      "qualname": "schedule",
      "async": false,
      "lineno": 12964,
      "end_lineno": 13013,
      "span": 50,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "tuple[dict, list[list[str]]]",
      "doc": "Phase 3 (pure Python): merge plans, resolve intra- and cross-domain"
    },
    {
      "name": "check_budget_feasibility",
      "qualname": "check_budget_feasibility",
      "async": false,
      "lineno": 13016,
      "end_lineno": 13077,
      "span": 62,
      "decorators": [],
      "signature": "(st: State, caps: dict, subtasks: dict, waves: list[list[str]])",
      "returns": "None",
      "doc": "Phase 3 pure-Python gate (DESIGN \u00a713 *Budget feasibility \u2014 fail"
    },
    {
      "name": "_write_subtask_artifacts",
      "qualname": "_write_subtask_artifacts",
      "async": false,
      "lineno": 13080,
      "end_lineno": 13101,
      "span": 22,
      "decorators": [],
      "signature": "(leerie_dir: Path, sid: str, artifacts: list)",
      "returns": "None",
      "doc": "Persist a subtask's `artifacts` result field to"
    },
    {
      "name": "_read_upstream_artifacts",
      "qualname": "_read_upstream_artifacts",
      "async": false,
      "lineno": 13104,
      "end_lineno": 13124,
      "span": 21,
      "decorators": [],
      "signature": "(leerie_dir: Path, predecessor_ids: list[str])",
      "returns": "list[dict]",
      "doc": "Read the artifacts files for `predecessor_ids` and return them"
    },
    {
      "name": "_format_upstream_artifacts_for_sid",
      "qualname": "_format_upstream_artifacts_for_sid",
      "async": false,
      "lineno": 13127,
      "end_lineno": 13156,
      "span": 30,
      "decorators": [],
      "signature": "(leerie_dir: Path, sid: str)",
      "returns": "str | None",
      "doc": "End-to-end helper: load the persisted plan, recompute the"
    },
    {
      "name": "_format_upstream_artifacts_section",
      "qualname": "_format_upstream_artifacts_section",
      "async": false,
      "lineno": 13159,
      "end_lineno": 13186,
      "span": 28,
      "decorators": [],
      "signature": "(payloads: list[dict])",
      "returns": "str | None",
      "doc": "Render upstream artifact payloads as a prompt section, or None"
    },
    {
      "name": "write_plan",
      "qualname": "write_plan",
      "async": false,
      "lineno": 13189,
      "end_lineno": 13214,
      "span": 26,
      "decorators": [],
      "signature": "(leerie_dir: Path, task: str, st: State, subtasks: dict, waves: list[list[str]])",
      "returns": "None",
      "doc": "Persist the merged plan and per-subtask spec files the implementers read."
    },
    {
      "name": "_format_provision_recipe_section",
      "qualname": "_format_provision_recipe_section",
      "async": false,
      "lineno": 13217,
      "end_lineno": 13270,
      "span": 54,
      "decorators": [],
      "signature": "(recipe: list[dict], *, audience: str)",
      "returns": "str | None",
      "doc": "Render the persisted provision recipe as a prompt section, or"
    },
    {
      "name": "run_implementer",
      "qualname": "run_implementer",
      "async": true,
      "lineno": 13273,
      "end_lineno": 13388,
      "span": 116,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None], continuation: bool = False, note: str = '')",
      "returns": "dict",
      "doc": "Spawn one implementer for one subtask in its own worktree. Handles"
    },
    {
      "name": "_retryable_failure",
      "qualname": "_retryable_failure",
      "async": false,
      "lineno": 13418,
      "end_lineno": 13432,
      "span": 15,
      "decorators": [],
      "signature": "(kind: str)",
      "returns": "bool",
      "doc": "The retry policy, in one place. Dispatches on a structured"
    },
    {
      "name": "discover_rules_files",
      "qualname": "discover_rules_files",
      "async": false,
      "lineno": 13465,
      "end_lineno": 13477,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "list[Path]",
      "doc": "Return existing rule-file paths from `_RULES_FILE_CANDIDATES`, in"
    },
    {
      "name": "_format_rules_paths",
      "qualname": "_format_rules_paths",
      "async": false,
      "lineno": 13480,
      "end_lineno": 13489,
      "span": 10,
      "decorators": [],
      "signature": "(rules_files: list[Path], repo_root: Path)",
      "returns": "str",
      "doc": "Render discovered rule/convention paths relative to `repo_root` for"
    },
    {
      "name": "_format_convention_docs_section",
      "qualname": "_format_convention_docs_section",
      "async": false,
      "lineno": 13492,
      "end_lineno": 13504,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "str | None",
      "doc": "Build the implementer's `CONVENTION_DOCS:` prompt block from the same"
    },
    {
      "name": "_is_rails_repo",
      "qualname": "_is_rails_repo",
      "async": false,
      "lineno": 13507,
      "end_lineno": 13512,
      "span": 6,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "bool",
      "doc": "True when the repo is a Rails application. Requires both a"
    },
    {
      "name": "_infer_build_lint_test",
      "qualname": "_infer_build_lint_test",
      "async": false,
      "lineno": 13515,
      "end_lineno": 13594,
      "span": 80,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "dict[str, str]",
      "doc": "Best-effort guess at the repo's build / lint / test commands. Returns"
    },
    {
      "name": "_load_blt_config",
      "qualname": "_load_blt_config",
      "async": false,
      "lineno": 13597,
      "end_lineno": 13615,
      "span": 19,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "dict[str, str] | None",
      "doc": "Read BLT-related keys from .leerie/config.toml."
    },
    {
      "name": "resolve_blt",
      "qualname": "resolve_blt",
      "async": false,
      "lineno": 13618,
      "end_lineno": 13647,
      "span": 30,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "dict[str, str]",
      "doc": "Return the effective build/lint/test commands for the repo."
    },
    {
      "name": "validate_conformance_result",
      "qualname": "validate_conformance_result",
      "async": false,
      "lineno": 13650,
      "end_lineno": 13703,
      "span": 54,
      "decorators": [],
      "signature": "(result: dict, worktree: str)",
      "returns": "str | None",
      "doc": "Cross-field invariants for the conformer's structured output."
    },
    {
      "name": "_branch_head_sha",
      "qualname": "_branch_head_sha",
      "async": true,
      "lineno": 13706,
      "end_lineno": 13712,
      "span": 7,
      "decorators": [],
      "signature": "(worktree: str)",
      "returns": "str",
      "doc": "HEAD sha in the worktree, or empty string on failure. Used as the"
    },
    {
      "name": "_protected_paths_since",
      "qualname": "_protected_paths_since",
      "async": true,
      "lineno": 13715,
      "end_lineno": 13736,
      "span": 22,
      "decorators": [],
      "signature": "(worktree: str, before_sha: str)",
      "returns": "list[str]",
      "doc": "Return the list of protected paths the diff `before_sha..HEAD`"
    },
    {
      "name": "rollback_conformer_commits",
      "qualname": "rollback_conformer_commits",
      "async": true,
      "lineno": 13739,
      "end_lineno": 13750,
      "span": 12,
      "decorators": [],
      "signature": "(worktree: str, before_sha: str)",
      "returns": "None",
      "doc": "Hard-reset the subtask branch back to `before_sha`. Used when the"
    },
    {
      "name": "_uncommitted_paths",
      "qualname": "_uncommitted_paths",
      "async": true,
      "lineno": 13753,
      "end_lineno": 13767,
      "span": 15,
      "decorators": [],
      "signature": "(worktree: str)",
      "returns": "list[str]",
      "doc": "Return tracked-file paths with uncommitted changes in the worktree,"
    },
    {
      "name": "_unprefixed_conformer_commits",
      "qualname": "_unprefixed_conformer_commits",
      "async": true,
      "lineno": 13770,
      "end_lineno": 13792,
      "span": 23,
      "decorators": [],
      "signature": "(worktree: str, before_sha: str, prefix: str = 'conformer:')",
      "returns": "list[str]",
      "doc": "Return subject lines of commits between before_sha..HEAD whose"
    },
    {
      "name": "run_conformer",
      "qualname": "run_conformer",
      "async": true,
      "lineno": 13795,
      "end_lineno": 13857,
      "span": 63,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, worktree: str, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None], rules_files: list[Path], blt_commands: dict[str, str], diff_base: str, extra_feedback: str | None = None)",
      "returns": "dict | None",
      "doc": "Spawn one conformer for one subtask in its existing worktree."
    },
    {
      "name": "_summarize_residuals",
      "qualname": "_summarize_residuals",
      "async": false,
      "lineno": 13860,
      "end_lineno": 13873,
      "span": 14,
      "decorators": [],
      "signature": "(conf_res: dict)",
      "returns": "list[str]",
      "doc": "One advisory string per residual / failing build-lint-test axis."
    },
    {
      "name": "_confidence_axes_clear",
      "qualname": "_confidence_axes_clear",
      "async": false,
      "lineno": 13876,
      "end_lineno": 13887,
      "span": 12,
      "decorators": [],
      "signature": "(conf: dict, axes: list[str], threshold: float = 9.0)",
      "returns": "bool",
      "doc": "True when every named axis in *conf* is a number >= *threshold*."
    },
    {
      "name": "_format_check_feedback",
      "qualname": "_format_check_feedback",
      "async": false,
      "lineno": 13890,
      "end_lineno": 13910,
      "span": 21,
      "decorators": [],
      "signature": "(issues: list[str], rnd: int, max_rounds: int)",
      "returns": "str",
      "doc": "Format a structured feedback block for a re-invocation."
    },
    {
      "name": "_run_checked_loop",
      "qualname": "_run_checked_loop",
      "async": true,
      "lineno": 13913,
      "end_lineno": 13966,
      "span": 54,
      "decorators": [],
      "signature": "(*, invoke: Callable[..., Awaitable[dict]], check: Callable[[dict], list[str]], name: str, max_rounds: int, make_feedback_prompt: Callable[[str], Awaitable[dict]] | None = None)",
      "returns": "tuple[dict | None, list[str]]",
      "doc": "Generic mechanical-feedback retry loop (CRITIC pattern)."
    },
    {
      "name": "_conformance_clean",
      "qualname": "_conformance_clean",
      "async": false,
      "lineno": 13969,
      "end_lineno": 13979,
      "span": 11,
      "decorators": [],
      "signature": "(conf_res: dict)",
      "returns": "bool",
      "doc": "True when the conformer reports no residuals and every axis is"
    },
    {
      "name": "_iter_log_tool_use",
      "qualname": "_iter_log_tool_use",
      "async": false,
      "lineno": 14016,
      "end_lineno": 14070,
      "span": 55,
      "decorators": [],
      "signature": "(log_path: Path)",
      "returns": "Iterator[tuple[str, dict, str]]",
      "doc": "Yield each Bash/BashOutput/KillBash/Read `tool_use` block from a"
    },
    {
      "name": "_count_bash_axis_invocations",
      "qualname": "_count_bash_axis_invocations",
      "async": false,
      "lineno": 14073,
      "end_lineno": 14084,
      "span": 12,
      "decorators": [],
      "signature": "(log_path: Path, axis_re: re.Pattern[str])",
      "returns": "int",
      "doc": "Count distinct Bash `tool_use` invocations in `log_path` whose"
    },
    {
      "name": "_count_orphaned_bg_axis",
      "qualname": "_count_orphaned_bg_axis",
      "async": false,
      "lineno": 14087,
      "end_lineno": 14148,
      "span": 62,
      "decorators": [],
      "signature": "(log_path: Path, axis_re: re.Pattern[str])",
      "returns": "list[str]",
      "doc": "Return the bash_ids of BLT commands matching `axis_re` that"
    },
    {
      "name": "_emit_bash_axis_warnings",
      "qualname": "_emit_bash_axis_warnings",
      "async": false,
      "lineno": 14151,
      "end_lineno": 14176,
      "span": 26,
      "decorators": [],
      "signature": "(log_path: Path, round_label: str, warnings: list[str])",
      "returns": "None",
      "doc": "Helper called once per conformer round: append advisory warnings"
    },
    {
      "name": "_run_conformance_phase",
      "qualname": "_run_conformance_phase",
      "async": true,
      "lineno": 14179,
      "end_lineno": 14288,
      "span": 110,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, worktree: str, subtask: dict, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "tuple[dict | None, list[str], str | None]",
      "doc": "Drive the orchestrator-level conformer loop for one subtask."
    },
    {
      "name": "run_final_conformance",
      "qualname": "run_final_conformance",
      "async": true,
      "lineno": 14291,
      "end_lineno": 14479,
      "span": 189,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "Whole-tree conformance pass on the integrated staging worktree"
    },
    {
      "name": "settle_subtask",
      "qualname": "settle_subtask",
      "async": true,
      "lineno": 14482,
      "end_lineno": 14777,
      "span": 296,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "dict",
      "doc": "Drive one subtask to a terminal state."
    },
    {
      "name": "integrate_wave",
      "qualname": "integrate_wave",
      "async": true,
      "lineno": 14780,
      "end_lineno": 14893,
      "span": 114,
      "decorators": [],
      "signature": "(wave: list[str], results: dict[str, dict], leerie_dir: Path, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "list[str]",
      "doc": "Merge each completed subtask branch into staging (git merge, not"
    },
    {
      "name": "phase_execute",
      "qualname": "phase_execute",
      "async": true,
      "lineno": 14896,
      "end_lineno": 14989,
      "span": 94,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "Phases 4-5: create staging, then run waves sequentially; within a wave,"
    },
    {
      "name": "find_pr_template",
      "qualname": "find_pr_template",
      "async": false,
      "lineno": 15026,
      "end_lineno": 15071,
      "span": 46,
      "decorators": [],
      "signature": "(repo_root: Path, override: str | None = None)",
      "returns": "tuple[Path, str] | None",
      "doc": "Locate the PR template the worker should fill out, or None when"
    },
    {
      "name": "_cap_text",
      "qualname": "_cap_text",
      "async": false,
      "lineno": 15091,
      "end_lineno": 15110,
      "span": 20,
      "decorators": [],
      "signature": "(s: str, max_bytes: int, label: str)",
      "returns": "tuple[str, bool]",
      "doc": "Return (capped_text, was_truncated). Cap `s` at `max_bytes` of"
    },
    {
      "name": "_strip_leerie_prefix",
      "qualname": "_strip_leerie_prefix",
      "async": false,
      "lineno": 15119,
      "end_lineno": 15129,
      "span": 11,
      "decorators": [],
      "signature": "(title: str)",
      "returns": "str",
      "doc": "Strip a leading `leerie:` from a worker-emitted PR title so the"
    },
    {
      "name": "_truncate_diff_sample",
      "qualname": "_truncate_diff_sample",
      "async": false,
      "lineno": 15132,
      "end_lineno": 15146,
      "span": 15,
      "decorators": [],
      "signature": "(diff_text: str, max_lines: int)",
      "returns": "tuple[str, bool]",
      "doc": "Return (truncated_text, was_truncated). Splits on newlines and"
    },
    {
      "name": "_final_conformance_payload",
      "qualname": "_final_conformance_payload",
      "async": false,
      "lineno": 15149,
      "end_lineno": 15205,
      "span": 57,
      "decorators": [],
      "signature": "(st: 'State')",
      "returns": "dict | None",
      "doc": "Compact view of the final-tree conformer pass for the pr_writer"
    },
    {
      "name": "_compose_pr_via_llm",
      "qualname": "_compose_pr_via_llm",
      "async": true,
      "lineno": 15208,
      "end_lineno": 15391,
      "span": 184,
      "decorators": [],
      "signature": "(st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None], repo_root: Path, pr_template_override: str | None)",
      "returns": "None",
      "doc": "Run the pr_writer worker and persist its title/body to run.json."
    },
    {
      "name": "phase_finalize",
      "qualname": "phase_finalize",
      "async": true,
      "lineno": 15394,
      "end_lineno": 15518,
      "span": 125,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, no_push: bool, no_verify: bool, caps: dict | None = None, models: dict[str, str] | None = None, efforts: dict[str, str | None] | None = None, pr_template_override: str | None = None, host_no_push: bool | None = None)",
      "returns": "None",
      "doc": "Phase 6: verify the run branch and record finalize state."
    },
    {
      "name": "orchestrate",
      "qualname": "orchestrate",
      "async": true,
      "lineno": 15524,
      "end_lineno": 15542,
      "span": 19,
      "decorators": [],
      "signature": "(args, caps: dict, leerie_dir: Path, st: State, sot_pref: str, verbosity: str, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "The async portion of a run: every phase that spawns a `claude -p`"
    },
    {
      "name": "_run_phases",
      "qualname": "_run_phases",
      "async": true,
      "lineno": 15545,
      "end_lineno": 15791,
      "span": 247,
      "decorators": [],
      "signature": "(args, caps: dict, leerie_dir: Path, st: State, sot_pref: str, verbosity: str, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "The phase sequence of one run. Split out from `orchestrate()`"
    },
    {
      "name": "main",
      "qualname": "main",
      "async": false,
      "lineno": 15794,
      "end_lineno": 16618,
      "span": 825,
      "decorators": [],
      "signature": "()",
      "returns": "None",
      "doc": null
    }
  ],
  "classes": [
    {
      "name": "InterruptedBySignal",
      "bases": [
        "BaseException"
      ],
      "lineno": 1440,
      "end_lineno": 1452,
      "span": 13,
      "doc": "Raised by signal handlers (SIGTERM, SIGHUP) installed in main().",
      "methods": []
    },
    {
      "name": "RateLimitedExit",
      "bases": [
        "BaseException"
      ],
      "lineno": 1455,
      "end_lineno": 1476,
      "span": 22,
      "doc": "Raised when claude -p reports the Claude Code subscription",
      "methods": [
        {
          "name": "__init__",
          "qualname": "RateLimitedExit.__init__",
          "async": false,
          "lineno": 1473,
          "end_lineno": 1476,
          "span": 4,
          "decorators": [],
          "signature": "(self, reset_at: datetime | None, raw_message: str)",
          "returns": null,
          "doc": null
        }
      ]
    },
    {
      "name": "_DescendantTracker",
      "bases": [],
      "lineno": 1681,
      "end_lineno": 1793,
      "span": 113,
      "doc": "Background poller that accumulates every PID ever observed as a",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_DescendantTracker.__init__",
          "async": false,
          "lineno": 1710,
          "end_lineno": 1716,
          "span": 7,
          "decorators": [],
          "signature": "(self, leader_pid: int, cgroup_sid: str | None = None)",
          "returns": null,
          "doc": null
        },
        {
          "name": "start",
          "qualname": "_DescendantTracker.start",
          "async": false,
          "lineno": 1718,
          "end_lineno": 1721,
          "span": 4,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": "Spawn the polling task on the current event loop."
        },
        {
          "name": "_poll_loop",
          "qualname": "_DescendantTracker._poll_loop",
          "async": true,
          "lineno": 1723,
          "end_lineno": 1770,
          "span": 48,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": null
        },
        {
          "name": "stop_and_reap",
          "qualname": "_DescendantTracker.stop_and_reap",
          "async": true,
          "lineno": 1772,
          "end_lineno": 1793,
          "span": 22,
          "decorators": [],
          "signature": "(self)",
          "returns": "int",
          "doc": "Stop polling, SIGKILL every accumulated PID, return the"
        }
      ]
    },
    {
      "name": "WorkerError",
      "bases": [
        "RuntimeError"
      ],
      "lineno": 6121,
      "end_lineno": 6122,
      "span": 2,
      "doc": null,
      "methods": []
    },
    {
      "name": "_ReplayState",
      "bases": [],
      "lineno": 7652,
      "end_lineno": 7680,
      "span": 29,
      "doc": "Minimal State-alike for replay_capture: no persistent writes.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_ReplayState.__init__",
          "async": false,
          "lineno": 7661,
          "end_lineno": 7670,
          "span": 10,
          "decorators": [],
          "signature": "(self, run_dir: Path, state_path: Path)",
          "returns": "None",
          "doc": null
        },
        {
          "name": "save",
          "qualname": "_ReplayState.save",
          "async": false,
          "lineno": 7672,
          "end_lineno": 7673,
          "span": 2,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": null
        },
        {
          "name": "bump_workers",
          "qualname": "_ReplayState.bump_workers",
          "async": false,
          "lineno": 7675,
          "end_lineno": 7676,
          "span": 2,
          "decorators": [],
          "signature": "(self, caps: dict)",
          "returns": "None",
          "doc": null
        },
        {
          "name": "add_telemetry",
          "qualname": "_ReplayState.add_telemetry",
          "async": false,
          "lineno": 7678,
          "end_lineno": 7680,
          "span": 3,
          "decorators": [],
          "signature": "(self, envelope: dict)",
          "returns": "None",
          "doc": null
        }
      ]
    },
    {
      "name": "StateLockedError",
      "bases": [
        "Exception"
      ],
      "lineno": 7683,
      "end_lineno": 7695,
      "span": 13,
      "doc": "Raised when State.__init__ cannot acquire the per-run-directory",
      "methods": [
        {
          "name": "__init__",
          "qualname": "StateLockedError.__init__",
          "async": false,
          "lineno": 7693,
          "end_lineno": 7695,
          "span": 3,
          "decorators": [],
          "signature": "(self, run_dir: Path)",
          "returns": null,
          "doc": null
        }
      ]
    },
    {
      "name": "State",
      "bases": [],
      "lineno": 7701,
      "end_lineno": 7830,
      "span": 130,
      "doc": "In-memory run state with atomic on-disk persistence.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "State.__init__",
          "async": false,
          "lineno": 7730,
          "end_lineno": 7750,
          "span": 21,
          "decorators": [],
          "signature": "(self, leerie_root: Path, run_id: str, repo_root: Path | None = None)",
          "returns": null,
          "doc": null
        },
        {
          "name": "_acquire_lock",
          "qualname": "State._acquire_lock",
          "async": false,
          "lineno": 7752,
          "end_lineno": 7766,
          "span": 15,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": "Open run_dir for flock and acquire EX|NB. Raises"
        },
        {
          "name": "release_lock",
          "qualname": "State.release_lock",
          "async": false,
          "lineno": 7768,
          "end_lineno": 7783,
          "span": 16,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": "Explicit release. Idempotent. The kernel also releases on"
        },
        {
          "name": "__del__",
          "qualname": "State.__del__",
          "async": false,
          "lineno": 7785,
          "end_lineno": 7796,
          "span": 12,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": null
        },
        {
          "name": "load",
          "qualname": "State.load",
          "async": false,
          "lineno": 7798,
          "end_lineno": 7802,
          "span": 5,
          "decorators": [],
          "signature": "(self)",
          "returns": "bool",
          "doc": null
        },
        {
          "name": "save",
          "qualname": "State.save",
          "async": false,
          "lineno": 7804,
          "end_lineno": 7813,
          "span": 10,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": "Atomic write via temp-file rename."
        },
        {
          "name": "bump_workers",
          "qualname": "State.bump_workers",
          "async": false,
          "lineno": 7815,
          "end_lineno": 7823,
          "span": 9,
          "decorators": [],
          "signature": "(self, caps: dict)",
          "returns": "None",
          "doc": null
        },
        {
          "name": "add_telemetry",
          "qualname": "State.add_telemetry",
          "async": false,
          "lineno": 7825,
          "end_lineno": 7830,
          "span": 6,
          "decorators": [],
          "signature": "(self, envelope: dict)",
          "returns": "None",
          "doc": "Accumulate run-weight signals from a worker envelope. On a"
        }
      ]
    },
    {
      "name": "HealState",
      "bases": [],
      "lineno": 7963,
      "end_lineno": 8008,
      "span": 46,
      "doc": "Persistent state for one heal-loop run scoped to a single call_type.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "HealState.__init__",
          "async": false,
          "lineno": 7976,
          "end_lineno": 7984,
          "span": 9,
          "decorators": [],
          "signature": "(self, heal_dir: Path, call_type: str)",
          "returns": "None",
          "doc": null
        },
        {
          "name": "save",
          "qualname": "HealState.save",
          "async": false,
          "lineno": 7986,
          "end_lineno": 7997,
          "span": 12,
          "decorators": [],
          "signature": "(self)",
          "returns": "None",
          "doc": "Atomic write via temp-file rename."
        },
        {
          "name": "load",
          "qualname": "HealState.load",
          "async": false,
          "lineno": 7999,
          "end_lineno": 8008,
          "span": 10,
          "decorators": [],
          "signature": "(self)",
          "returns": "bool",
          "doc": "Load state from disk. Returns True if file existed and was loaded."
        }
      ]
    }
  ],
  "callgraph": {
    "fan_in_top": {
      "log": 46,
      "die": 35,
      "run_proc": 18,
      "claude_p": 14,
      "_read_toml_key": 13,
      "load_prompt": 13,
      "now": 11,
      "compute_run_branch": 8,
      "_resolve_positive_int_pref": 8,
      "_resolve_bool_pref": 8,
      "_confidence_issues": 6,
      "gather_or_cancel": 6,
      "_run_checked_loop": 6,
      "_cgroup_request": 5,
      "_build_predecessor_graph": 5,
      "_write_run_json": 5,
      "_resolve_str_pref": 4,
      "is_protected_path": 4,
      "run_script": 4,
      "_format_check_feedback": 4,
      "_enumerate_descendants": 3,
      "_signal_pids": 3,
      "_resolve_enum_pref": 3,
      "_parse_bool_envtoml": 3,
      "_terminate_proc_tree": 3,
      "_iter_log_tool_use": 3,
      "judge_capture": 3,
      "_tarjan_sccs": 3,
      "_attribute_cycle_edges": 3,
      "_shared_files_in_scc": 3,
      "_format_provision_recipe_section": 3,
      "discover_rules_files": 3,
      "_format_rules_paths": 3,
      "_cgroup_stat": 2,
      "_format_run_duration": 2,
      "_validate_run_json": 2,
      "discover_runs": 2,
      "_derive_run_status": 2,
      "_invoke": 2,
      "_expand_braces": 2
    },
    "fan_out_top": {
      "main": 43,
      "_run_phases": 31,
      "phase_reconcile": 22,
      "run_final_conformance": 18,
      "settle_subtask": 16,
      "_run_conformance_phase": 14,
      "phase_provision": 13,
      "phase_plan": 11,
      "phase_overlap_judge": 11,
      "integrate_wave": 11,
      "_compose_pr_via_llm": 11,
      "_invoke": 10,
      "phase_finalize": 10,
      "phase_execute": 8,
      "phase_heal": 7,
      "capture_repo_deps": 6,
      "claude_p": 6,
      "phase_classify": 6,
      "run_implementer": 6,
      "preflight": 5,
      "_summarize_stream_event": 5,
      "_build_cycle_retry_prompt": 5,
      "_apply_overlap_collisions": 5,
      "run_conformer": 5,
      "_DescendantTracker._poll_loop": 4,
      "filter_satisfied_subtasks": 4,
      "check_diff_scope": 4,
      "heal_baseline": 4,
      "heal_replay_patched": 4,
      "synth_mise_go_override": 4,
      "run_mise_install": 4,
      "resolve_run_id": 3,
      "_format_run_for_disambiguation": 3,
      "_collect_run_rows": 3,
      "resolve_worker_memory_max": 3,
      "_resolve_bool_pref": 3,
      "resolve_telemetry_enabled": 3,
      "check_planner_output": 3,
      "validate_plan": 3,
      "extract_readme_sections": 3
    },
    "edges": {
      "_read_version": [],
      "load_prompt": [],
      "is_protected_path": [],
      "resolve_prompt": [],
      "_confidence_schema": [],
      "now": [
        "now"
      ],
      "log": [
        "now"
      ],
      "die": [],
      "RateLimitedExit.__init__": [],
      "detect_session_limit": [
        "now"
      ],
      "_install_signal_handlers": [],
      "_enumerate_descendants": [],
      "_signal_pids": [],
      "_reparented_orphans": [],
      "_DescendantTracker.__init__": [],
      "_DescendantTracker.start": [],
      "_DescendantTracker._poll_loop": [
        "_enumerate_descendants",
        "_cgroup_stat",
        "_reparented_orphans",
        "_signal_pids"
      ],
      "_DescendantTracker.stop_and_reap": [
        "_enumerate_descendants",
        "_signal_pids"
      ],
      "_terminate_proc_tree": [
        "_enumerate_descendants",
        "_signal_pids"
      ],
      "_cleanup_on_abnormal_exit": [
        "log"
      ],
      "_reset_subtask_worktree": [
        "run_proc"
      ],
      "_parse_claude_version": [],
      "_check_claude_cli_version": [
        "die",
        "_parse_claude_version"
      ],
      "compute_run_branch": [],
      "compute_subtask_branch": [],
      "_validate_run_json": [],
      "_format_run_duration": [],
      "compose_pr_body": [
        "_format_run_duration"
      ],
      "_write_run_json": [
        "_validate_run_json"
      ],
      "discover_runs": [
        "log"
      ],
      "resolve_run_id": [
        "discover_runs",
        "die",
        "_format_run_for_disambiguation"
      ],
      "_format_run_for_disambiguation": [
        "_derive_run_status",
        "_format_age",
        "now"
      ],
      "_format_age": [],
      "_derive_run_status": [
        "_validate_run_json"
      ],
      "_collect_run_rows": [
        "discover_runs",
        "_derive_run_status",
        "compute_run_branch"
      ],
      "_render_run_table": [],
      "list_runs": [
        "_collect_run_rows",
        "_render_run_table"
      ],
      "_read_toml_key": [],
      "resolve_task_argument": [
        "die"
      ],
      "resolve_leerie_root": [],
      "_resolve_enum_pref": [
        "die",
        "_read_toml_key"
      ],
      "resolve_source_of_truth": [
        "_resolve_enum_pref"
      ],
      "resolve_runtime": [
        "_resolve_enum_pref"
      ],
      "_resolve_str_pref": [
        "_read_toml_key"
      ],
      "resolve_pr_template": [
        "_resolve_str_pref"
      ],
      "_resolve_positive_int_pref": [
        "die",
        "_read_toml_key"
      ],
      "resolve_confidence_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_max_workers": [
        "_resolve_positive_int_pref"
      ],
      "resolve_max_parallel": [
        "_resolve_positive_int_pref"
      ],
      "resolve_judgment_check_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_planner_check_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_implementer_confidence_retries": [
        "_resolve_positive_int_pref"
      ],
      "resolve_planner_samples": [
        "_resolve_positive_int_pref"
      ],
      "_parse_memory_size": [
        "die"
      ],
      "_auto_worker_memory_max": [],
      "resolve_worker_memory_max": [
        "_parse_memory_size",
        "_read_toml_key",
        "_auto_worker_memory_max"
      ],
      "resolve_inspect_dirs": [
        "_read_toml_key"
      ],
      "_parse_bool_envtoml": [],
      "_resolve_bool_pref": [
        "_parse_bool_envtoml",
        "die",
        "_read_toml_key"
      ],
      "resolve_no_push": [
        "_resolve_bool_pref"
      ],
      "push_will_happen": [],
      "resolve_clarify": [
        "_resolve_bool_pref"
      ],
      "resolve_dangerously_skip_permissions": [
        "_resolve_bool_pref"
      ],
      "resolve_dangerously_allow_uncapped": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_overlap_judge": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_budget_check": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_satisfied_check": [
        "_resolve_bool_pref"
      ],
      "resolve_strict_conformer": [
        "_resolve_bool_pref"
      ],
      "_positive_int": [],
      "resolve_verbosity": [
        "_resolve_enum_pref"
      ],
      "verbosity_from_shortcuts": [],
      "resolve_models": [
        "die",
        "_read_toml_key"
      ],
      "resolve_efforts": [
        "die",
        "_read_toml_key"
      ],
      "resolve_telemetry_enabled": [
        "_parse_bool_envtoml",
        "die",
        "_read_toml_key"
      ],
      "resolve_telemetry_subdir": [
        "_resolve_str_pref"
      ],
      "resolve_judge_dir": [
        "_resolve_str_pref"
      ],
      "resolve_heal_dir": [
        "_resolve_str_pref"
      ],
      "resolve_heal_max_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_heal_success_threshold": [
        "die",
        "_read_toml_key"
      ],
      "run_proc": [
        "_terminate_proc_tree"
      ],
      "run_streaming": [
        "log",
        "_terminate_proc_tree"
      ],
      "gather_or_cancel": [],
      "run_script": [
        "run_proc"
      ],
      "preflight": [
        "run_proc",
        "die",
        "_check_claude_cli_version",
        "log",
        "_invoke"
      ],
      "_confidence_issues": [],
      "check_classifier_output": [
        "_confidence_issues"
      ],
      "_grep_old_pattern": [],
      "_check_migration_surface": [
        "_grep_old_pattern"
      ],
      "check_planner_output": [
        "is_protected_path",
        "_check_migration_surface",
        "_confidence_issues"
      ],
      "check_reconciler_output": [
        "_confidence_issues"
      ],
      "check_overlap_judge_output": [
        "_confidence_issues"
      ],
      "check_provision_output": [
        "_confidence_issues"
      ],
      "check_integrator_output": [
        "_confidence_issues"
      ],
      "check_implementer_output": [],
      "_expand_braces": [
        "_expand_braces"
      ],
      "glob_task_references": [
        "_expand_braces"
      ],
      "extract_task_file_structure": [
        "glob_task_references"
      ],
      "check_task_file_coverage": [],
      "_format_task_file_structure": [],
      "validate_plan": [
        "is_protected_path",
        "die",
        "log"
      ],
      "warn_cross_planner_file_overlap": [
        "log"
      ],
      "warn_layer_gaps": [
        "log"
      ],
      "_resolves_under": [],
      "filter_offtree_subtasks": [
        "_resolves_under",
        "log"
      ],
      "filter_satisfied_subtasks": [
        "log",
        "load_prompt",
        "claude_p",
        "gather_or_cancel"
      ],
      "_lockfile_table_entries": [],
      "detect_recipe_from_lockfiles": [
        "_lockfile_table_entries"
      ],
      "validate_provision_recipe": [],
      "_parse_apt_intents": [],
      "_merge_setup_packages": [],
      "_write_config_toml_keys": [],
      "_capture_installs_from_logs": [
        "_iter_log_tool_use",
        "_parse_apt_intents"
      ],
      "resolve_capture_deps": [
        "_parse_bool_envtoml",
        "_read_toml_key"
      ],
      "capture_repo_deps": [
        "resolve_capture_deps",
        "_capture_installs_from_logs",
        "log",
        "_read_toml_key",
        "_merge_setup_packages",
        "_write_config_toml_keys"
      ],
      "_split_readme_headers": [],
      "_is_install_section": [],
      "_slice_code_fences_with_install_hints": [],
      "extract_readme_sections": [
        "_split_readme_headers",
        "_is_install_section",
        "_slice_code_fences_with_install_hints"
      ],
      "_read_file_safely": [],
      "_sample_workspace_manifests": [
        "_read_file_safely"
      ],
      "gather_provision_fixtures": [
        "_read_file_safely",
        "extract_readme_sections",
        "_sample_workspace_manifests"
      ],
      "_split_checkpoint_sections": [],
      "validate_checkpoint": [
        "_split_checkpoint_sections",
        "_normalize_for_noise",
        "_parse_touched_file_line"
      ],
      "_normalize_for_noise": [
        "_strip_bullet"
      ],
      "_strip_bullet": [],
      "_parse_touched_file_line": [
        "_strip_bullet"
      ],
      "validate_result": [],
      "check_diff_scope": [
        "compute_run_branch",
        "run_proc",
        "is_protected_path",
        "log"
      ],
      "check_merge_committed": [
        "run_proc"
      ],
      "check_integrator_commit": [
        "run_proc"
      ],
      "check_branch_has_commits": [
        "run_proc"
      ],
      "scan_conflict_markers": [
        "run_proc"
      ],
      "validate_resume_state": [
        "die"
      ],
      "_is_auth_or_quota_failure": [],
      "_extract_tool_result_text": [],
      "_is_fork_exhaustion": [],
      "_tool_result_outcome": [],
      "_tag_each_line": [],
      "_summarize_tool_use": [],
      "_summarize_stream_event": [
        "detect_session_limit",
        "_summarize_tool_use",
        "_extract_tool_result_text",
        "_is_fork_exhaustion",
        "_tag_each_line"
      ],
      "_format_progress_prefix": [],
      "_get_progress": [],
      "_cgroup_request": [],
      "_cgroup_probe": [
        "_cgroup_request",
        "log"
      ],
      "_cgroup_create": [
        "_cgroup_probe",
        "_cgroup_request",
        "log"
      ],
      "_cgroup_enroll": [
        "_cgroup_request",
        "log"
      ],
      "_cgroup_destroy": [
        "_cgroup_request"
      ],
      "_cgroup_stat": [
        "_cgroup_request"
      ],
      "enforce_and_record_cgroup_containment": [
        "_cgroup_probe",
        "log",
        "die"
      ],
      "_invoke": [
        "log",
        "_cgroup_create",
        "_cgroup_enroll",
        "now",
        "_tool_result_outcome",
        "_cgroup_stat",
        "_summarize_stream_event",
        "_format_progress_prefix",
        "_terminate_proc_tree",
        "_cgroup_destroy"
      ],
      "_capture_call": [],
      "_collect_memory_sample": [
        "now"
      ],
      "_memory_sampler": [
        "_collect_memory_sample"
      ],
      "claude_p": [
        "_invoke",
        "_get_progress",
        "_capture_call",
        "now",
        "log",
        "_is_auth_or_quota_failure"
      ],
      "replay_capture": [
        "claude_p"
      ],
      "_accumulate_telemetry": [],
      "_ReplayState.__init__": [],
      "_ReplayState.save": [],
      "_ReplayState.bump_workers": [],
      "_ReplayState.add_telemetry": [
        "_accumulate_telemetry"
      ],
      "StateLockedError.__init__": [],
      "State.__init__": [],
      "State._acquire_lock": [],
      "State.release_lock": [],
      "State.__del__": [],
      "State.load": [],
      "State.save": [],
      "State.bump_workers": [],
      "State.add_telemetry": [
        "_accumulate_telemetry"
      ],
      "judge_capture": [
        "load_prompt",
        "claude_p"
      ],
      "phase_judge": [
        "log",
        "judge_capture",
        "gather_or_cancel"
      ],
      "HealState.__init__": [],
      "HealState.save": [],
      "HealState.load": [],
      "heal_baseline": [
        "replay_capture",
        "judge_capture",
        "gather_or_cancel",
        "log"
      ],
      "heal_apply_patch": [
        "log"
      ],
      "heal_replay_patched": [
        "replay_capture",
        "judge_capture",
        "log",
        "gather_or_cancel"
      ],
      "check_convergence": [],
      "write_heal_report": [
        "log"
      ],
      "request_patch": [
        "resolve_prompt",
        "load_prompt",
        "claude_p"
      ],
      "phase_heal": [
        "log",
        "heal_baseline",
        "request_patch",
        "heal_apply_patch",
        "heal_replay_patched",
        "check_convergence",
        "write_heal_report"
      ],
      "run_setup_hook": [
        "die",
        "log",
        "run_streaming"
      ],
      "_existing_mise_toml_path": [],
      "_go_already_pinned": [
        "_existing_mise_toml_path"
      ],
      "_existing_mise_toml_tool_keys": [],
      "_read_idiomatic_pins": [],
      "synth_mise_go_override": [
        "_go_already_pinned",
        "_existing_mise_toml_path",
        "_existing_mise_toml_tool_keys",
        "_read_idiomatic_pins"
      ],
      "_repo_has_version_signal": [],
      "run_mise_install": [
        "_repo_has_version_signal",
        "log",
        "run_streaming",
        "die"
      ],
      "phase_classify": [
        "log",
        "load_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_classifier_output",
        "die"
      ],
      "gather_answers": [
        "die",
        "log"
      ],
      "absorb_supplied_answers": [
        "die"
      ],
      "surface_clarification": [
        "log"
      ],
      "_format_provision_user_prompt": [],
      "phase_provision": [
        "log",
        "run_setup_hook",
        "synth_mise_go_override",
        "run_mise_install",
        "detect_recipe_from_lockfiles",
        "gather_provision_fixtures",
        "load_prompt",
        "_format_provision_user_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_provision_output",
        "die",
        "validate_provision_recipe"
      ],
      "_select_best_planner_sample": [
        "check_planner_output",
        "log"
      ],
      "phase_plan": [
        "log",
        "load_prompt",
        "extract_task_file_structure",
        "_format_task_file_structure",
        "claude_p",
        "check_planner_output",
        "check_task_file_coverage",
        "_run_checked_loop",
        "die",
        "gather_or_cancel",
        "_select_best_planner_sample"
      ],
      "_promote_external_collisions": [],
      "_collect_external_preconditions": [],
      "_compute_unresolved_requires": [],
      "_prune_dead_subtasks": [],
      "_find_oversized_added_subtasks": [],
      "_apply_reconciler_output": [
        "die"
      ],
      "phase_reconcile": [
        "die",
        "_promote_external_collisions",
        "log",
        "_collect_external_preconditions",
        "_compute_unresolved_requires",
        "load_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_reconciler_output",
        "_prune_dead_subtasks",
        "_apply_reconciler_output",
        "_find_oversized_added_subtasks",
        "_build_size_retry_prompt",
        "_build_predecessor_graph",
        "_tarjan_sccs",
        "_format_cycle_diagnostic",
        "_recommend_cycle_resolution",
        "_build_cycle_retry_prompt",
        "_validate_must_include",
        "_recommend_unresolved_resolution",
        "_build_unresolved_retry_prompt",
        "_validate_unresolved_must_include"
      ],
      "_tarjan_sccs": [],
      "_attribute_cycle_edges": [],
      "_format_cycle_diagnostic": [
        "_attribute_cycle_edges",
        "_shared_files_in_scc"
      ],
      "_shared_files_in_scc": [],
      "_original_tag_for_rename_edge": [],
      "_recommend_cycle_resolution": [
        "_attribute_cycle_edges",
        "_original_tag_for_rename_edge",
        "_shared_files_in_scc"
      ],
      "_format_recommendation": [],
      "_format_must_include": [
        "_original_tag_for_rename_edge"
      ],
      "_build_cycle_retry_prompt": [
        "_attribute_cycle_edges",
        "_shared_files_in_scc",
        "_format_recommendation",
        "_format_must_include",
        "_matches_recommendation"
      ],
      "_build_size_retry_prompt": [],
      "_matches_recommendation": [],
      "_validate_must_include": [],
      "_tag_jaccard": [],
      "_recommend_unresolved_resolution": [
        "_tag_jaccard"
      ],
      "_build_unresolved_retry_prompt": [
        "_tag_jaccard"
      ],
      "_validate_unresolved_must_include": [],
      "_compute_overlap_anchors": [],
      "_validate_overlap_judge_output": [
        "die",
        "_compute_overlap_anchors"
      ],
      "_apply_overlap_drop": [],
      "_apply_overlap_merge": [
        "die"
      ],
      "_would_cycle_after": [
        "_build_predecessor_graph",
        "_tarjan_sccs"
      ],
      "_apply_overlap_collisions": [
        "_compute_overlap_anchors",
        "log",
        "_would_cycle_after",
        "_apply_overlap_merge",
        "_apply_overlap_drop"
      ],
      "phase_overlap_judge": [
        "log",
        "load_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_overlap_judge_output",
        "die",
        "_validate_overlap_judge_output",
        "_apply_overlap_collisions",
        "_build_predecessor_graph",
        "_tarjan_sccs",
        "_format_cycle_diagnostic"
      ],
      "_build_predecessor_graph": [],
      "detect_no_work": [],
      "_finish_no_work_run": [
        "log",
        "now",
        "_write_run_json"
      ],
      "schedule": [
        "log",
        "die",
        "_build_predecessor_graph"
      ],
      "check_budget_feasibility": [
        "die"
      ],
      "_write_subtask_artifacts": [],
      "_read_upstream_artifacts": [],
      "_format_upstream_artifacts_for_sid": [
        "_build_predecessor_graph",
        "_read_upstream_artifacts",
        "_format_upstream_artifacts_section"
      ],
      "_format_upstream_artifacts_section": [],
      "write_plan": [],
      "_format_provision_recipe_section": [],
      "run_implementer": [
        "load_prompt",
        "run_script",
        "_format_provision_recipe_section",
        "_format_upstream_artifacts_for_sid",
        "_format_convention_docs_section",
        "claude_p"
      ],
      "_retryable_failure": [],
      "discover_rules_files": [],
      "_format_rules_paths": [],
      "_format_convention_docs_section": [
        "discover_rules_files",
        "_format_rules_paths"
      ],
      "_is_rails_repo": [],
      "_infer_build_lint_test": [
        "_is_rails_repo"
      ],
      "_load_blt_config": [
        "_read_toml_key"
      ],
      "resolve_blt": [
        "_load_blt_config",
        "_infer_build_lint_test",
        "log"
      ],
      "validate_conformance_result": [],
      "_branch_head_sha": [
        "run_proc"
      ],
      "_protected_paths_since": [
        "run_proc",
        "is_protected_path"
      ],
      "rollback_conformer_commits": [
        "run_proc"
      ],
      "_uncommitted_paths": [
        "run_proc"
      ],
      "_unprefixed_conformer_commits": [
        "run_proc"
      ],
      "run_conformer": [
        "load_prompt",
        "_format_rules_paths",
        "_format_provision_recipe_section",
        "claude_p",
        "log"
      ],
      "_summarize_residuals": [],
      "_confidence_axes_clear": [],
      "_format_check_feedback": [],
      "_run_checked_loop": [
        "_format_check_feedback"
      ],
      "_conformance_clean": [],
      "_iter_log_tool_use": [],
      "_count_bash_axis_invocations": [
        "_iter_log_tool_use"
      ],
      "_count_orphaned_bg_axis": [
        "_iter_log_tool_use"
      ],
      "_emit_bash_axis_warnings": [
        "_count_bash_axis_invocations",
        "_count_orphaned_bg_axis"
      ],
      "_run_conformance_phase": [
        "discover_rules_files",
        "resolve_blt",
        "compute_run_branch",
        "_branch_head_sha",
        "run_conformer",
        "validate_conformance_result",
        "check_diff_scope",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "_unprefixed_conformer_commits",
        "_emit_bash_axis_warnings",
        "_format_check_feedback",
        "_conformance_clean",
        "_summarize_residuals"
      ],
      "run_final_conformance": [
        "log",
        "discover_rules_files",
        "resolve_blt",
        "load_prompt",
        "_format_rules_paths",
        "_branch_head_sha",
        "_format_provision_recipe_section",
        "claude_p",
        "validate_conformance_result",
        "_protected_paths_since",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "_unprefixed_conformer_commits",
        "_emit_bash_axis_warnings",
        "_format_check_feedback",
        "_conformance_clean",
        "_summarize_residuals",
        "die"
      ],
      "settle_subtask": [
        "_retryable_failure",
        "log",
        "_reset_subtask_worktree",
        "run_implementer",
        "validate_result",
        "_confidence_axes_clear",
        "compute_run_branch",
        "run_proc",
        "check_implementer_output",
        "_format_check_feedback",
        "check_branch_has_commits",
        "check_diff_scope",
        "_write_subtask_artifacts",
        "_run_conformance_phase",
        "validate_checkpoint",
        "surface_clarification"
      ],
      "integrate_wave": [
        "run_script",
        "die",
        "log",
        "load_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_integrator_output",
        "run_proc",
        "check_merge_committed",
        "compute_run_branch",
        "check_integrator_commit"
      ],
      "phase_execute": [
        "log",
        "run_script",
        "die",
        "run_proc",
        "settle_subtask",
        "gather_or_cancel",
        "integrate_wave",
        "scan_conflict_markers"
      ],
      "find_pr_template": [],
      "_cap_text": [],
      "_strip_leerie_prefix": [],
      "_truncate_diff_sample": [],
      "_final_conformance_payload": [],
      "_compose_pr_via_llm": [
        "find_pr_template",
        "_cap_text",
        "log",
        "compute_run_branch",
        "_truncate_diff_sample",
        "_format_run_duration",
        "_final_conformance_payload",
        "load_prompt",
        "claude_p",
        "_strip_leerie_prefix",
        "_write_run_json"
      ],
      "phase_finalize": [
        "log",
        "die",
        "run_script",
        "run_proc",
        "now",
        "push_will_happen",
        "_write_run_json",
        "capture_repo_deps",
        "_compose_pr_via_llm",
        "compute_run_branch"
      ],
      "orchestrate": [
        "_memory_sampler",
        "_run_phases"
      ],
      "_run_phases": [
        "die",
        "validate_resume_state",
        "log",
        "_read_version",
        "enforce_and_record_cgroup_containment",
        "absorb_supplied_answers",
        "resolve_task_argument",
        "now",
        "preflight",
        "phase_classify",
        "run_proc",
        "_write_run_json",
        "compute_run_branch",
        "phase_provision",
        "gather_answers",
        "phase_plan",
        "phase_reconcile",
        "detect_no_work",
        "_finish_no_work_run",
        "phase_overlap_judge",
        "warn_cross_planner_file_overlap",
        "warn_layer_gaps",
        "filter_offtree_subtasks",
        "filter_satisfied_subtasks",
        "schedule",
        "check_budget_feasibility",
        "validate_plan",
        "write_plan",
        "phase_execute",
        "run_final_conformance",
        "phase_finalize"
      ],
      "main": [
        "_read_version",
        "resolve_leerie_root",
        "list_runs",
        "die",
        "resolve_max_workers",
        "resolve_max_parallel",
        "resolve_confidence_rounds",
        "resolve_judgment_check_rounds",
        "resolve_planner_check_rounds",
        "resolve_implementer_confidence_retries",
        "resolve_planner_samples",
        "resolve_worker_memory_max",
        "verbosity_from_shortcuts",
        "resolve_verbosity",
        "resolve_run_id",
        "log",
        "resolve_source_of_truth",
        "resolve_runtime",
        "resolve_models",
        "resolve_efforts",
        "resolve_no_push",
        "resolve_clarify",
        "resolve_dangerously_skip_permissions",
        "resolve_skip_overlap_judge",
        "resolve_skip_satisfied_check",
        "resolve_skip_budget_check",
        "resolve_strict_conformer",
        "resolve_dangerously_allow_uncapped",
        "resolve_pr_template",
        "resolve_inspect_dirs",
        "resolve_telemetry_enabled",
        "resolve_telemetry_subdir",
        "resolve_judge_dir",
        "resolve_heal_dir",
        "resolve_heal_max_rounds",
        "resolve_heal_success_threshold",
        "phase_judge",
        "phase_heal",
        "_install_signal_handlers",
        "orchestrate",
        "_cleanup_on_abnormal_exit",
        "now",
        "_write_run_json"
      ]
    },
    "ordered_calls": {
      "_read_version": [],
      "load_prompt": [],
      "is_protected_path": [],
      "resolve_prompt": [],
      "_confidence_schema": [],
      "now": [
        "now"
      ],
      "log": [
        "now"
      ],
      "die": [],
      "RateLimitedExit.__init__": [],
      "detect_session_limit": [
        "now"
      ],
      "_install_signal_handlers": [],
      "_enumerate_descendants": [],
      "_signal_pids": [],
      "_reparented_orphans": [],
      "_DescendantTracker.__init__": [],
      "_DescendantTracker.start": [],
      "_DescendantTracker._poll_loop": [
        "_enumerate_descendants",
        "_cgroup_stat",
        "_reparented_orphans",
        "_cgroup_stat",
        "_signal_pids"
      ],
      "_DescendantTracker.stop_and_reap": [
        "_enumerate_descendants",
        "_signal_pids"
      ],
      "_terminate_proc_tree": [
        "_enumerate_descendants",
        "_signal_pids",
        "_enumerate_descendants",
        "_signal_pids"
      ],
      "_cleanup_on_abnormal_exit": [
        "log",
        "log",
        "log",
        "log",
        "log"
      ],
      "_reset_subtask_worktree": [
        "run_proc",
        "run_proc",
        "run_proc"
      ],
      "_parse_claude_version": [],
      "_check_claude_cli_version": [
        "die",
        "_parse_claude_version",
        "die"
      ],
      "compute_run_branch": [],
      "compute_subtask_branch": [],
      "_validate_run_json": [],
      "_format_run_duration": [],
      "compose_pr_body": [
        "_format_run_duration"
      ],
      "_write_run_json": [
        "_validate_run_json"
      ],
      "discover_runs": [
        "log",
        "log",
        "log",
        "log"
      ],
      "resolve_run_id": [
        "discover_runs",
        "die",
        "die",
        "_format_run_for_disambiguation",
        "die"
      ],
      "_format_run_for_disambiguation": [
        "_derive_run_status",
        "_format_age",
        "now"
      ],
      "_format_age": [],
      "_derive_run_status": [
        "_validate_run_json"
      ],
      "_collect_run_rows": [
        "discover_runs",
        "_derive_run_status",
        "compute_run_branch"
      ],
      "_render_run_table": [],
      "list_runs": [
        "_collect_run_rows",
        "_render_run_table"
      ],
      "_read_toml_key": [],
      "resolve_task_argument": [
        "die",
        "die"
      ],
      "resolve_leerie_root": [],
      "_resolve_enum_pref": [
        "die",
        "_read_toml_key",
        "die"
      ],
      "resolve_source_of_truth": [
        "_resolve_enum_pref"
      ],
      "resolve_runtime": [
        "_resolve_enum_pref"
      ],
      "_resolve_str_pref": [
        "_read_toml_key"
      ],
      "resolve_pr_template": [
        "_resolve_str_pref"
      ],
      "_resolve_positive_int_pref": [
        "die",
        "die",
        "_read_toml_key",
        "die",
        "die"
      ],
      "resolve_confidence_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_max_workers": [
        "_resolve_positive_int_pref"
      ],
      "resolve_max_parallel": [
        "_resolve_positive_int_pref"
      ],
      "resolve_judgment_check_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_planner_check_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_implementer_confidence_retries": [
        "_resolve_positive_int_pref"
      ],
      "resolve_planner_samples": [
        "_resolve_positive_int_pref"
      ],
      "_parse_memory_size": [
        "die",
        "die",
        "die"
      ],
      "_auto_worker_memory_max": [],
      "resolve_worker_memory_max": [
        "_parse_memory_size",
        "_parse_memory_size",
        "_read_toml_key",
        "_parse_memory_size",
        "_auto_worker_memory_max"
      ],
      "resolve_inspect_dirs": [
        "_read_toml_key"
      ],
      "_parse_bool_envtoml": [],
      "_resolve_bool_pref": [
        "_parse_bool_envtoml",
        "die",
        "_read_toml_key",
        "_parse_bool_envtoml",
        "die"
      ],
      "resolve_no_push": [
        "_resolve_bool_pref"
      ],
      "push_will_happen": [],
      "resolve_clarify": [
        "_resolve_bool_pref"
      ],
      "resolve_dangerously_skip_permissions": [
        "_resolve_bool_pref"
      ],
      "resolve_dangerously_allow_uncapped": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_overlap_judge": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_budget_check": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_satisfied_check": [
        "_resolve_bool_pref"
      ],
      "resolve_strict_conformer": [
        "_resolve_bool_pref"
      ],
      "_positive_int": [],
      "resolve_verbosity": [
        "_resolve_enum_pref"
      ],
      "verbosity_from_shortcuts": [],
      "resolve_models": [
        "die",
        "_read_toml_key",
        "die"
      ],
      "resolve_efforts": [
        "die",
        "_read_toml_key",
        "die"
      ],
      "resolve_telemetry_enabled": [
        "_parse_bool_envtoml",
        "die",
        "_read_toml_key",
        "_parse_bool_envtoml",
        "die"
      ],
      "resolve_telemetry_subdir": [
        "_resolve_str_pref"
      ],
      "resolve_judge_dir": [
        "_resolve_str_pref"
      ],
      "resolve_heal_dir": [
        "_resolve_str_pref"
      ],
      "resolve_heal_max_rounds": [
        "_resolve_positive_int_pref"
      ],
      "resolve_heal_success_threshold": [
        "die",
        "die",
        "_read_toml_key",
        "die",
        "die"
      ],
      "run_proc": [
        "_terminate_proc_tree",
        "_terminate_proc_tree"
      ],
      "run_streaming": [
        "log",
        "_terminate_proc_tree",
        "_terminate_proc_tree"
      ],
      "gather_or_cancel": [],
      "run_script": [
        "run_proc"
      ],
      "preflight": [
        "run_proc",
        "die",
        "run_proc",
        "die",
        "run_proc",
        "die",
        "_check_claude_cli_version",
        "log",
        "_invoke",
        "die",
        "die",
        "die",
        "log"
      ],
      "_confidence_issues": [],
      "check_classifier_output": [
        "_confidence_issues"
      ],
      "_grep_old_pattern": [],
      "_check_migration_surface": [
        "_grep_old_pattern"
      ],
      "check_planner_output": [
        "is_protected_path",
        "_check_migration_surface",
        "_confidence_issues"
      ],
      "check_reconciler_output": [
        "_confidence_issues"
      ],
      "check_overlap_judge_output": [
        "_confidence_issues"
      ],
      "check_provision_output": [
        "_confidence_issues"
      ],
      "check_integrator_output": [
        "_confidence_issues"
      ],
      "check_implementer_output": [],
      "_expand_braces": [
        "_expand_braces"
      ],
      "glob_task_references": [
        "_expand_braces"
      ],
      "extract_task_file_structure": [
        "glob_task_references"
      ],
      "check_task_file_coverage": [],
      "_format_task_file_structure": [],
      "validate_plan": [
        "is_protected_path",
        "die",
        "log"
      ],
      "warn_cross_planner_file_overlap": [
        "log",
        "log"
      ],
      "warn_layer_gaps": [
        "log",
        "log"
      ],
      "_resolves_under": [],
      "filter_offtree_subtasks": [
        "_resolves_under",
        "_resolves_under",
        "log",
        "log"
      ],
      "filter_satisfied_subtasks": [
        "log",
        "log",
        "load_prompt",
        "claude_p",
        "log",
        "gather_or_cancel",
        "log",
        "log"
      ],
      "_lockfile_table_entries": [],
      "detect_recipe_from_lockfiles": [
        "_lockfile_table_entries"
      ],
      "validate_provision_recipe": [],
      "_parse_apt_intents": [],
      "_merge_setup_packages": [],
      "_write_config_toml_keys": [],
      "_capture_installs_from_logs": [
        "_iter_log_tool_use",
        "_parse_apt_intents"
      ],
      "resolve_capture_deps": [
        "_parse_bool_envtoml",
        "_read_toml_key",
        "_parse_bool_envtoml"
      ],
      "capture_repo_deps": [
        "resolve_capture_deps",
        "_capture_installs_from_logs",
        "log",
        "_read_toml_key",
        "_merge_setup_packages",
        "_write_config_toml_keys",
        "log"
      ],
      "_split_readme_headers": [],
      "_is_install_section": [],
      "_slice_code_fences_with_install_hints": [],
      "extract_readme_sections": [
        "_split_readme_headers",
        "_is_install_section",
        "_slice_code_fences_with_install_hints"
      ],
      "_read_file_safely": [],
      "_sample_workspace_manifests": [
        "_read_file_safely"
      ],
      "gather_provision_fixtures": [
        "_read_file_safely",
        "extract_readme_sections",
        "_read_file_safely",
        "_sample_workspace_manifests",
        "_read_file_safely",
        "_read_file_safely"
      ],
      "_split_checkpoint_sections": [],
      "validate_checkpoint": [
        "_split_checkpoint_sections",
        "_normalize_for_noise",
        "_parse_touched_file_line"
      ],
      "_normalize_for_noise": [
        "_strip_bullet"
      ],
      "_strip_bullet": [],
      "_parse_touched_file_line": [
        "_strip_bullet"
      ],
      "validate_result": [],
      "check_diff_scope": [
        "compute_run_branch",
        "run_proc",
        "is_protected_path",
        "log"
      ],
      "check_merge_committed": [
        "run_proc",
        "run_proc"
      ],
      "check_integrator_commit": [
        "run_proc"
      ],
      "check_branch_has_commits": [
        "run_proc"
      ],
      "scan_conflict_markers": [
        "run_proc"
      ],
      "validate_resume_state": [
        "die",
        "die",
        "die",
        "die"
      ],
      "_is_auth_or_quota_failure": [],
      "_extract_tool_result_text": [],
      "_is_fork_exhaustion": [],
      "_tool_result_outcome": [],
      "_tag_each_line": [],
      "_summarize_tool_use": [],
      "_summarize_stream_event": [
        "detect_session_limit",
        "_summarize_tool_use",
        "_extract_tool_result_text",
        "_is_fork_exhaustion",
        "_tag_each_line",
        "_tag_each_line",
        "_tag_each_line"
      ],
      "_format_progress_prefix": [],
      "_get_progress": [],
      "_cgroup_request": [],
      "_cgroup_probe": [
        "_cgroup_request",
        "log",
        "log"
      ],
      "_cgroup_create": [
        "_cgroup_probe",
        "_cgroup_request",
        "log",
        "log"
      ],
      "_cgroup_enroll": [
        "_cgroup_request",
        "log",
        "log"
      ],
      "_cgroup_destroy": [
        "_cgroup_request"
      ],
      "_cgroup_stat": [
        "_cgroup_request"
      ],
      "enforce_and_record_cgroup_containment": [
        "_cgroup_probe",
        "log",
        "die"
      ],
      "_invoke": [
        "log",
        "_cgroup_create",
        "_cgroup_enroll",
        "now",
        "now",
        "_tool_result_outcome",
        "_cgroup_stat",
        "log",
        "_summarize_stream_event",
        "_format_progress_prefix",
        "log",
        "now",
        "log",
        "log",
        "_terminate_proc_tree",
        "_terminate_proc_tree",
        "_cgroup_destroy",
        "log"
      ],
      "_capture_call": [],
      "_collect_memory_sample": [
        "now"
      ],
      "_memory_sampler": [
        "_collect_memory_sample",
        "_collect_memory_sample"
      ],
      "claude_p": [
        "_invoke",
        "_get_progress",
        "_capture_call",
        "now",
        "log",
        "log",
        "_is_auth_or_quota_failure",
        "log",
        "_is_auth_or_quota_failure"
      ],
      "replay_capture": [
        "claude_p"
      ],
      "_accumulate_telemetry": [],
      "_ReplayState.__init__": [],
      "_ReplayState.save": [],
      "_ReplayState.bump_workers": [],
      "_ReplayState.add_telemetry": [
        "_accumulate_telemetry"
      ],
      "StateLockedError.__init__": [],
      "State.__init__": [],
      "State._acquire_lock": [],
      "State.release_lock": [],
      "State.__del__": [],
      "State.load": [],
      "State.save": [],
      "State.bump_workers": [],
      "State.add_telemetry": [
        "_accumulate_telemetry"
      ],
      "judge_capture": [
        "load_prompt",
        "claude_p"
      ],
      "phase_judge": [
        "log",
        "log",
        "log",
        "log",
        "judge_capture",
        "log",
        "gather_or_cancel",
        "log"
      ],
      "HealState.__init__": [],
      "HealState.save": [],
      "HealState.load": [],
      "heal_baseline": [
        "replay_capture",
        "judge_capture",
        "gather_or_cancel",
        "log"
      ],
      "heal_apply_patch": [
        "log"
      ],
      "heal_replay_patched": [
        "replay_capture",
        "judge_capture",
        "log",
        "gather_or_cancel",
        "log"
      ],
      "check_convergence": [],
      "write_heal_report": [
        "log"
      ],
      "request_patch": [
        "resolve_prompt",
        "load_prompt",
        "claude_p"
      ],
      "phase_heal": [
        "log",
        "heal_baseline",
        "request_patch",
        "heal_apply_patch",
        "heal_replay_patched",
        "check_convergence",
        "log",
        "write_heal_report",
        "log"
      ],
      "run_setup_hook": [
        "die",
        "log",
        "run_streaming",
        "die",
        "die"
      ],
      "_existing_mise_toml_path": [],
      "_go_already_pinned": [
        "_existing_mise_toml_path"
      ],
      "_existing_mise_toml_tool_keys": [],
      "_read_idiomatic_pins": [],
      "synth_mise_go_override": [
        "_go_already_pinned",
        "_existing_mise_toml_path",
        "_existing_mise_toml_tool_keys",
        "_read_idiomatic_pins"
      ],
      "_repo_has_version_signal": [],
      "run_mise_install": [
        "_repo_has_version_signal",
        "log",
        "run_streaming",
        "die",
        "die",
        "log"
      ],
      "phase_classify": [
        "log",
        "load_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_classifier_output",
        "die",
        "log",
        "die",
        "log"
      ],
      "gather_answers": [
        "die",
        "log"
      ],
      "absorb_supplied_answers": [
        "die",
        "die",
        "die",
        "die"
      ],
      "surface_clarification": [
        "log"
      ],
      "_format_provision_user_prompt": [],
      "phase_provision": [
        "log",
        "log",
        "run_setup_hook",
        "synth_mise_go_override",
        "log",
        "run_mise_install",
        "log",
        "detect_recipe_from_lockfiles",
        "log",
        "log",
        "gather_provision_fixtures",
        "load_prompt",
        "_format_provision_user_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_provision_output",
        "die",
        "log",
        "validate_provision_recipe",
        "die",
        "log"
      ],
      "_select_best_planner_sample": [
        "check_planner_output",
        "log"
      ],
      "phase_plan": [
        "log",
        "load_prompt",
        "extract_task_file_structure",
        "_format_task_file_structure",
        "log",
        "claude_p",
        "check_planner_output",
        "check_task_file_coverage",
        "_run_checked_loop",
        "log",
        "log",
        "die",
        "log",
        "gather_or_cancel",
        "log",
        "gather_or_cancel",
        "die",
        "_select_best_planner_sample",
        "log",
        "log"
      ],
      "_promote_external_collisions": [],
      "_collect_external_preconditions": [],
      "_compute_unresolved_requires": [],
      "_prune_dead_subtasks": [],
      "_find_oversized_added_subtasks": [],
      "_apply_reconciler_output": [
        "die",
        "die",
        "die",
        "die",
        "die",
        "die"
      ],
      "phase_reconcile": [
        "die",
        "_promote_external_collisions",
        "log",
        "_collect_external_preconditions",
        "log",
        "_compute_unresolved_requires",
        "log",
        "load_prompt",
        "claude_p",
        "die",
        "_run_checked_loop",
        "check_reconciler_output",
        "die",
        "log",
        "_prune_dead_subtasks",
        "log",
        "_apply_reconciler_output",
        "_find_oversized_added_subtasks",
        "log",
        "_build_size_retry_prompt",
        "log",
        "check_reconciler_output",
        "log",
        "_apply_reconciler_output",
        "_find_oversized_added_subtasks",
        "die",
        "_build_predecessor_graph",
        "_tarjan_sccs",
        "_format_cycle_diagnostic",
        "log",
        "_recommend_cycle_resolution",
        "_build_cycle_retry_prompt",
        "log",
        "_validate_must_include",
        "die",
        "check_reconciler_output",
        "log",
        "_apply_reconciler_output",
        "_build_predecessor_graph",
        "_tarjan_sccs",
        "_format_cycle_diagnostic",
        "die",
        "_promote_external_collisions",
        "log",
        "_collect_external_preconditions",
        "log",
        "_compute_unresolved_requires",
        "log",
        "_recommend_unresolved_resolution",
        "log",
        "_build_unresolved_retry_prompt",
        "log",
        "_validate_unresolved_must_include",
        "die",
        "check_reconciler_output",
        "log",
        "_apply_reconciler_output",
        "_promote_external_collisions",
        "_collect_external_preconditions",
        "_build_predecessor_graph",
        "_tarjan_sccs",
        "_format_cycle_diagnostic",
        "die",
        "_compute_unresolved_requires",
        "die",
        "log"
      ],
      "_tarjan_sccs": [],
      "_attribute_cycle_edges": [],
      "_format_cycle_diagnostic": [
        "_attribute_cycle_edges",
        "_shared_files_in_scc"
      ],
      "_shared_files_in_scc": [],
      "_original_tag_for_rename_edge": [],
      "_recommend_cycle_resolution": [
        "_attribute_cycle_edges",
        "_original_tag_for_rename_edge",
        "_shared_files_in_scc",
        "_original_tag_for_rename_edge"
      ],
      "_format_recommendation": [],
      "_format_must_include": [
        "_original_tag_for_rename_edge"
      ],
      "_build_cycle_retry_prompt": [
        "_attribute_cycle_edges",
        "_shared_files_in_scc",
        "_format_recommendation",
        "_format_must_include",
        "_matches_recommendation"
      ],
      "_build_size_retry_prompt": [],
      "_matches_recommendation": [],
      "_validate_must_include": [],
      "_tag_jaccard": [],
      "_recommend_unresolved_resolution": [
        "_tag_jaccard"
      ],
      "_build_unresolved_retry_prompt": [
        "_tag_jaccard"
      ],
      "_validate_unresolved_must_include": [],
      "_compute_overlap_anchors": [],
      "_validate_overlap_judge_output": [
        "die",
        "die",
        "die",
        "die",
        "die",
        "die",
        "_compute_overlap_anchors",
        "die",
        "die"
      ],
      "_apply_overlap_drop": [],
      "_apply_overlap_merge": [
        "die",
        "die"
      ],
      "_would_cycle_after": [
        "_build_predecessor_graph",
        "_tarjan_sccs"
      ],
      "_apply_overlap_collisions": [
        "_compute_overlap_anchors",
        "log",
        "_would_cycle_after",
        "_apply_overlap_merge",
        "log",
        "_apply_overlap_merge",
        "log",
        "_would_cycle_after",
        "_apply_overlap_drop",
        "log",
        "_apply_overlap_drop",
        "log",
        "_would_cycle_after",
        "_apply_overlap_drop",
        "log",
        "_apply_overlap_drop",
        "log"
      ],
      "phase_overlap_judge": [
        "log",
        "log",
        "log",
        "log",
        "load_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_overlap_judge_output",
        "die",
        "log",
        "log",
        "_validate_overlap_judge_output",
        "die",
        "_apply_overlap_collisions",
        "_build_predecessor_graph",
        "_tarjan_sccs",
        "_format_cycle_diagnostic",
        "die",
        "log"
      ],
      "_build_predecessor_graph": [],
      "detect_no_work": [],
      "_finish_no_work_run": [
        "log",
        "log",
        "now",
        "_write_run_json",
        "log",
        "log"
      ],
      "schedule": [
        "log",
        "die",
        "die",
        "log",
        "_build_predecessor_graph",
        "die",
        "log"
      ],
      "check_budget_feasibility": [
        "die"
      ],
      "_write_subtask_artifacts": [],
      "_read_upstream_artifacts": [],
      "_format_upstream_artifacts_for_sid": [
        "_build_predecessor_graph",
        "_read_upstream_artifacts",
        "_format_upstream_artifacts_section"
      ],
      "_format_upstream_artifacts_section": [],
      "write_plan": [],
      "_format_provision_recipe_section": [],
      "run_implementer": [
        "load_prompt",
        "run_script",
        "_format_provision_recipe_section",
        "_format_upstream_artifacts_for_sid",
        "_format_convention_docs_section",
        "claude_p"
      ],
      "_retryable_failure": [],
      "discover_rules_files": [],
      "_format_rules_paths": [],
      "_format_convention_docs_section": [
        "discover_rules_files",
        "_format_rules_paths"
      ],
      "_is_rails_repo": [],
      "_infer_build_lint_test": [
        "_is_rails_repo"
      ],
      "_load_blt_config": [
        "_read_toml_key"
      ],
      "resolve_blt": [
        "_load_blt_config",
        "_infer_build_lint_test",
        "log",
        "log"
      ],
      "validate_conformance_result": [],
      "_branch_head_sha": [
        "run_proc"
      ],
      "_protected_paths_since": [
        "run_proc",
        "is_protected_path"
      ],
      "rollback_conformer_commits": [
        "run_proc"
      ],
      "_uncommitted_paths": [
        "run_proc"
      ],
      "_unprefixed_conformer_commits": [
        "run_proc"
      ],
      "run_conformer": [
        "load_prompt",
        "_format_rules_paths",
        "_format_provision_recipe_section",
        "claude_p",
        "log",
        "log"
      ],
      "_summarize_residuals": [],
      "_confidence_axes_clear": [],
      "_format_check_feedback": [],
      "_run_checked_loop": [
        "_format_check_feedback"
      ],
      "_conformance_clean": [],
      "_iter_log_tool_use": [],
      "_count_bash_axis_invocations": [
        "_iter_log_tool_use"
      ],
      "_count_orphaned_bg_axis": [
        "_iter_log_tool_use"
      ],
      "_emit_bash_axis_warnings": [
        "_count_bash_axis_invocations",
        "_count_orphaned_bg_axis"
      ],
      "_run_conformance_phase": [
        "discover_rules_files",
        "resolve_blt",
        "compute_run_branch",
        "_branch_head_sha",
        "run_conformer",
        "validate_conformance_result",
        "check_diff_scope",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "_uncommitted_paths",
        "_unprefixed_conformer_commits",
        "_emit_bash_axis_warnings",
        "_format_check_feedback",
        "_conformance_clean",
        "_summarize_residuals",
        "_conformance_clean",
        "_summarize_residuals"
      ],
      "run_final_conformance": [
        "log",
        "log",
        "log",
        "log",
        "discover_rules_files",
        "resolve_blt",
        "load_prompt",
        "_format_rules_paths",
        "_branch_head_sha",
        "_format_provision_recipe_section",
        "claude_p",
        "validate_conformance_result",
        "_protected_paths_since",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "_uncommitted_paths",
        "_unprefixed_conformer_commits",
        "_emit_bash_axis_warnings",
        "_format_check_feedback",
        "_conformance_clean",
        "_summarize_residuals",
        "_conformance_clean",
        "log",
        "die"
      ],
      "settle_subtask": [
        "_retryable_failure",
        "log",
        "log",
        "_reset_subtask_worktree",
        "run_implementer",
        "validate_result",
        "log",
        "_confidence_axes_clear",
        "log",
        "compute_run_branch",
        "run_proc",
        "check_implementer_output",
        "log",
        "_format_check_feedback",
        "check_branch_has_commits",
        "compute_run_branch",
        "log",
        "run_proc",
        "check_diff_scope",
        "_write_subtask_artifacts",
        "_run_conformance_phase",
        "log",
        "validate_checkpoint",
        "log",
        "validate_checkpoint",
        "log",
        "surface_clarification"
      ],
      "integrate_wave": [
        "run_script",
        "die",
        "log",
        "load_prompt",
        "claude_p",
        "_run_checked_loop",
        "check_integrator_output",
        "run_proc",
        "die",
        "log",
        "check_merge_committed",
        "run_proc",
        "die",
        "compute_run_branch",
        "check_integrator_commit",
        "log",
        "log",
        "run_proc",
        "die",
        "compute_run_branch"
      ],
      "phase_execute": [
        "log",
        "run_script",
        "die",
        "run_proc",
        "settle_subtask",
        "log",
        "log",
        "log",
        "log",
        "gather_or_cancel",
        "integrate_wave",
        "scan_conflict_markers",
        "die",
        "log",
        "die"
      ],
      "find_pr_template": [],
      "_cap_text": [],
      "_strip_leerie_prefix": [],
      "_truncate_diff_sample": [],
      "_final_conformance_payload": [],
      "_compose_pr_via_llm": [
        "find_pr_template",
        "_cap_text",
        "log",
        "log",
        "compute_run_branch",
        "_cap_text",
        "_truncate_diff_sample",
        "_format_run_duration",
        "_final_conformance_payload",
        "load_prompt",
        "log",
        "claude_p",
        "_strip_leerie_prefix",
        "log",
        "_write_run_json",
        "log",
        "log"
      ],
      "phase_finalize": [
        "log",
        "die",
        "run_script",
        "die",
        "run_script",
        "run_proc",
        "die",
        "now",
        "push_will_happen",
        "_write_run_json",
        "capture_repo_deps",
        "log",
        "_compose_pr_via_llm",
        "log",
        "compute_run_branch",
        "log",
        "compute_run_branch",
        "log",
        "compute_run_branch",
        "log"
      ],
      "orchestrate": [
        "_memory_sampler",
        "_run_phases"
      ],
      "_run_phases": [
        "die",
        "validate_resume_state",
        "log",
        "log",
        "log",
        "log",
        "die",
        "die",
        "_read_version",
        "enforce_and_record_cgroup_containment",
        "absorb_supplied_answers",
        "die",
        "resolve_task_argument",
        "now",
        "_read_version",
        "enforce_and_record_cgroup_containment",
        "preflight",
        "phase_classify",
        "log",
        "run_proc",
        "_write_run_json",
        "compute_run_branch",
        "phase_provision",
        "gather_answers",
        "phase_plan",
        "phase_reconcile",
        "detect_no_work",
        "_finish_no_work_run",
        "phase_overlap_judge",
        "warn_cross_planner_file_overlap",
        "warn_layer_gaps",
        "filter_offtree_subtasks",
        "filter_satisfied_subtasks",
        "_finish_no_work_run",
        "schedule",
        "check_budget_feasibility",
        "validate_plan",
        "write_plan",
        "phase_execute",
        "run_final_conformance",
        "log",
        "phase_finalize"
      ],
      "main": [
        "_read_version",
        "resolve_leerie_root",
        "list_runs",
        "die",
        "resolve_max_workers",
        "resolve_max_parallel",
        "resolve_confidence_rounds",
        "resolve_judgment_check_rounds",
        "resolve_planner_check_rounds",
        "resolve_implementer_confidence_retries",
        "resolve_planner_samples",
        "resolve_worker_memory_max",
        "verbosity_from_shortcuts",
        "resolve_verbosity",
        "resolve_leerie_root",
        "resolve_run_id",
        "die",
        "log",
        "resolve_source_of_truth",
        "resolve_runtime",
        "resolve_models",
        "log",
        "resolve_efforts",
        "log",
        "resolve_no_push",
        "resolve_clarify",
        "resolve_dangerously_skip_permissions",
        "log",
        "resolve_skip_overlap_judge",
        "resolve_skip_satisfied_check",
        "resolve_skip_budget_check",
        "resolve_strict_conformer",
        "resolve_dangerously_allow_uncapped",
        "resolve_pr_template",
        "resolve_inspect_dirs",
        "resolve_telemetry_enabled",
        "resolve_telemetry_subdir",
        "resolve_judge_dir",
        "resolve_heal_dir",
        "resolve_heal_max_rounds",
        "resolve_heal_success_threshold",
        "resolve_run_id",
        "log",
        "die",
        "phase_judge",
        "log",
        "phase_judge",
        "die",
        "log",
        "log",
        "phase_heal",
        "_install_signal_handlers",
        "orchestrate",
        "log",
        "_cleanup_on_abnormal_exit",
        "log",
        "now",
        "log",
        "log",
        "log",
        "log",
        "log",
        "log",
        "now",
        "_write_run_json",
        "log",
        "_cleanup_on_abnormal_exit",
        "log",
        "die"
      ]
    }
  }
}
