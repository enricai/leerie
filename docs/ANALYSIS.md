{
  "preprocess": {
    "path": "orchestrator/leerie.py",
    "sha256": "c726c82c09ee007ad5bc0e9e798acda02c0ab1e3765b476346c17452c71cdf54",
    "lines": 19491,
    "bytes": 919039,
    "module_doc": "Leerie \u2014 deterministic task orchestrator for Claude Code."
  },
  "lex": {
    "total_tokens": 104465,
    "token_class_counts": {
      "OP": 37842,
      "NAME": 30507,
      "NL": 8916,
      "NEWLINE": 7447,
      "STRING": 6351,
      "COMMENT": 3553,
      "INDENT": 2407,
      "DEDENT": 2407,
      "FSTRING_MIDDLE": 1977,
      "FSTRING_START": 1148,
      "FSTRING_END": 1148,
      "NUMBER": 761,
      "ENDMARKER": 1
    },
    "keyword_counts": {
      "if": 1326,
      "in": 845,
      "None": 741,
      "return": 655,
      "for": 632,
      "not": 551,
      "or": 489,
      "def": 372,
      "and": 222,
      "is": 195,
      "await": 189,
      "except": 181,
      "try": 168,
      "continue": 164,
      "else": 164,
      "True": 162,
      "False": 129,
      "async": 94,
      "as": 66,
      "raise": 58,
      "break": 44,
      "pass": 41,
      "elif": 39,
      "import": 32,
      "with": 24
    },
    "top_identifiers": {
      "get": 980,
      "str": 965,
      "dict": 518,
      "st": 477,
      "sid": 351,
      "list": 342,
      "append": 312,
      "s": 311,
      "repo_root": 311,
      "r": 305,
      "log": 228,
      "data": 221,
      "len": 209,
      "Path": 206,
      "caps": 203,
      "e": 197,
      "int": 184,
      "out": 151,
      "set": 134,
      "plans": 131,
      "args": 130,
      "plan": 121,
      "strip": 118,
      "f": 115,
      "die": 112,
      "bool": 111,
      "self": 110,
      "join": 109,
      "line": 104,
      "models": 102,
      "efforts": 102,
      "entry": 100,
      "parts": 96,
      "p": 92,
      "a": 82,
      "c": 81,
      "output": 81,
      "run_id": 78,
      "leerie_dir": 76,
      "name": 75,
      "ValueError": 74,
      "is_file": 74,
      "lines": 74,
      "json": 72,
      "text": 72,
      "run_dir": 70,
      "tag": 70,
      "tuple": 69,
      "OSError": 68,
      "v": 68,
      "n": 68,
      "cli_value": 68,
      "i": 68,
      "save": 67,
      "subtasks": 66,
      "cwd": 65,
      "asyncio": 63,
      "os": 63,
      "w": 62,
      "cmd": 62
    },
    "top_operators": {
      "(": 5577,
      ")": 5577,
      ",": 5011,
      ":": 4686,
      "=": 3450,
      ".": 3395,
      "[": 2246,
      "]": 2246,
      "{": 1989,
      "}": 1989,
      "->": 362,
      "/": 248,
      "|": 204,
      "==": 191,
      "!": 130,
      "+": 128,
      "*": 70,
      "!=": 65,
      "-": 53,
      "+=": 52,
      ">": 50,
      "<": 42,
      ">=": 27,
      "<=": 21,
      "**": 12
    },
    "comment_chars": 208144,
    "string_literal_count": 6351,
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
      "LEERIE_MODEL_DEP_CAPTURE",
      "LEERIE_MODEL_HEAL",
      "LEERIE_MODEL_JUDGE",
      "LEERIE_MODEL_PR_WRITER",
      "LEERIE_NO_PUSH",
      "LEERIE_PLANNER_CHECK_ROUNDS",
      "LEERIE_PLANNER_SAMPLES",
      "LEERIE_PR_TEMPLATE",
      "LEERIE_RUNTIME",
      "LEERIE_SKIP_BASE_BASELINE",
      "LEERIE_SKIP_BUDGET_CHECK",
      "LEERIE_SKIP_OVERLAP_JUDGE",
      "LEERIE_SKIP_REPO_MAP",
      "LEERIE_SKIP_SATISFIED_CHECK",
      "LEERIE_SOURCE_OF_TRUTH",
      "LEERIE_STATE_DIR",
      "LEERIE_STATE_HOST_DIR",
      "LEERIE_STRICT_CONFORMER",
      "LEERIE_VERBOSITY",
      "LEERIE_WORKER_DEBUG",
      "LEERIE_WORKER_MEMORY_MAX",
      "LEERIE_WORKER_PIDS_MAX"
    ],
    "exit_symbols": [
      "EXIT_BUDGET_INFEASIBLE",
      "EXIT_LOCKED",
      "EXIT_NEEDS_ANSWERS"
    ]
  },
  "ast": {
    "node_class_counts": {
      "Load": 19762,
      "Name": 17131,
      "Constant": 9116,
      "Call": 4624,
      "Store": 3583,
      "Attribute": 3394,
      "Assign": 2255,
      "Subscript": 1500,
      "Expr": 1462,
      "FormattedValue": 1349,
      "If": 1159,
      "arg": 868,
      "keyword": 833,
      "Compare": 799,
      "JoinedStr": 784,
      "Return": 655,
      "BinOp": 654,
      "Tuple": 652,
      "List": 647,
      "BoolOp": 632,
      "Dict": 581,
      "Or": 434,
      "For": 407,
      "arguments": 393,
      "UnaryOp": 357,
      "Not": 325,
      "FunctionDef": 287,
      "AnnAssign": 264,
      "Div": 248,
      "comprehension": 222,
      "BitOr": 205,
      "And": 198,
      "Eq": 191,
      "Await": 189,
      "ExceptHandler": 181,
      "Add": 180,
      "Try": 168,
      "Continue": 164,
      "IsNot": 136,
      "In": 123,
      "IfExp": 103,
      "ListComp": 99,
      "NotIn": 90,
      "AsyncFunctionDef": 85,
      "GeneratorExp": 80,
      "NotEq": 65,
      "Slice": 61,
      "Is": 59,
      "Raise": 58,
      "AugAssign": 57,
      "Gt": 50,
      "Break": 44,
      "alias": 42,
      "Lt": 42,
      "Pass": 41,
      "Mult": 33,
      "USub": 32,
      "GtE": 27,
      "Import": 24,
      "withitem": 24,
      "Starred": 23,
      "Sub": 23,
      "DictComp": 22,
      "Lambda": 21,
      "LtE": 21,
      "SetComp": 20,
      "While": 18,
      "With": 18,
      "Set": 17,
      "ClassDef": 10,
      "ImportFrom": 8,
      "FloorDiv": 8,
      "Pow": 7,
      "AsyncWith": 6,
      "BitAnd": 6,
      "Nonlocal": 4,
      "AsyncFor": 3,
      "Global": 2,
      "Delete": 2,
      "Del": 2,
      "Module": 1,
      "Yield": 1,
      "NamedExpr": 1,
      "LShift": 1
    },
    "imports": [
      "from __future__ import annotations",
      "argparse",
      "asyncio",
      "contextlib",
      "copy",
      "ctypes",
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
      "from zoneinfo import ZoneInfo, ZoneInfoNotFoundError"
    ],
    "constant_count": 175,
    "function_count": 302,
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
      "len": 21,
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
        "budget_safety_margin",
        "repo_map_tokens",
        "decompose_max_depth",
        "decompose_fit_threshold",
        "decompose_noprogress_rounds"
      ]
    },
    {
      "name": "STATE_FIELDS",
      "lineno": 235,
      "kind": "tuple",
      "len": 41,
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
        "skip_base_baseline",
        "skip_repo_map",
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
        "leerie_version",
        "dep_capture_done"
      ]
    },
    {
      "name": "CATEGORIES",
      "lineno": 327,
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
      "lineno": 336,
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
      "lineno": 362,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".leerie/",
        ".git/"
      ]
    },
    {
      "name": "_CLAUDE_DELIVERABLE_PREFIXES",
      "lineno": 363,
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
      "lineno": 378,
      "kind": "str",
      "value": "Read,Grep,Glob,WebSearch,WebFetch"
    },
    {
      "name": "INSPECT_TOOLS",
      "lineno": 402,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(find:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(tree:*),Bash(pwd),Bash(echo:*),Bash(git log:*),Bash(git s"
    },
    {
      "name": "ACT_TOOLS",
      "lineno": 410,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash,Write,Edit'"
    },
    {
      "name": "SATISFIED_PROBE_TOOLS",
      "lineno": 424,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(pwd),Bash(echo:*),Bash(git show HEAD:*),Bash(git diff:*),Bash(git status)'"
    },
    {
      "name": "DISALLOWED_TOOLS",
      "lineno": 438,
      "kind": "str",
      "value": "Agent,SendMessage,ScheduleWakeup,CronCreate,CronDelete,CronList,RemoteTrigger,PushNotification"
    },
    {
      "name": "INSPECT_DIRS_ENV",
      "lineno": 452,
      "kind": "str",
      "value": "LEERIE_INSPECT_DIRS"
    },
    {
      "name": "INSPECT_DIRS_FILE",
      "lineno": 453,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EXIT_NEEDS_ANSWERS",
      "lineno": 455,
      "kind": "int",
      "value": 10
    },
    {
      "name": "EXIT_BUDGET_INFEASIBLE",
      "lineno": 466,
      "kind": "int",
      "value": 11
    },
    {
      "name": "EXIT_LOCKED",
      "lineno": 474,
      "kind": "int",
      "value": 75
    },
    {
      "name": "RATE_LIMIT_RETRY_BACKOFF_SEC",
      "lineno": 483,
      "kind": "int",
      "value": 300
    },
    {
      "name": "SOURCE_OF_TRUTH_VALUES",
      "lineno": 491,
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
      "lineno": 492,
      "kind": "str",
      "value": "LEERIE_SOURCE_OF_TRUTH"
    },
    {
      "name": "SOURCE_OF_TRUTH_FILE",
      "lineno": 493,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "RUNTIME_VALUES",
      "lineno": 498,
      "kind": "tuple",
      "len": 2,
      "values": [
        "local",
        "fly"
      ]
    },
    {
      "name": "RUNTIME_ENV",
      "lineno": 499,
      "kind": "str",
      "value": "LEERIE_RUNTIME"
    },
    {
      "name": "RUNTIME_FILE",
      "lineno": 500,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CONFIDENCE_ROUNDS_ENV",
      "lineno": 507,
      "kind": "str",
      "value": "LEERIE_CONFIDENCE_ROUNDS"
    },
    {
      "name": "CONFIDENCE_ROUNDS_FILE",
      "lineno": 508,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "JUDGMENT_CHECK_ROUNDS_ENV",
      "lineno": 511,
      "kind": "str",
      "value": "LEERIE_JUDGMENT_CHECK_ROUNDS"
    },
    {
      "name": "PLANNER_CHECK_ROUNDS_ENV",
      "lineno": 512,
      "kind": "str",
      "value": "LEERIE_PLANNER_CHECK_ROUNDS"
    },
    {
      "name": "IMPLEMENTER_CONFIDENCE_RETRIES_ENV",
      "lineno": 513,
      "kind": "str",
      "value": "LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES"
    },
    {
      "name": "PLANNER_SAMPLES_ENV",
      "lineno": 514,
      "kind": "str",
      "value": "LEERIE_PLANNER_SAMPLES"
    },
    {
      "name": "MAX_WORKERS_ENV",
      "lineno": 519,
      "kind": "str",
      "value": "LEERIE_MAX_WORKERS"
    },
    {
      "name": "MAX_WORKERS_FILE",
      "lineno": 520,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "MAX_PARALLEL_ENV",
      "lineno": 525,
      "kind": "str",
      "value": "LEERIE_MAX_PARALLEL"
    },
    {
      "name": "MAX_PARALLEL_FILE",
      "lineno": 526,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "WORKER_MEMORY_MAX_ENV",
      "lineno": 533,
      "kind": "str",
      "value": "LEERIE_WORKER_MEMORY_MAX"
    },
    {
      "name": "WORKER_MEMORY_MAX_FILE",
      "lineno": 534,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "WORKER_PIDS_MAX_ENV",
      "lineno": 540,
      "kind": "str",
      "value": "LEERIE_WORKER_PIDS_MAX"
    },
    {
      "name": "WORKER_PIDS_MAX_FILE",
      "lineno": 541,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "NO_PUSH_ENV",
      "lineno": 549,
      "kind": "str",
      "value": "LEERIE_NO_PUSH"
    },
    {
      "name": "NO_PUSH_FILE",
      "lineno": 550,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CLARIFY_ENV",
      "lineno": 558,
      "kind": "str",
      "value": "LEERIE_CLARIFY"
    },
    {
      "name": "CLARIFY_FILE",
      "lineno": 559,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_ENV",
      "lineno": 569,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_SKIP_PERMISSIONS"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_FILE",
      "lineno": 570,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_ENV",
      "lineno": 586,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_ALLOW_UNCAPPED"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_FILE",
      "lineno": 587,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_ENV",
      "lineno": 596,
      "kind": "str",
      "value": "LEERIE_SKIP_OVERLAP_JUDGE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_FILE",
      "lineno": 597,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_BUDGET_CHECK_ENV",
      "lineno": 609,
      "kind": "str",
      "value": "LEERIE_SKIP_BUDGET_CHECK"
    },
    {
      "name": "SKIP_BUDGET_CHECK_FILE",
      "lineno": 610,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "STRICT_CONFORMER_ENV",
      "lineno": 612,
      "kind": "str",
      "value": "LEERIE_STRICT_CONFORMER"
    },
    {
      "name": "STRICT_CONFORMER_FILE",
      "lineno": 613,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_BASE_BASELINE_ENV",
      "lineno": 626,
      "kind": "str",
      "value": "LEERIE_SKIP_BASE_BASELINE"
    },
    {
      "name": "SKIP_BASE_BASELINE_FILE",
      "lineno": 627,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_REPO_MAP_ENV",
      "lineno": 636,
      "kind": "str",
      "value": "LEERIE_SKIP_REPO_MAP"
    },
    {
      "name": "SKIP_REPO_MAP_FILE",
      "lineno": 637,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "REPO_MAP_CACHE_DIR",
      "lineno": 643,
      "kind": "str",
      "value": "repo-map-cache"
    },
    {
      "name": "CAPTURE_DEPS_ENV",
      "lineno": 649,
      "kind": "str",
      "value": "LEERIE_CAPTURE_DEPS"
    },
    {
      "name": "CAPTURE_DEPS_CONFIG",
      "lineno": 650,
      "kind": "str",
      "value": ".leerie/config.toml"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_ENV",
      "lineno": 662,
      "kind": "str",
      "value": "LEERIE_SKIP_SATISFIED_CHECK"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_FILE",
      "lineno": 663,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "PR_TEMPLATE_ENV",
      "lineno": 672,
      "kind": "str",
      "value": "LEERIE_PR_TEMPLATE"
    },
    {
      "name": "PR_TEMPLATE_FILE",
      "lineno": 673,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "VERBOSITY_VALUES",
      "lineno": 681,
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
      "lineno": 682,
      "kind": "str",
      "value": "stream"
    },
    {
      "name": "VERBOSITY_ENV",
      "lineno": 683,
      "kind": "str",
      "value": "LEERIE_VERBOSITY"
    },
    {
      "name": "VERBOSITY_FILE",
      "lineno": 684,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "_TERMINAL_STATUSES",
      "lineno": 687,
      "kind": "expr",
      "expr": "frozenset({'complete', 'failed', 'blocked'})"
    },
    {
      "name": "MODEL_VALUES",
      "lineno": 693,
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
      "lineno": 700,
      "kind": "str",
      "value": "opus"
    },
    {
      "name": "MODEL_DEFAULT_PER_WORKER",
      "lineno": 704,
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
      "lineno": 716,
      "kind": "str",
      "value": "LEERIE_MODEL"
    },
    {
      "name": "MODEL_FILE",
      "lineno": 717,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EFFORT_VALUES",
      "lineno": 725,
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
      "lineno": 726,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "EFFORT_DEFAULT_PER_WORKER",
      "lineno": 727,
      "kind": "dict",
      "len": 10,
      "keys": [
        "classifier",
        "planner",
        "reconciler",
        "plan_overlap_judge",
        "provision",
        "integrator",
        "pr_writer",
        "dep_capture",
        "fit_judge",
        "splitter"
      ]
    },
    {
      "name": "EFFORT_ENV",
      "lineno": 739,
      "kind": "str",
      "value": "LEERIE_EFFORT"
    },
    {
      "name": "WORKER_TYPES",
      "lineno": 740,
      "kind": "tuple",
      "len": 11,
      "values": [
        "classifier",
        "planner",
        "reconciler",
        "plan_overlap_judge",
        "satisfied_probe",
        "provision",
        "implementer",
        "integrator",
        "conformer",
        "fit_judge",
        "splitter"
      ]
    },
    {
      "name": "MODEL_JUDGE_ENV",
      "lineno": 746,
      "kind": "str",
      "value": "LEERIE_MODEL_JUDGE"
    },
    {
      "name": "MODEL_HEAL_ENV",
      "lineno": 747,
      "kind": "str",
      "value": "LEERIE_MODEL_HEAL"
    },
    {
      "name": "MODEL_PR_WRITER_ENV",
      "lineno": 748,
      "kind": "str",
      "value": "LEERIE_MODEL_PR_WRITER"
    },
    {
      "name": "MODEL_DEP_CAPTURE_ENV",
      "lineno": 749,
      "kind": "str",
      "value": "LEERIE_MODEL_DEP_CAPTURE"
    },
    {
      "name": "JUDGE_DIR_DEFAULT",
      "lineno": 754,
      "kind": "str",
      "value": "judge-out"
    },
    {
      "name": "JUDGE_DIR_ENV",
      "lineno": 755,
      "kind": "str",
      "value": "LEERIE_JUDGE_DIR"
    },
    {
      "name": "JUDGE_DIR_FILE",
      "lineno": 756,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_DIR_DEFAULT",
      "lineno": 761,
      "kind": "str",
      "value": "heal-out"
    },
    {
      "name": "HEAL_DIR_ENV",
      "lineno": 762,
      "kind": "str",
      "value": "LEERIE_HEAL_DIR"
    },
    {
      "name": "HEAL_DIR_FILE",
      "lineno": 763,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_MAX_ROUNDS_DEFAULT",
      "lineno": 768,
      "kind": "int",
      "value": 10
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_DEFAULT",
      "lineno": 769,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "HEAL_PLATEAU_WINDOW_DEFAULT",
      "lineno": 770,
      "kind": "int",
      "value": 3
    },
    {
      "name": "HEAL_PLATEAU_DELTA_DEFAULT",
      "lineno": 771,
      "kind": "float",
      "value": 0.03
    },
    {
      "name": "HEAL_N_REPLAYS_DEFAULT",
      "lineno": 772,
      "kind": "int",
      "value": 5
    },
    {
      "name": "HEAL_MAX_ROUNDS_ENV",
      "lineno": 773,
      "kind": "str",
      "value": "LEERIE_HEAL_MAX_ROUNDS"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_ENV",
      "lineno": 774,
      "kind": "str",
      "value": "LEERIE_HEAL_SUCCESS_THRESHOLD"
    },
    {
      "name": "HEAL_MAX_ROUNDS_FILE",
      "lineno": 775,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_FILE",
      "lineno": 776,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "STATE_DIR_ENV",
      "lineno": 784,
      "kind": "str",
      "value": "LEERIE_STATE_DIR"
    },
    {
      "name": "_CONFORMER_BLT_PROP",
      "lineno": 817,
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
      "lineno": 836,
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
      "lineno": 872,
      "kind": "dict",
      "len": 15,
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
        "dep_capture",
        "provision",
        "plan_overlap_judge",
        "satisfied_probe",
        "fit_judge",
        "splitter"
      ]
    },
    {
      "name": "_SESSION_LIMIT_PREFIX",
      "lineno": 1700,
      "kind": "expr",
      "expr": "re.compile(\"you've hit your session limit\", re.IGNORECASE)"
    },
    {
      "name": "_SESSION_LIMIT_RESET",
      "lineno": 1702,
      "kind": "expr",
      "expr": "re.compile('resets?\\\\s+(\\\\d{1,2}):(\\\\d{2})\\\\s*([ap]m)\\\\s*\\\\(([^)]+)\\\\)', re.IGNORECASE)"
    },
    {
      "name": "_RATE_LIMIT_ALLOWED_STATUSES",
      "lineno": 1711,
      "kind": "tuple",
      "len": 2,
      "values": [
        "allowed",
        "allowed_warning"
      ]
    },
    {
      "name": "_PROC_TREE_GRACE_SEC",
      "lineno": 1769,
      "kind": "float",
      "value": 2.0
    },
    {
      "name": "_DESCENDANT_POLL_SEC",
      "lineno": 1873,
      "kind": "float",
      "value": 0.5
    },
    {
      "name": "_PID_REAP_HIGH_WATER",
      "lineno": 1880,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "_PID_REAP_LOW_WATER",
      "lineno": 1881,
      "kind": "float",
      "value": 0.75
    },
    {
      "name": "_PID_REAP_MIN_AGE_SEC",
      "lineno": 1882,
      "kind": "int",
      "value": 60
    },
    {
      "name": "_PID_EXHAUSTION_WINDOW",
      "lineno": 1897,
      "kind": "int",
      "value": 6
    },
    {
      "name": "_PID_EXHAUSTION_ERROR_THRESHOLD",
      "lineno": 1898,
      "kind": "int",
      "value": 3
    },
    {
      "name": "RUN_STATUSES",
      "lineno": 2832,
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
      "lineno": 3196,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".txt",
        ".md"
      ]
    },
    {
      "name": "_MEMORY_SUFFIX_MULTIPLIER",
      "lineno": 3453,
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
      "lineno": 4425,
      "kind": "expr",
      "expr": "frozenset((f'{v}-' for v in CATEGORY_ABBREV.values()))"
    },
    {
      "name": "_VALID_EXTENTS",
      "lineno": 4428,
      "kind": "expr",
      "expr": "frozenset({'in_plan', 'external'})"
    },
    {
      "name": "_MIGRATION_SIGNAL_RE",
      "lineno": 4511,
      "kind": "expr",
      "expr": "re.compile('replac(?:es?|ing)\\\\s+(?:direct\\\\s+)?[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|migrat(?:es?|ing)\\\\s+from\\\\s+[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|extract(?:s|ing)\\\\s+[`\\'\\\\\"]?([a-zA"
    },
    {
      "name": "_MIGRATION_SURFACE_THRESHOLD",
      "lineno": 4519,
      "kind": "int",
      "value": 5
    },
    {
      "name": "_GLOB_CHARS",
      "lineno": 5167,
      "kind": "expr",
      "expr": "frozenset('*?[{')"
    },
    {
      "name": "_BRACE_RE",
      "lineno": 5168,
      "kind": "expr",
      "expr": "re.compile('\\\\{([^}]+)\\\\}')"
    },
    {
      "name": "_MAX_COVERAGE_ITEMS",
      "lineno": 5256,
      "kind": "int",
      "value": 50
    },
    {
      "name": "_SOURCE_EXTS",
      "lineno": 5398,
      "kind": "expr",
      "expr": "frozenset({'.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.go', '.rs', '.rb', '.java', '.kt', '.c', '.h', '.cc', '.cpp', '.hpp', '.cs', '.php', '.swift', '.scala', '.sh', '.lua'})"
    },
    {
      "name": "_repo_map_empty_warned",
      "lineno": 5404,
      "kind": "bool",
      "value": false
    },
    {
      "name": "_ENV_TAG_KEYWORDS",
      "lineno": 5941,
      "kind": "expr",
      "expr": "frozenset({'env', 'bootstrap', 'secret', 'config-key', 'credential'})"
    },
    {
      "name": "_PROVISION_ARGV0_ALLOW",
      "lineno": 6298,
      "kind": "expr",
      "expr": "frozenset({'pnpm', 'npm', 'yarn', 'pip', 'pip3', 'uv', 'poetry', 'pipenv', 'go', 'cargo', 'bundle', 'gem', 'mvn', 'gradle', 'gradlew', 'make', 'composer', 'dotnet'})"
    },
    {
      "name": "_PROVISION_SHELL_METACHARS",
      "lineno": 6309,
      "kind": "expr",
      "expr": "frozenset(set('|&;$`><\\n\\r'))"
    },
    {
      "name": "_README_SECTION_RE",
      "lineno": 6562,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(install|getting[\\\\s-]?started|quick[\\\\s-]?start|setup|usage|\\\\brun\\\\b|develop|build(ing)?( from source| instructions)?|compil(e|ing)( from source)?|download|from source|requirement"
    },
    {
      "name": "_HEADER_DECOR_RE",
      "lineno": 6587,
      "kind": "expr",
      "expr": "re.compile('^[^\\\\w]+', flags=re.UNICODE)"
    },
    {
      "name": "_INSTALL_CMD_HINT_RE",
      "lineno": 6592,
      "kind": "expr",
      "expr": "re.compile('\\\\b(pip|pip3|npm|pnpm|yarn|uv|poetry|cargo|brew|apt|apt-get|dnf|yum|pacman|go install|make|bundle install|gem install|mise install)\\\\b')"
    },
    {
      "name": "_DEPCAP_TOTAL_BUDGET",
      "lineno": 6603,
      "kind": "int",
      "value": 307200
    },
    {
      "name": "_DEP_MANIFEST_NAMES",
      "lineno": 6614,
      "kind": "tuple",
      "len": 19,
      "values": [
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-test.txt",
        "pyproject.toml",
        "Pipfile",
        "Pipfile.lock",
        "setup.py",
        "setup.cfg",
        "package.json",
        "pnpm-lock.yaml",
        "package-lock.json",
        "yarn.lock",
        "go.mod",
        "Cargo.toml",
        "Cargo.lock",
        "Gemfile",
        "Gemfile.lock",
        "composer.json",
        "composer.lock"
      ]
    },
    {
      "name": "_DEPCAP_MANIFEST_FILE_BUDGET",
      "lineno": 6621,
      "kind": "int",
      "value": 16384
    },
    {
      "name": "_DEPCAP_MANIFEST_TOTAL_BUDGET",
      "lineno": 6622,
      "kind": "int",
      "value": 131072
    },
    {
      "name": "_DEPCAP_INSTALL_RE",
      "lineno": 6629,
      "kind": "expr",
      "expr": "re.compile('(?:^|[\\\\s;&|(])(?:sudo\\\\s+)?(?:apt-get\\\\s+install|apt\\\\s+install|yum\\\\s+install|dnf\\\\s+install|apk\\\\s+add|pip3?\\\\s+install|pipx\\\\s+install|poetry\\\\s+add|uv\\\\s+(?:add|pip\\\\s+install)|npm\\\\s"
    },
    {
      "name": "_DEPCAP_TEXT_TOOLS",
      "lineno": 6640,
      "kind": "expr",
      "expr": "frozenset({'grep', 'rg', 'git', 'sed', 'awk', 'echo', 'cat', 'printf', 'ag', 'ack'})"
    },
    {
      "name": "_DEPCAP_SEGMENT_RE",
      "lineno": 6647,
      "kind": "expr",
      "expr": "re.compile('[\\\\n;]|&&|\\\\|\\\\||[|&]')"
    },
    {
      "name": "_README_INTRO_BUDGET",
      "lineno": 7254,
      "kind": "int",
      "value": 1024
    },
    {
      "name": "_README_EXTRACT_BUDGET",
      "lineno": 7255,
      "kind": "int",
      "value": 8192
    },
    {
      "name": "_README_FALLBACK_BUDGET",
      "lineno": 7256,
      "kind": "int",
      "value": 6144
    },
    {
      "name": "_FIXTURE_TOTAL_BUDGET",
      "lineno": 7257,
      "kind": "int",
      "value": 24576
    },
    {
      "name": "_PROVISION_ROOT_MANIFESTS",
      "lineno": 7331,
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
      "lineno": 7336,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(ci|test|build|release)\\\\b')"
    },
    {
      "name": "_PROVISION_WORKFLOW_SKIP_RE",
      "lineno": 7337,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(codeql|stale|dependabot)\\\\b')"
    },
    {
      "name": "_CHECKPOINT_SECTIONS",
      "lineno": 7493,
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
      "lineno": 7506,
      "kind": "set",
      "len": 2,
      "values": [
        "## Decisions made",
        "## Open unknowns"
      ]
    },
    {
      "name": "_NOISE_TOKENS",
      "lineno": 7514,
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
      "lineno": 8050,
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
      "lineno": 8444,
      "kind": "str",
      "value": "/run/leerie-cgroup.sock"
    },
    {
      "name": "_CGROUP_PROBE_RESULT",
      "lineno": 8445,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_CGROUP_HIERARCHY",
      "lineno": 8446,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_PR_SET_CHILD_SUBREAPER",
      "lineno": 9216,
      "kind": "int",
      "value": 36
    },
    {
      "name": "_PR_GET_CHILD_SUBREAPER",
      "lineno": 9217,
      "kind": "int",
      "value": 37
    },
    {
      "name": "_ASYNCIO_MANAGED_PIDS",
      "lineno": 9229,
      "kind": "expr",
      "expr": "set()"
    },
    {
      "name": "_DOCS_ONLY_CATEGORIES",
      "lineno": 10663,
      "kind": "expr",
      "expr": "frozenset({'documentation'})"
    },
    {
      "name": "_GO_MOD_VERSION_RE",
      "lineno": 10740,
      "kind": "expr",
      "expr": "re.compile('^\\\\s*go\\\\s+(\\\\d+(?:\\\\.\\\\d+){0,2})\\\\s*$', re.MULTILINE)"
    },
    {
      "name": "_LEADING_V_RE",
      "lineno": 10795,
      "kind": "expr",
      "expr": "re.compile('^[vV]+')"
    },
    {
      "name": "_IDIOMATIC_VERSION_FILES",
      "lineno": 10806,
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
      "lineno": 10821,
      "kind": "dict",
      "len": 2,
      "keys": [
        "nodejs",
        "python3"
      ]
    },
    {
      "name": "_MISE_SIGNAL_FILES",
      "lineno": 11048,
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
      "lineno": 15584,
      "kind": "expr",
      "expr": "frozenset({'no_commits', 'dirty_worktree', 'empty_handoff'})"
    },
    {
      "name": "_RULES_FILE_CANDIDATES",
      "lineno": 15634,
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
      "lineno": 16234,
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
      "lineno": 16258,
      "kind": "str",
      "value": "Command running in background with ID:"
    },
    {
      "name": "_BG_ID_RE",
      "lineno": 16259,
      "kind": "expr",
      "expr": "re.compile('Command running in background with ID:\\\\s*(\\\\w+)')"
    },
    {
      "name": "_PR_TEMPLATE_SINGLE_LOCATIONS",
      "lineno": 17687,
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
      "lineno": 17695,
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
      "lineno": 17756,
      "kind": "int",
      "value": 80000
    },
    {
      "name": "PR_WRITER_TEMPLATE_MAX_BYTES",
      "lineno": 17757,
      "kind": "int",
      "value": 32000
    },
    {
      "name": "PR_WRITER_DIFF_SAMPLE_MAX_LINES",
      "lineno": 17758,
      "kind": "int",
      "value": 500
    },
    {
      "name": "PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES",
      "lineno": 17764,
      "kind": "int",
      "value": 8000
    },
    {
      "name": "_LEERIE_PREFIX_RE",
      "lineno": 17792,
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
      "lineno": 368,
      "end_lineno": 376,
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
      "lineno": 789,
      "end_lineno": 804,
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
      "lineno": 847,
      "end_lineno": 870,
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
      "lineno": 1621,
      "end_lineno": 1622,
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
      "lineno": 1625,
      "end_lineno": 1628,
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
      "lineno": 1631,
      "end_lineno": 1633,
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
      "lineno": 1714,
      "end_lineno": 1753,
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
      "lineno": 1756,
      "end_lineno": 1766,
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
      "lineno": 1772,
      "end_lineno": 1811,
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
      "lineno": 1814,
      "end_lineno": 1828,
      "span": 15,
      "decorators": [],
      "signature": "(pids: set[int], sig: int)",
      "returns": "None",
      "doc": "Best-effort signal delivery to a set of PIDs. Drops ProcessLookupError"
    },
    {
      "name": "_reparented_orphans",
      "qualname": "_reparented_orphans",
      "async": false,
      "lineno": 1831,
      "end_lineno": 1870,
      "span": 40,
      "decorators": [],
      "signature": "(seen: set[int])",
      "returns": "list[int]",
      "doc": "Return PIDs from `seen` that are currently alive, reparented to"
    },
    {
      "name": "_terminate_proc_tree",
      "qualname": "_terminate_proc_tree",
      "async": true,
      "lineno": 2016,
      "end_lineno": 2084,
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
      "lineno": 2087,
      "end_lineno": 2202,
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
      "lineno": 2205,
      "end_lineno": 2228,
      "span": 24,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, run_id: str)",
      "returns": "None",
      "doc": "Remove the per-subtask worktree directory and branch so a corrective"
    },
    {
      "name": "_sleep_then_reexec",
      "qualname": "_sleep_then_reexec",
      "async": false,
      "lineno": 2231,
      "end_lineno": 2301,
      "span": 71,
      "decorators": [],
      "signature": "(st: 'State', wait_seconds: int, reason: str)",
      "returns": "int | None",
      "doc": "Shared tail of the rate-limit auto-resume path (DESIGN \u00a76 *Rate-limited"
    },
    {
      "name": "_parse_claude_version",
      "qualname": "_parse_claude_version",
      "async": false,
      "lineno": 2304,
      "end_lineno": 2309,
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
      "lineno": 2312,
      "end_lineno": 2342,
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
      "lineno": 2356,
      "end_lineno": 2367,
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
      "lineno": 2370,
      "end_lineno": 2379,
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
      "lineno": 2384,
      "end_lineno": 2480,
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
      "lineno": 2485,
      "end_lineno": 2506,
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
      "lineno": 2509,
      "end_lineno": 2591,
      "span": 83,
      "decorators": [],
      "signature": "(state: dict, run_id: str)",
      "returns": "str",
      "doc": "Generate the deterministic fallback PR body from run state +"
    },
    {
      "name": "_write_run_json",
      "qualname": "_write_run_json",
      "async": false,
      "lineno": 2594,
      "end_lineno": 2619,
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
      "lineno": 2624,
      "end_lineno": 2711,
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
      "lineno": 2714,
      "end_lineno": 2748,
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
      "lineno": 2751,
      "end_lineno": 2796,
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
      "lineno": 2799,
      "end_lineno": 2816,
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
      "lineno": 2848,
      "end_lineno": 2935,
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
      "lineno": 2938,
      "end_lineno": 2975,
      "span": 38,
      "decorators": [],
      "signature": "(leerie_root: Path)",
      "returns": "list[tuple[str, str, str, str, bool, str]]",
      "doc": "Build (run_id, started_at, status, branch, is_fly, cost) rows for"
    },
    {
      "name": "_render_run_table",
      "qualname": "_render_run_table",
      "async": false,
      "lineno": 2978,
      "end_lineno": 2995,
      "span": 18,
      "decorators": [],
      "signature": "(rows: list[tuple[str, str, str, str, bool, str]])",
      "returns": "None",
      "doc": "Print rows as a columnar table with auto-sized columns. Column"
    },
    {
      "name": "list_runs",
      "qualname": "list_runs",
      "async": false,
      "lineno": 2998,
      "end_lineno": 3032,
      "span": 35,
      "decorators": [],
      "signature": "(leerie_root: Path, status_filter: str | None = None, runtime_filter: str | None = None)",
      "returns": "None",
      "doc": "Render a sortable columnar table of runs to stdout. Used by"
    },
    {
      "name": "_aggregate_calls",
      "qualname": "_aggregate_calls",
      "async": false,
      "lineno": 3035,
      "end_lineno": 3066,
      "span": 32,
      "decorators": [],
      "signature": "(calls_path: Path)",
      "returns": "dict[str, dict]",
      "doc": "Group a run's calls.ndjson by call_type, summing per-type counts,"
    },
    {
      "name": "_memory_peak",
      "qualname": "_memory_peak",
      "async": false,
      "lineno": 3069,
      "end_lineno": 3093,
      "span": 25,
      "decorators": [],
      "signature": "(mem_path: Path)",
      "returns": "dict | None",
      "doc": "Read a run's memory.ndjson and return peak rss_kb + max open_fds /"
    },
    {
      "name": "report_run",
      "qualname": "report_run",
      "async": false,
      "lineno": 3096,
      "end_lineno": 3174,
      "span": 79,
      "decorators": [],
      "signature": "(leerie_root: Path, cli_run_id: str | None)",
      "returns": "None",
      "doc": "Print a telemetry report for one run: header (status, duration,"
    },
    {
      "name": "_read_toml_key",
      "qualname": "_read_toml_key",
      "async": false,
      "lineno": 3177,
      "end_lineno": 3193,
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
      "lineno": 3199,
      "end_lineno": 3227,
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
      "lineno": 3230,
      "end_lineno": 3245,
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
      "lineno": 3248,
      "end_lineno": 3267,
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
      "lineno": 3270,
      "end_lineno": 3282,
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
      "lineno": 3285,
      "end_lineno": 3296,
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
      "lineno": 3299,
      "end_lineno": 3313,
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
      "lineno": 3316,
      "end_lineno": 3328,
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
      "lineno": 3331,
      "end_lineno": 3357,
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
      "lineno": 3360,
      "end_lineno": 3372,
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
      "lineno": 3375,
      "end_lineno": 3387,
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
      "lineno": 3390,
      "end_lineno": 3402,
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
      "lineno": 3405,
      "end_lineno": 3414,
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
      "lineno": 3417,
      "end_lineno": 3426,
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
      "lineno": 3429,
      "end_lineno": 3439,
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
      "lineno": 3442,
      "end_lineno": 3450,
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
      "lineno": 3458,
      "end_lineno": 3482,
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
      "lineno": 3485,
      "end_lineno": 3520,
      "span": 36,
      "decorators": [],
      "signature": "(max_parallel: int)",
      "returns": "int",
      "doc": "Auto-derive a per-worker memory cap from /proc/meminfo."
    },
    {
      "name": "resolve_worker_memory_max",
      "qualname": "resolve_worker_memory_max",
      "async": false,
      "lineno": 3523,
      "end_lineno": 3543,
      "span": 21,
      "decorators": [],
      "signature": "(repo_root: Path, max_parallel: int, cli_value: str | None = None)",
      "returns": "int",
      "doc": "Resolve the per-worker cgroup memory cap (bytes). Order:"
    },
    {
      "name": "resolve_worker_pids_max",
      "qualname": "resolve_worker_pids_max",
      "async": false,
      "lineno": 3546,
      "end_lineno": 3558,
      "span": 13,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: int | None = None)",
      "returns": "int",
      "doc": "Resolve the per-worker cgroup PID cap (pids.max). Order:"
    },
    {
      "name": "resolve_inspect_dirs",
      "qualname": "resolve_inspect_dirs",
      "async": false,
      "lineno": 3561,
      "end_lineno": 3599,
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
      "lineno": 3602,
      "end_lineno": 3614,
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
      "lineno": 3617,
      "end_lineno": 3644,
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
      "lineno": 3647,
      "end_lineno": 3654,
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
      "lineno": 3657,
      "end_lineno": 3681,
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
      "lineno": 3684,
      "end_lineno": 3691,
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
      "lineno": 3694,
      "end_lineno": 3713,
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
      "lineno": 3716,
      "end_lineno": 3731,
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
      "lineno": 3734,
      "end_lineno": 3751,
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
      "lineno": 3754,
      "end_lineno": 3773,
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
      "lineno": 3776,
      "end_lineno": 3798,
      "span": 23,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --skip-satisfied-check preference. Order:"
    },
    {
      "name": "resolve_strict_conformer",
      "qualname": "resolve_strict_conformer",
      "async": false,
      "lineno": 3801,
      "end_lineno": 3815,
      "span": 15,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --strict-conformer preference. Order:"
    },
    {
      "name": "resolve_skip_base_baseline",
      "qualname": "resolve_skip_base_baseline",
      "async": false,
      "lineno": 3818,
      "end_lineno": 3833,
      "span": 16,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --skip-base-baseline preference. Order:"
    },
    {
      "name": "resolve_skip_repo_map",
      "qualname": "resolve_skip_repo_map",
      "async": false,
      "lineno": 3836,
      "end_lineno": 3851,
      "span": 16,
      "decorators": [],
      "signature": "(repo_root: Path, cli_value: bool)",
      "returns": "bool",
      "doc": "Resolve the --skip-repo-map preference. Order:"
    },
    {
      "name": "_positive_int",
      "qualname": "_positive_int",
      "async": false,
      "lineno": 3854,
      "end_lineno": 3863,
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
      "lineno": 3866,
      "end_lineno": 3885,
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
      "lineno": 3888,
      "end_lineno": 3906,
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
      "lineno": 3909,
      "end_lineno": 3990,
      "span": 82,
      "decorators": [],
      "signature": "(repo_root: Path, args)",
      "returns": "dict[str, str]",
      "doc": "Resolve the model alias for each worker type. Per-worker"
    },
    {
      "name": "resolve_efforts",
      "qualname": "resolve_efforts",
      "async": false,
      "lineno": 3993,
      "end_lineno": 4054,
      "span": 62,
      "decorators": [],
      "signature": "(repo_root: Path, args)",
      "returns": "dict[str, str | None]",
      "doc": "Resolve the --effort value for each worker type. Mirrors"
    },
    {
      "name": "resolve_judge_dir",
      "qualname": "resolve_judge_dir",
      "async": false,
      "lineno": 4057,
      "end_lineno": 4066,
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
      "lineno": 4069,
      "end_lineno": 4078,
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
      "lineno": 4081,
      "end_lineno": 4090,
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
      "lineno": 4093,
      "end_lineno": 4120,
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
      "lineno": 4123,
      "end_lineno": 4167,
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
      "lineno": 4170,
      "end_lineno": 4293,
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
      "lineno": 4296,
      "end_lineno": 4317,
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
      "lineno": 4320,
      "end_lineno": 4322,
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
      "lineno": 4335,
      "end_lineno": 4422,
      "span": 88,
      "decorators": [],
      "signature": "(leerie_dir: Path, verbosity: str = VERBOSITY_DEFAULT, skip_smoke: bool = False, no_push: bool = False)",
      "returns": "None",
      "doc": "Hard checks before any LLM work. Fails fast rather than wasting workers."
    },
    {
      "name": "_confidence_issues",
      "qualname": "_confidence_issues",
      "async": false,
      "lineno": 4437,
      "end_lineno": 4455,
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
      "lineno": 4458,
      "end_lineno": 4504,
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
      "lineno": 4522,
      "end_lineno": 4544,
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
      "lineno": 4547,
      "end_lineno": 4575,
      "span": 29,
      "decorators": [],
      "signature": "(subtasks: list[dict], repo_root: Path)",
      "returns": "list[str]",
      "doc": "UNCOVERED_MIGRATION_SURFACE check (DESIGN \u00a75)."
    },
    {
      "name": "partition_files",
      "qualname": "partition_files",
      "async": false,
      "lineno": 4582,
      "end_lineno": 4598,
      "span": 17,
      "decorators": [],
      "signature": "(files: list[str], chunk_size: int)",
      "returns": "list[list[str]]",
      "doc": "Partition *files* into non-overlapping chunks of at most *chunk_size*."
    },
    {
      "name": "_migration_child",
      "qualname": "_migration_child",
      "async": false,
      "lineno": 4601,
      "end_lineno": 4618,
      "span": 18,
      "decorators": [],
      "signature": "(subtask: dict, chunk: list[str], cid: str, title: str, criteria: str)",
      "returns": "dict",
      "doc": "Build one migration-chunk child subtask from its (code-fixed) file"
    },
    {
      "name": "_deterministic_chunk_label",
      "qualname": "_deterministic_chunk_label",
      "async": false,
      "lineno": 4621,
      "end_lineno": 4633,
      "span": 13,
      "decorators": [],
      "signature": "(subtask: dict, chunk: list[str], idx: int, total: int)",
      "returns": "tuple[str, str]",
      "doc": "Fallback title + criteria for a migration chunk when the splitter"
    },
    {
      "name": "_label_migration_chunks",
      "qualname": "_label_migration_chunks",
      "async": true,
      "lineno": 4636,
      "end_lineno": 4702,
      "span": 67,
      "decorators": [],
      "signature": "(subtask: dict, chunks: list[list[str]], base_id: str, depth: int, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None], repo_root: Path, wrap_repo_map: Callable[[str], str])",
      "returns": "list[dict]",
      "doc": "LABEL-ONLY splitter pass for migration chunks (DESIGN \u00a75\u00bd \u2014 \"the LLM"
    },
    {
      "name": "recursive_decompose",
      "qualname": "recursive_decompose",
      "async": true,
      "lineno": 4705,
      "end_lineno": 4875,
      "span": 171,
      "decorators": [],
      "signature": "(subtask: dict, depth: int, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None], repo_root: Path, *, repo_map: dict | None = None, _parent_score: float | None = None, _noprogress_count: int = 0)",
      "returns": "list[dict]",
      "doc": "Recursively decompose *subtask* until leaves pass the P1 fit threshold."
    },
    {
      "name": "check_planner_output",
      "qualname": "check_planner_output",
      "async": false,
      "lineno": 4878,
      "end_lineno": 4985,
      "span": 108,
      "decorators": [],
      "signature": "(result: dict, repo_root: Path, domain: str)",
      "returns": "list[str]",
      "doc": "Rich mechanical checks on a single planner domain's output."
    },
    {
      "name": "check_reconciler_output",
      "qualname": "check_reconciler_output",
      "async": false,
      "lineno": 4988,
      "end_lineno": 5019,
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
      "lineno": 5022,
      "end_lineno": 5083,
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
      "lineno": 5086,
      "end_lineno": 5125,
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
      "lineno": 5128,
      "end_lineno": 5131,
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
      "lineno": 5134,
      "end_lineno": 5156,
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
      "lineno": 5171,
      "end_lineno": 5184,
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
      "lineno": 5187,
      "end_lineno": 5216,
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
      "lineno": 5219,
      "end_lineno": 5253,
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
      "lineno": 5259,
      "end_lineno": 5290,
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
      "lineno": 5293,
      "end_lineno": 5304,
      "span": 12,
      "decorators": [],
      "signature": "(items: list[str])",
      "returns": "str",
      "doc": "Format extracted structure as an external coverage reference"
    },
    {
      "name": "_repo_map_cache_key",
      "qualname": "_repo_map_cache_key",
      "async": false,
      "lineno": 5318,
      "end_lineno": 5324,
      "span": 7,
      "decorators": [],
      "signature": "(path: Path)",
      "returns": "str",
      "doc": "Return a stable cache key: '<abs_path>@<mtime_ns>'."
    },
    {
      "name": "_walk_calls",
      "qualname": "_walk_calls",
      "async": false,
      "lineno": 5327,
      "end_lineno": 5350,
      "span": 24,
      "decorators": [],
      "signature": "(node: 'object')",
      "returns": "list[str]",
      "doc": "Collect identifier names from call-expression function positions."
    },
    {
      "name": "_parse_repo_file",
      "qualname": "_parse_repo_file",
      "async": false,
      "lineno": 5353,
      "end_lineno": 5392,
      "span": 40,
      "decorators": [],
      "signature": "(path: Path)",
      "returns": "tuple[list[str], list[str]]",
      "doc": "Parse one source file and return ``(defs, refs)``."
    },
    {
      "name": "_tree_sitter_extraction_works",
      "qualname": "_tree_sitter_extraction_works",
      "async": false,
      "lineno": 5407,
      "end_lineno": 5422,
      "span": 16,
      "decorators": [],
      "signature": "()",
      "returns": "bool",
      "doc": "True only if the tree-sitter stack can actually extract a symbol."
    },
    {
      "name": "_warn_repo_map_empty_once",
      "qualname": "_warn_repo_map_empty_once",
      "async": false,
      "lineno": 5425,
      "end_lineno": 5443,
      "span": 19,
      "decorators": [],
      "signature": "(source_candidates: int)",
      "returns": "None",
      "doc": "Emit at most one warning per process when build_repo_map produces an"
    },
    {
      "name": "build_repo_map",
      "qualname": "build_repo_map",
      "async": false,
      "lineno": 5446,
      "end_lineno": 5554,
      "span": 109,
      "decorators": [],
      "signature": "(repo_root: Path, leerie_root: Path)",
      "returns": "dict",
      "doc": "Build (or update) the repo-map symbol/reference graph for *repo_root*."
    },
    {
      "name": "_pagerank",
      "qualname": "_pagerank",
      "async": false,
      "lineno": 5557,
      "end_lineno": 5605,
      "span": 49,
      "decorators": [],
      "signature": "(graph: dict[str, set[str]], personalization: dict[str, float], damping: float = 0.85, max_iter: int = 100, tol: float = 1e-06)",
      "returns": "dict[str, float]",
      "doc": "Personalized PageRank on a directed graph (stdlib-only, no networkx)."
    },
    {
      "name": "_render_repo_map_subgraph",
      "qualname": "_render_repo_map_subgraph",
      "async": false,
      "lineno": 5608,
      "end_lineno": 5630,
      "span": 23,
      "decorators": [],
      "signature": "(repo_map: dict, ranked_files: list[tuple[str, float]], max_files: int)",
      "returns": "str",
      "doc": "Render the top *max_files* files from *ranked_files* as a compact"
    },
    {
      "name": "_count_tokens_approx",
      "qualname": "_count_tokens_approx",
      "async": false,
      "lineno": 5633,
      "end_lineno": 5635,
      "span": 3,
      "decorators": [],
      "signature": "(text: str)",
      "returns": "int",
      "doc": "Approximate token count: ~4 bytes per token (GPT/Claude typical)."
    },
    {
      "name": "rank_repo_map",
      "qualname": "rank_repo_map",
      "async": false,
      "lineno": 5638,
      "end_lineno": 5728,
      "span": 91,
      "decorators": [],
      "signature": "(repo_map: dict, seed_files: list[str], seed_symbols: list[str], token_budget: int | None = None)",
      "returns": "str",
      "doc": "Run personalized PageRank on *repo_map* biased toward *seed_files* and"
    },
    {
      "name": "validate_plan",
      "qualname": "validate_plan",
      "async": false,
      "lineno": 5731,
      "end_lineno": 5837,
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
      "lineno": 5840,
      "end_lineno": 5871,
      "span": 32,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "None",
      "doc": "Log a warning when subtasks from different planner outputs both list"
    },
    {
      "name": "warn_provider_subset_subtasks",
      "qualname": "warn_provider_subset_subtasks",
      "async": false,
      "lineno": 5874,
      "end_lineno": 5938,
      "span": 65,
      "decorators": [],
      "signature": "(plans: list[dict])",
      "returns": "None",
      "doc": "Advisory plan-time warning (DESIGN \u00a75): flag a subtask whose ENTIRE"
    },
    {
      "name": "warn_layer_gaps",
      "qualname": "warn_layer_gaps",
      "async": false,
      "lineno": 5945,
      "end_lineno": 5983,
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
      "lineno": 5986,
      "end_lineno": 5998,
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
      "lineno": 6001,
      "end_lineno": 6058,
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
      "lineno": 6061,
      "end_lineno": 6209,
      "span": 149,
      "decorators": [],
      "signature": "(plans: list[dict], repo_root: Path, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "dict[str, str] | None",
      "doc": "Mutate `plans` in place: drop any subtask whose success criteria"
    },
    {
      "name": "probe_criteria_satisfied_on_head",
      "qualname": "probe_criteria_satisfied_on_head",
      "async": true,
      "lineno": 6212,
      "end_lineno": 6286,
      "span": 75,
      "decorators": [],
      "signature": "(subtask: dict, worktree: str, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "dict | None",
      "doc": "Post-execution analogue of `filter_satisfied_subtasks`'s per-subtask"
    },
    {
      "name": "_lockfile_table_entries",
      "qualname": "_lockfile_table_entries",
      "async": false,
      "lineno": 6312,
      "end_lineno": 6425,
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
      "lineno": 6428,
      "end_lineno": 6433,
      "span": 6,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "list[dict]",
      "doc": "Public entry point for the deterministic detection layer. Returns"
    },
    {
      "name": "_normalize_pip_installs",
      "qualname": "_normalize_pip_installs",
      "async": false,
      "lineno": 6436,
      "end_lineno": 6467,
      "span": 32,
      "decorators": [],
      "signature": "(recipe: list[dict])",
      "returns": "list[dict]",
      "doc": "Add `--break-system-packages` to every `pip`/`pip3`/`python -m pip`"
    },
    {
      "name": "_is_pip_install",
      "qualname": "_is_pip_install",
      "async": false,
      "lineno": 6470,
      "end_lineno": 6489,
      "span": 20,
      "decorators": [],
      "signature": "(cmd: list[str])",
      "returns": "bool",
      "doc": "True iff `cmd` (an argv list) is a pip *install* invocation \u2014 bare"
    },
    {
      "name": "validate_provision_recipe",
      "qualname": "validate_provision_recipe",
      "async": false,
      "lineno": 6492,
      "end_lineno": 6553,
      "span": 62,
      "decorators": [],
      "signature": "(recipe: list[dict])",
      "returns": "None",
      "doc": "Mechanically bound the provision recipe. Raises ValueError on any"
    },
    {
      "name": "_is_install_command",
      "qualname": "_is_install_command",
      "async": false,
      "lineno": 6650,
      "end_lineno": 6673,
      "span": 24,
      "decorators": [],
      "signature": "(cmd: str)",
      "returns": "bool",
      "doc": "True if `cmd` invokes a package-manager install verb (SECONDARY hint)."
    },
    {
      "name": "_gather_dep_manifests",
      "qualname": "_gather_dep_manifests",
      "async": false,
      "lineno": 6676,
      "end_lineno": 6702,
      "span": 27,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "str",
      "doc": "Read the repo's dependency-manifest files (PRIMARY dep_capture corpus)."
    },
    {
      "name": "_normalize_setup_packages",
      "qualname": "_normalize_setup_packages",
      "async": false,
      "lineno": 6705,
      "end_lineno": 6709,
      "span": 5,
      "decorators": [],
      "signature": "(pkgs: list[str])",
      "returns": "str",
      "doc": "Render a package list in the canonical persisted form: order-preserving"
    },
    {
      "name": "_merge_setup_packages",
      "qualname": "_merge_setup_packages",
      "async": false,
      "lineno": 6712,
      "end_lineno": 6729,
      "span": 18,
      "decorators": [],
      "signature": "(existing: str, captured: list[str])",
      "returns": "str | None",
      "doc": "Union existing setup_packages with newly-captured packages."
    },
    {
      "name": "_dump_language_installs",
      "qualname": "_dump_language_installs",
      "async": false,
      "lineno": 6732,
      "end_lineno": 6743,
      "span": 12,
      "decorators": [],
      "signature": "(entries: list[dict])",
      "returns": "str",
      "doc": "JSON-encode `language_installs` for TOML persistence, single-quote-safe."
    },
    {
      "name": "_toml_value",
      "qualname": "_toml_value",
      "async": false,
      "lineno": 6746,
      "end_lineno": 6760,
      "span": 15,
      "decorators": [],
      "signature": "(val: str)",
      "returns": "str",
      "doc": "Render `val` as a TOML string literal."
    },
    {
      "name": "_write_config_toml_keys",
      "qualname": "_write_config_toml_keys",
      "async": false,
      "lineno": 6763,
      "end_lineno": 6802,
      "span": 40,
      "decorators": [],
      "signature": "(cfg_path: Path, updates: dict[str, str])",
      "returns": "None",
      "doc": "Minimal deterministic TOML upsert for a set of key-value pairs."
    },
    {
      "name": "_extract_depcap_commands",
      "qualname": "_extract_depcap_commands",
      "async": false,
      "lineno": 6805,
      "end_lineno": 6831,
      "span": 27,
      "decorators": [],
      "signature": "(log_dir: Path)",
      "returns": "tuple[str, bool]",
      "doc": "Install-shaped Bash commands from worker logs (SECONDARY dep_capture hint)."
    },
    {
      "name": "resolve_capture_deps",
      "qualname": "resolve_capture_deps",
      "async": false,
      "lineno": 6834,
      "end_lineno": 6857,
      "span": 24,
      "decorators": [],
      "signature": "(repo_root: Path)",
      "returns": "bool",
      "doc": "Resolve the capture_deps preference (DESIGN \u00a76\u00bd)."
    },
    {
      "name": "capture_repo_deps",
      "qualname": "capture_repo_deps",
      "async": true,
      "lineno": 6860,
      "end_lineno": 7021,
      "span": 162,
      "decorators": [],
      "signature": "(repo_root: Path, st: object, caps: dict | None = None, models: dict[str, str] | None = None, efforts: dict[str, str | None] | None = None, replace: bool = False)",
      "returns": "None",
      "doc": "Invoke the dep_capture LLM worker and write deps to .leerie/config.toml (non-fatal)."
    },
    {
      "name": "_backstop_capture_prior_runs",
      "qualname": "_backstop_capture_prior_runs",
      "async": true,
      "lineno": 7024,
      "end_lineno": 7059,
      "span": 36,
      "decorators": [],
      "signature": "(leerie_root: Path, repo_root: Path, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "Cover SIGKILL/crash: run dep_capture over prior runs with logs/ but no sentinel."
    },
    {
      "name": "run_recapture_deps",
      "qualname": "run_recapture_deps",
      "async": false,
      "lineno": 7062,
      "end_lineno": 7144,
      "span": 83,
      "decorators": [],
      "signature": "(leerie_root: Path, repo_root: Path, force: bool = False, run_id: str | None = None)",
      "returns": "None",
      "doc": "Host-side recapture entrypoint (DESIGN \u00a76\u00bd) \u2014 consolidates dep_capture across runs."
    },
    {
      "name": "_split_readme_headers",
      "qualname": "_split_readme_headers",
      "async": false,
      "lineno": 7147,
      "end_lineno": 7196,
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
      "lineno": 7199,
      "end_lineno": 7206,
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
      "lineno": 7209,
      "end_lineno": 7248,
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
      "lineno": 7260,
      "end_lineno": 7317,
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
      "lineno": 7320,
      "end_lineno": 7327,
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
      "lineno": 7340,
      "end_lineno": 7377,
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
      "lineno": 7380,
      "end_lineno": 7488,
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
      "lineno": 7521,
      "end_lineno": 7537,
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
      "lineno": 7540,
      "end_lineno": 7589,
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
      "lineno": 7592,
      "end_lineno": 7604,
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
      "lineno": 7607,
      "end_lineno": 7621,
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
      "lineno": 7624,
      "end_lineno": 7644,
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
      "lineno": 7649,
      "end_lineno": 7717,
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
      "lineno": 7722,
      "end_lineno": 7765,
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
      "lineno": 7770,
      "end_lineno": 7793,
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
      "lineno": 7796,
      "end_lineno": 7809,
      "span": 14,
      "decorators": [],
      "signature": "(staging: Path)",
      "returns": "str | None",
      "doc": "Return an error if the integrator's merge commit touched .leerie/ files."
    },
    {
      "name": "branch_has_commits_ahead",
      "qualname": "branch_has_commits_ahead",
      "async": true,
      "lineno": 7814,
      "end_lineno": 7837,
      "span": 24,
      "decorators": [],
      "signature": "(worktree: str, parent_branch: str)",
      "returns": "bool",
      "doc": "True iff the subtask branch has \u22651 commit ahead of `parent_branch`."
    },
    {
      "name": "check_branch_has_commits",
      "qualname": "check_branch_has_commits",
      "async": true,
      "lineno": 7840,
      "end_lineno": 7872,
      "span": 33,
      "decorators": [],
      "signature": "(sid: str, worktree: str, parent_branch: str)",
      "returns": "tuple[str, str] | None",
      "doc": "Return `(failure_kind, message)` if the implementer's subtask"
    },
    {
      "name": "scan_conflict_markers",
      "qualname": "scan_conflict_markers",
      "async": true,
      "lineno": 7877,
      "end_lineno": 7895,
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
      "lineno": 7900,
      "end_lineno": 7925,
      "span": 26,
      "decorators": [],
      "signature": "(data: dict)",
      "returns": "None",
      "doc": "Assert the structure of a loaded state.json before resuming. A corrupt"
    },
    {
      "name": "_api_error_category",
      "qualname": "_api_error_category",
      "async": false,
      "lineno": 7935,
      "end_lineno": 7947,
      "span": 13,
      "decorators": [],
      "signature": "(status: int | None)",
      "returns": "str | None",
      "doc": "Map a `claude -p` `api_error_status` to a coarse failure category."
    },
    {
      "name": "_is_auth_or_quota_failure",
      "qualname": "_is_auth_or_quota_failure",
      "async": false,
      "lineno": 7950,
      "end_lineno": 7980,
      "span": 31,
      "decorators": [],
      "signature": "(envelope: dict)",
      "returns": "bool",
      "doc": "True if the `claude -p` envelope looks like a 401/429/529/"
    },
    {
      "name": "_classify_failure_kind",
      "qualname": "_classify_failure_kind",
      "async": false,
      "lineno": 7983,
      "end_lineno": 8024,
      "span": 42,
      "decorators": [],
      "signature": "(envelope: dict, parsed_ok: bool)",
      "returns": "str | None",
      "doc": "Categorize *why* a captured `claude -p` call failed, for the"
    },
    {
      "name": "_extract_tool_result_text",
      "qualname": "_extract_tool_result_text",
      "async": false,
      "lineno": 8027,
      "end_lineno": 8040,
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
      "lineno": 8058,
      "end_lineno": 8062,
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
      "lineno": 8065,
      "end_lineno": 8088,
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
      "lineno": 8091,
      "end_lineno": 8126,
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
      "lineno": 8129,
      "end_lineno": 8168,
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
      "lineno": 8171,
      "end_lineno": 8335,
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
      "lineno": 8338,
      "end_lineno": 8361,
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
      "lineno": 8364,
      "end_lineno": 8407,
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
      "lineno": 8449,
      "end_lineno": 8457,
      "span": 9,
      "decorators": [],
      "signature": "(payload: str, timeout: float = 5.0)",
      "returns": "str",
      "doc": "Send one request to the cgroup broker and return its response line"
    },
    {
      "name": "_cgroup_probe",
      "qualname": "_cgroup_probe",
      "async": false,
      "lineno": 8460,
      "end_lineno": 8489,
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
      "lineno": 8492,
      "end_lineno": 8511,
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
      "lineno": 8514,
      "end_lineno": 8527,
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
      "lineno": 8530,
      "end_lineno": 8537,
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
      "lineno": 8540,
      "end_lineno": 8568,
      "span": 29,
      "decorators": [],
      "signature": "(sid: str | None)",
      "returns": "tuple[int, int, int, int] | None",
      "doc": "Read-only probe of a worker cgroup's PID + memory-OOM counters via"
    },
    {
      "name": "enforce_and_record_cgroup_containment",
      "qualname": "enforce_and_record_cgroup_containment",
      "async": false,
      "lineno": 8571,
      "end_lineno": 8622,
      "span": 52,
      "decorators": [],
      "signature": "(st: 'State', allow_uncapped: bool)",
      "returns": "None",
      "doc": "Fail-closed containment gate + state recording, run once per run"
    },
    {
      "name": "_invoke",
      "qualname": "_invoke",
      "async": true,
      "lineno": 8625,
      "end_lineno": 9145,
      "span": 521,
      "decorators": [],
      "signature": "(cmd: list[str], cwd: str, timeout: int, sid: str, leerie_dir: Path, verbosity: str, progress: Callable[[], tuple[int, int, int, int] | None] | None = None, idle_warn_sec: float | None = None, worker_memory_max_bytes: int | None = None, worker_pids_max: int | None = None)",
      "returns": "dict",
      "doc": "Run a `claude -p` command, streaming events as they arrive."
    },
    {
      "name": "_capture_call",
      "qualname": "_capture_call",
      "async": false,
      "lineno": 9148,
      "end_lineno": 9158,
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
      "lineno": 9161,
      "end_lineno": 9210,
      "span": 50,
      "decorators": [],
      "signature": "(st: 'State')",
      "returns": "dict",
      "doc": "Snapshot orchestrator RSS / current phase / worker count / open FDs /"
    },
    {
      "name": "_restore_sigchld_default",
      "qualname": "_restore_sigchld_default",
      "async": false,
      "lineno": 9232,
      "end_lineno": 9256,
      "span": 25,
      "decorators": [],
      "signature": "()",
      "returns": "None",
      "doc": "Force SIGCHLD to SIG_DFL before this process spawns anything."
    },
    {
      "name": "_sigchld_is_ignored",
      "qualname": "_sigchld_is_ignored",
      "async": false,
      "lineno": 9259,
      "end_lineno": 9276,
      "span": 18,
      "decorators": [],
      "signature": "()",
      "returns": "bool | None",
      "doc": "True if the kernel is auto-reaping our children (SIGCHLD ignored)."
    },
    {
      "name": "_become_subreaper",
      "qualname": "_become_subreaper",
      "async": false,
      "lineno": 9279,
      "end_lineno": 9312,
      "span": 34,
      "decorators": [],
      "signature": "()",
      "returns": "bool",
      "doc": "Install this process as a child-subreaper so orphaned descendants"
    },
    {
      "name": "_orphan_zombie_children",
      "qualname": "_orphan_zombie_children",
      "async": false,
      "lineno": 9315,
      "end_lineno": 9352,
      "span": 38,
      "decorators": [],
      "signature": "()",
      "returns": "list[int]",
      "doc": "Return PIDs of processes that are (a) zombies (`<defunct>`, state `Z`),"
    },
    {
      "name": "_zombie_reaper",
      "qualname": "_zombie_reaper",
      "async": true,
      "lineno": 9355,
      "end_lineno": 9382,
      "span": 28,
      "decorators": [],
      "signature": "(interval_sec: float = 1.0)",
      "returns": "None",
      "doc": "Periodically `wait()` orphaned descendants that reparented to this"
    },
    {
      "name": "_memory_sampler",
      "qualname": "_memory_sampler",
      "async": true,
      "lineno": 9385,
      "end_lineno": 9427,
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
      "lineno": 9430,
      "end_lineno": 9705,
      "span": 276,
      "decorators": [],
      "signature": "(user_prompt: str, system_prompt: str, *, schema_key: str, cwd: str, allowed_tools: str, max_turns: int, autonomous: bool, caps: dict, st: 'State', model: str, sid: str, add_dirs: list[str] | None = None, effort: str | None = None, _suppress_capture: bool = False)",
      "returns": "dict",
      "doc": "Run one headless Claude Code worker and return its validated"
    },
    {
      "name": "replay_capture",
      "qualname": "replay_capture",
      "async": true,
      "lineno": 9708,
      "end_lineno": 9772,
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
      "lineno": 9775,
      "end_lineno": 9785,
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
      "lineno": 9973,
      "end_lineno": 10021,
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
      "lineno": 10024,
      "end_lineno": 10092,
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
      "lineno": 10147,
      "end_lineno": 10226,
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
      "lineno": 10229,
      "end_lineno": 10252,
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
      "lineno": 10255,
      "end_lineno": 10354,
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
      "lineno": 10357,
      "end_lineno": 10417,
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
      "lineno": 10420,
      "end_lineno": 10472,
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
      "lineno": 10475,
      "end_lineno": 10562,
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
      "lineno": 10565,
      "end_lineno": 10650,
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
      "lineno": 10666,
      "end_lineno": 10735,
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
      "lineno": 10743,
      "end_lineno": 10755,
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
      "lineno": 10758,
      "end_lineno": 10790,
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
      "lineno": 10827,
      "end_lineno": 10849,
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
      "lineno": 10852,
      "end_lineno": 10912,
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
      "lineno": 10915,
      "end_lineno": 11041,
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
      "lineno": 11059,
      "end_lineno": 11070,
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
      "lineno": 11073,
      "end_lineno": 11160,
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
      "lineno": 11166,
      "end_lineno": 11219,
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
      "lineno": 11222,
      "end_lineno": 11267,
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
      "lineno": 11270,
      "end_lineno": 11332,
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
      "lineno": 11335,
      "end_lineno": 11380,
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
      "lineno": 11383,
      "end_lineno": 11411,
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
      "lineno": 11414,
      "end_lineno": 11572,
      "span": 159,
      "decorators": [],
      "signature": "(repo_root: Path, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "Phase 1\u00bd: per-repo dependency *detection*."
    },
    {
      "name": "_select_best_planner_sample",
      "qualname": "_select_best_planner_sample",
      "async": false,
      "lineno": 11575,
      "end_lineno": 11597,
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
      "lineno": 11600,
      "end_lineno": 11781,
      "span": 182,
      "decorators": [],
      "signature": "(task: str, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "list[dict]",
      "doc": "Phase 2: one planner per category, run in parallel (bounded by"
    },
    {
      "name": "_promote_external_collisions",
      "qualname": "_promote_external_collisions",
      "async": false,
      "lineno": 11784,
      "end_lineno": 11808,
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
      "lineno": 11811,
      "end_lineno": 11841,
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
      "lineno": 11844,
      "end_lineno": 11883,
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
      "lineno": 11886,
      "end_lineno": 11941,
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
      "lineno": 11944,
      "end_lineno": 11963,
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
      "lineno": 11966,
      "end_lineno": 12359,
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
      "lineno": 12362,
      "end_lineno": 12957,
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
      "lineno": 12960,
      "end_lineno": 13028,
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
      "lineno": 13031,
      "end_lineno": 13094,
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
      "lineno": 13097,
      "end_lineno": 13137,
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
      "lineno": 13140,
      "end_lineno": 13155,
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
      "lineno": 13158,
      "end_lineno": 13188,
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
      "lineno": 13191,
      "end_lineno": 13386,
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
      "lineno": 13389,
      "end_lineno": 13409,
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
      "lineno": 13412,
      "end_lineno": 13449,
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
      "lineno": 13452,
      "end_lineno": 13540,
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
      "lineno": 13543,
      "end_lineno": 13611,
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
      "lineno": 13614,
      "end_lineno": 13628,
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
      "lineno": 13631,
      "end_lineno": 13673,
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
      "lineno": 13676,
      "end_lineno": 13693,
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
      "lineno": 13696,
      "end_lineno": 13793,
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
      "lineno": 13796,
      "end_lineno": 13949,
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
      "lineno": 13952,
      "end_lineno": 14084,
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
      "lineno": 14114,
      "end_lineno": 14136,
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
      "lineno": 14139,
      "end_lineno": 14236,
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
      "lineno": 14239,
      "end_lineno": 14345,
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
      "lineno": 14348,
      "end_lineno": 14540,
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
      "lineno": 14543,
      "end_lineno": 14573,
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
      "lineno": 14576,
      "end_lineno": 14794,
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
      "lineno": 14797,
      "end_lineno": 15015,
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
      "lineno": 15018,
      "end_lineno": 15069,
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
      "lineno": 15072,
      "end_lineno": 15100,
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
      "lineno": 15103,
      "end_lineno": 15146,
      "span": 44,
      "decorators": [],
      "signature": "(st: State, no_work_map: dict[str, str])",
      "returns": "None",
      "doc": "Terminal-state handler for the cleared-but-empty case"
    },
    {
      "name": "schedule",
      "qualname": "schedule",
      "async": false,
      "lineno": 15149,
      "end_lineno": 15198,
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
      "lineno": 15201,
      "end_lineno": 15262,
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
      "lineno": 15265,
      "end_lineno": 15286,
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
      "lineno": 15289,
      "end_lineno": 15309,
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
      "lineno": 15312,
      "end_lineno": 15341,
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
      "lineno": 15344,
      "end_lineno": 15371,
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
      "lineno": 15374,
      "end_lineno": 15399,
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
      "lineno": 15402,
      "end_lineno": 15455,
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
      "lineno": 15458,
      "end_lineno": 15573,
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
      "lineno": 15603,
      "end_lineno": 15617,
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
      "lineno": 15650,
      "end_lineno": 15662,
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
      "lineno": 15665,
      "end_lineno": 15674,
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
      "lineno": 15677,
      "end_lineno": 15689,
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
      "lineno": 15692,
      "end_lineno": 15697,
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
      "lineno": 15700,
      "end_lineno": 15779,
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
      "lineno": 15782,
      "end_lineno": 15800,
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
      "lineno": 15803,
      "end_lineno": 15832,
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
      "lineno": 15835,
      "end_lineno": 15888,
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
      "lineno": 15891,
      "end_lineno": 15897,
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
      "lineno": 15900,
      "end_lineno": 15921,
      "span": 22,
      "decorators": [],
      "signature": "(worktree: str, before_sha: str)",
      "returns": "list[str]",
      "doc": "Return the list of protected paths the diff `before_sha..HEAD`"
    },
    {
      "name": "_blob_sha",
      "qualname": "_blob_sha",
      "async": true,
      "lineno": 15924,
      "end_lineno": 15938,
      "span": 15,
      "decorators": [],
      "signature": "(worktree: str, ref: str, path: str)",
      "returns": "str | None",
      "doc": "Blob SHA of `path` at `ref` in `worktree`, or None if the path is"
    },
    {
      "name": "clobbered_owned_files",
      "qualname": "clobbered_owned_files",
      "async": true,
      "lineno": 15941,
      "end_lineno": 15978,
      "span": 38,
      "decorators": [],
      "signature": "(worktree: str, base_ref: str, impl_head: str)",
      "returns": "list[str]",
      "doc": "Return implementer-owned files the conformer reverted-to-base or"
    },
    {
      "name": "rollback_conformer_commits",
      "qualname": "rollback_conformer_commits",
      "async": true,
      "lineno": 15981,
      "end_lineno": 15992,
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
      "lineno": 15995,
      "end_lineno": 16009,
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
      "lineno": 16012,
      "end_lineno": 16034,
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
      "lineno": 16037,
      "end_lineno": 16103,
      "span": 67,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, worktree: str, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None], rules_files: list[Path], blt_commands: dict[str, str], diff_base: str, extra_feedback: str | None = None)",
      "returns": "dict | None",
      "doc": "Spawn one conformer for one subtask in its existing worktree."
    },
    {
      "name": "_summarize_residuals",
      "qualname": "_summarize_residuals",
      "async": false,
      "lineno": 16106,
      "end_lineno": 16119,
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
      "lineno": 16122,
      "end_lineno": 16133,
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
      "lineno": 16136,
      "end_lineno": 16156,
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
      "lineno": 16159,
      "end_lineno": 16212,
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
      "lineno": 16215,
      "end_lineno": 16225,
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
      "lineno": 16262,
      "end_lineno": 16316,
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
      "lineno": 16319,
      "end_lineno": 16330,
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
      "lineno": 16333,
      "end_lineno": 16394,
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
      "lineno": 16397,
      "end_lineno": 16422,
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
      "lineno": 16425,
      "end_lineno": 16580,
      "span": 156,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, worktree: str, subtask: dict, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "tuple[dict | None, list[str], str | None]",
      "doc": "Drive the orchestrator-level conformer loop for one subtask."
    },
    {
      "name": "_runner_missing",
      "qualname": "_runner_missing",
      "async": false,
      "lineno": 16583,
      "end_lineno": 16590,
      "span": 8,
      "decorators": [],
      "signature": "(summary: str)",
      "returns": "bool",
      "doc": "True if a failed baseline command failed because its runner is not"
    },
    {
      "name": "_format_baseline_section",
      "qualname": "_format_baseline_section",
      "async": false,
      "lineno": 16593,
      "end_lineno": 16666,
      "span": 74,
      "decorators": [],
      "signature": "(baseline: dict | None)",
      "returns": "str | None",
      "doc": "Render the base-tree health baseline as a conformer prompt section,"
    },
    {
      "name": "capture_conformance_baseline",
      "qualname": "capture_conformance_baseline",
      "async": true,
      "lineno": 16669,
      "end_lineno": 16809,
      "span": 141,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, caps: dict)",
      "returns": "None",
      "doc": "Record base-tree build/lint/test health once per run (DESIGN \u00a79"
    },
    {
      "name": "run_final_conformance",
      "qualname": "run_final_conformance",
      "async": true,
      "lineno": 16812,
      "end_lineno": 17041,
      "span": 230,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "Whole-tree conformance pass on the integrated staging worktree"
    },
    {
      "name": "settle_subtask",
      "qualname": "settle_subtask",
      "async": true,
      "lineno": 17044,
      "end_lineno": 17439,
      "span": 396,
      "decorators": [],
      "signature": "(sid: str, leerie_dir: Path, caps: dict, st: State, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "dict",
      "doc": "Drive one subtask to a terminal state."
    },
    {
      "name": "integrate_wave",
      "qualname": "integrate_wave",
      "async": true,
      "lineno": 17442,
      "end_lineno": 17555,
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
      "lineno": 17558,
      "end_lineno": 17665,
      "span": 108,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "Phases 4-5: create staging, then run waves sequentially; within a wave,"
    },
    {
      "name": "find_pr_template",
      "qualname": "find_pr_template",
      "async": false,
      "lineno": 17702,
      "end_lineno": 17747,
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
      "lineno": 17767,
      "end_lineno": 17786,
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
      "lineno": 17795,
      "end_lineno": 17805,
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
      "lineno": 17808,
      "end_lineno": 17822,
      "span": 15,
      "decorators": [],
      "signature": "(diff_text: str, max_lines: int)",
      "returns": "tuple[str, bool]",
      "doc": "Return (truncated_text, was_truncated). Splits on newlines and"
    },
    {
      "name": "_base_health_payload",
      "qualname": "_base_health_payload",
      "async": false,
      "lineno": 17825,
      "end_lineno": 17855,
      "span": 31,
      "decorators": [],
      "signature": "(st: 'State')",
      "returns": "dict | None",
      "doc": "Compact view of the base-tree health baseline (DESIGN \u00a79) for the"
    },
    {
      "name": "_record_run_health",
      "qualname": "_record_run_health",
      "async": false,
      "lineno": 17858,
      "end_lineno": 17922,
      "span": 65,
      "decorators": [],
      "signature": "(st: 'State')",
      "returns": "None",
      "doc": "Compute and persist run-health signals into `run.json.health`"
    },
    {
      "name": "_final_conformance_payload",
      "qualname": "_final_conformance_payload",
      "async": false,
      "lineno": 17925,
      "end_lineno": 17981,
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
      "lineno": 17984,
      "end_lineno": 18170,
      "span": 187,
      "decorators": [],
      "signature": "(st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None], repo_root: Path, pr_template_override: str | None)",
      "returns": "None",
      "doc": "Run the pr_writer worker and persist its title/body to run.json."
    },
    {
      "name": "phase_finalize",
      "qualname": "phase_finalize",
      "async": true,
      "lineno": 18173,
      "end_lineno": 18311,
      "span": 139,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, no_push: bool, no_verify: bool, caps: dict | None = None, models: dict[str, str] | None = None, efforts: dict[str, str | None] | None = None, pr_template_override: str | None = None, host_no_push: bool | None = None)",
      "returns": "None",
      "doc": "Phase 6: verify the run branch and record finalize state."
    },
    {
      "name": "orchestrate",
      "qualname": "orchestrate",
      "async": true,
      "lineno": 18317,
      "end_lineno": 18344,
      "span": 28,
      "decorators": [],
      "signature": "(args, caps: dict, leerie_dir: Path, st: State, sot_pref: str, verbosity: str, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "The async portion of a run: every phase that spawns a `claude -p`"
    },
    {
      "name": "_run_phases",
      "qualname": "_run_phases",
      "async": true,
      "lineno": 18347,
      "end_lineno": 18609,
      "span": 263,
      "decorators": [],
      "signature": "(args, caps: dict, leerie_dir: Path, st: State, sot_pref: str, verbosity: str, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "The phase sequence of one run. Split out from `orchestrate()`"
    },
    {
      "name": "main",
      "qualname": "main",
      "async": false,
      "lineno": 18612,
      "end_lineno": 19487,
      "span": 876,
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
      "lineno": 1636,
      "end_lineno": 1648,
      "span": 13,
      "doc": "Raised by signal handlers (SIGTERM, SIGHUP) installed in main().",
      "methods": []
    },
    {
      "name": "RateLimitedExit",
      "bases": [
        "BaseException"
      ],
      "lineno": 1651,
      "end_lineno": 1687,
      "span": 37,
      "doc": "Raised when claude -p reports the Claude Code subscription",
      "methods": [
        {
          "name": "__init__",
          "qualname": "RateLimitedExit.__init__",
          "async": false,
          "lineno": 1682,
          "end_lineno": 1687,
          "span": 6,
          "decorators": [],
          "signature": "(self, reset_at: datetime | None, raw_message: str, out_of_credits: bool = False)",
          "returns": null,
          "doc": null
        }
      ]
    },
    {
      "name": "_DescendantTracker",
      "bases": [],
      "lineno": 1901,
      "end_lineno": 2013,
      "span": 113,
      "doc": "Background poller that accumulates every PID ever observed as a",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_DescendantTracker.__init__",
          "async": false,
          "lineno": 1930,
          "end_lineno": 1936,
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
          "lineno": 1938,
          "end_lineno": 1941,
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
          "lineno": 1943,
          "end_lineno": 1990,
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
          "lineno": 1992,
          "end_lineno": 2013,
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
      "lineno": 7931,
      "end_lineno": 7932,
      "span": 2,
      "doc": null,
      "methods": []
    },
    {
      "name": "_ReplayState",
      "bases": [],
      "lineno": 9788,
      "end_lineno": 9816,
      "span": 29,
      "doc": "Minimal State-alike for replay_capture: no persistent writes.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_ReplayState.__init__",
          "async": false,
          "lineno": 9797,
          "end_lineno": 9806,
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
          "lineno": 9808,
          "end_lineno": 9809,
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
          "lineno": 9811,
          "end_lineno": 9812,
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
          "lineno": 9814,
          "end_lineno": 9816,
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
      "lineno": 9819,
      "end_lineno": 9831,
      "span": 13,
      "doc": "Raised when State.__init__ cannot acquire the per-run-directory",
      "methods": [
        {
          "name": "__init__",
          "qualname": "StateLockedError.__init__",
          "async": false,
          "lineno": 9829,
          "end_lineno": 9831,
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
      "lineno": 9837,
      "end_lineno": 9966,
      "span": 130,
      "doc": "In-memory run state with atomic on-disk persistence.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "State.__init__",
          "async": false,
          "lineno": 9866,
          "end_lineno": 9886,
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
          "lineno": 9888,
          "end_lineno": 9902,
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
          "lineno": 9904,
          "end_lineno": 9919,
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
          "lineno": 9921,
          "end_lineno": 9932,
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
          "lineno": 9934,
          "end_lineno": 9938,
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
          "lineno": 9940,
          "end_lineno": 9949,
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
          "lineno": 9951,
          "end_lineno": 9959,
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
          "lineno": 9961,
          "end_lineno": 9966,
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
      "lineno": 10099,
      "end_lineno": 10144,
      "span": 46,
      "doc": "Persistent state for one heal-loop run scoped to a single call_type.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "HealState.__init__",
          "async": false,
          "lineno": 10112,
          "end_lineno": 10120,
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
          "lineno": 10122,
          "end_lineno": 10133,
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
          "lineno": 10135,
          "end_lineno": 10144,
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
      "log": 56,
      "die": 35,
      "run_proc": 21,
      "claude_p": 18,
      "load_prompt": 17,
      "_read_toml_key": 12,
      "now": 11,
      "_resolve_bool_pref": 10,
      "compute_run_branch": 9,
      "_resolve_positive_int_pref": 9,
      "_write_run_json": 7,
      "_confidence_issues": 6,
      "_build_predecessor_graph": 6,
      "gather_or_cancel": 6,
      "_run_checked_loop": 6,
      "_cgroup_request": 5,
      "is_protected_path": 4,
      "capture_repo_deps": 4,
      "run_script": 4,
      "_format_check_feedback": 4,
      "_enumerate_descendants": 3,
      "_signal_pids": 3,
      "_format_run_duration": 3,
      "_derive_run_status": 3,
      "_resolve_enum_pref": 3,
      "_resolve_str_pref": 3,
      "_terminate_proc_tree": 3,
      "_iter_log_tool_use": 3,
      "judge_capture": 3,
      "run_streaming": 3,
      "_tarjan_sccs": 3,
      "_attribute_cycle_edges": 3,
      "_shared_files_in_scc": 3,
      "_format_provision_recipe_section": 3,
      "discover_rules_files": 3,
      "_format_rules_paths": 3,
      "resolve_blt": 3,
      "_cgroup_stat": 2,
      "_cleanup_on_abnormal_exit": 2,
      "_validate_run_json": 2
    },
    "fan_out_top": {
      "main": 49,
      "_run_phases": 33,
      "phase_reconcile": 22,
      "run_final_conformance": 21,
      "settle_subtask": 18,
      "_run_conformance_phase": 15,
      "phase_provision": 14,
      "phase_plan": 14,
      "_compose_pr_via_llm": 12,
      "capture_repo_deps": 11,
      "phase_overlap_judge": 11,
      "integrate_wave": 11,
      "phase_finalize": 11,
      "_invoke": 10,
      "phase_execute": 9,
      "recursive_decompose": 7,
      "claude_p": 7,
      "phase_heal": 7,
      "preflight": 6,
      "phase_classify": 6,
      "run_implementer": 6,
      "run_conformer": 6,
      "report_run": 5,
      "_label_migration_chunks": 5,
      "run_recapture_deps": 5,
      "_summarize_stream_event": 5,
      "_build_cycle_retry_prompt": 5,
      "_apply_overlap_collisions": 5,
      "capture_conformance_baseline": 5,
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
      "resolve_worker_memory_max": 3
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
      "_sleep_then_reexec": [
        "_cleanup_on_abnormal_exit",
        "log"
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
      "_aggregate_calls": [],
      "_memory_peak": [],
      "report_run": [
        "resolve_run_id",
        "_derive_run_status",
        "_format_run_duration",
        "_aggregate_calls",
        "_memory_peak"
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
      "resolve_worker_pids_max": [
        "_resolve_positive_int_pref"
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
      "resolve_skip_base_baseline": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_repo_map": [
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
        "_sigchld_is_ignored",
        "die",
        "run_proc",
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
      "partition_files": [],
      "_migration_child": [],
      "_deterministic_chunk_label": [],
      "_label_migration_chunks": [
        "_deterministic_chunk_label",
        "load_prompt",
        "claude_p",
        "log",
        "_migration_child"
      ],
      "recursive_decompose": [
        "rank_repo_map",
        "load_prompt",
        "claude_p",
        "log",
        "partition_files",
        "_label_migration_chunks",
        "recursive_decompose"
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
      "_repo_map_cache_key": [],
      "_walk_calls": [],
      "_parse_repo_file": [
        "_walk_calls"
      ],
      "_tree_sitter_extraction_works": [
        "_parse_repo_file"
      ],
      "_warn_repo_map_empty_once": [
        "_tree_sitter_extraction_works",
        "log"
      ],
      "build_repo_map": [
        "_parse_repo_file",
        "_warn_repo_map_empty_once"
      ],
      "_pagerank": [],
      "_render_repo_map_subgraph": [],
      "_count_tokens_approx": [],
      "rank_repo_map": [
        "_pagerank",
        "_render_repo_map_subgraph",
        "_count_tokens_approx"
      ],
      "validate_plan": [
        "is_protected_path",
        "die",
        "log"
      ],
      "warn_cross_planner_file_overlap": [
        "log"
      ],
      "warn_provider_subset_subtasks": [
        "_build_predecessor_graph",
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
      "probe_criteria_satisfied_on_head": [
        "claude_p",
        "load_prompt",
        "log"
      ],
      "_lockfile_table_entries": [],
      "detect_recipe_from_lockfiles": [
        "_lockfile_table_entries"
      ],
      "_normalize_pip_installs": [
        "_is_pip_install"
      ],
      "_is_pip_install": [],
      "validate_provision_recipe": [],
      "_is_install_command": [],
      "_gather_dep_manifests": [],
      "_normalize_setup_packages": [],
      "_merge_setup_packages": [
        "_normalize_setup_packages"
      ],
      "_dump_language_installs": [],
      "_toml_value": [],
      "_write_config_toml_keys": [
        "_toml_value"
      ],
      "_extract_depcap_commands": [
        "_iter_log_tool_use",
        "_is_install_command"
      ],
      "resolve_capture_deps": [
        "_parse_bool_envtoml",
        "_read_toml_key"
      ],
      "capture_repo_deps": [
        "resolve_capture_deps",
        "log",
        "_gather_dep_manifests",
        "_extract_depcap_commands",
        "load_prompt",
        "claude_p",
        "_normalize_setup_packages",
        "_read_toml_key",
        "_merge_setup_packages",
        "_dump_language_installs",
        "_write_config_toml_keys"
      ],
      "_backstop_capture_prior_runs": [
        "log",
        "capture_repo_deps"
      ],
      "run_recapture_deps": [
        "resolve_models",
        "resolve_efforts",
        "die",
        "log",
        "capture_repo_deps"
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
      "branch_has_commits_ahead": [
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
      "_api_error_category": [],
      "_is_auth_or_quota_failure": [
        "_api_error_category"
      ],
      "_classify_failure_kind": [
        "_api_error_category"
      ],
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
      "_restore_sigchld_default": [],
      "_sigchld_is_ignored": [],
      "_become_subreaper": [
        "log"
      ],
      "_orphan_zombie_children": [],
      "_zombie_reaper": [
        "_orphan_zombie_children"
      ],
      "_memory_sampler": [
        "_collect_memory_sample"
      ],
      "claude_p": [
        "_invoke",
        "_get_progress",
        "_classify_failure_kind",
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
        "validate_provision_recipe",
        "_normalize_pip_installs"
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
        "build_repo_map",
        "rank_repo_map",
        "claude_p",
        "check_planner_output",
        "check_task_file_coverage",
        "_run_checked_loop",
        "die",
        "gather_or_cancel",
        "_select_best_planner_sample",
        "recursive_decompose"
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
      "_blob_sha": [
        "run_proc"
      ],
      "clobbered_owned_files": [
        "run_proc",
        "_blob_sha"
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
        "_format_baseline_section",
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
        "clobbered_owned_files",
        "_unprefixed_conformer_commits",
        "_emit_bash_axis_warnings",
        "_format_check_feedback",
        "_conformance_clean",
        "_summarize_residuals"
      ],
      "_runner_missing": [],
      "_format_baseline_section": [],
      "capture_conformance_baseline": [
        "log",
        "resolve_blt",
        "run_streaming",
        "_runner_missing",
        "_write_run_json"
      ],
      "run_final_conformance": [
        "log",
        "discover_rules_files",
        "resolve_blt",
        "load_prompt",
        "_format_rules_paths",
        "compute_run_branch",
        "_branch_head_sha",
        "_format_baseline_section",
        "_format_provision_recipe_section",
        "claude_p",
        "validate_conformance_result",
        "_protected_paths_since",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "clobbered_owned_files",
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
        "branch_has_commits_ahead",
        "compute_run_branch",
        "_confidence_axes_clear",
        "run_proc",
        "check_implementer_output",
        "_format_check_feedback",
        "check_branch_has_commits",
        "probe_criteria_satisfied_on_head",
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
        "capture_conformance_baseline",
        "settle_subtask",
        "gather_or_cancel",
        "integrate_wave",
        "scan_conflict_markers"
      ],
      "find_pr_template": [],
      "_cap_text": [],
      "_strip_leerie_prefix": [],
      "_truncate_diff_sample": [],
      "_base_health_payload": [],
      "_record_run_health": [
        "_write_run_json"
      ],
      "_final_conformance_payload": [],
      "_compose_pr_via_llm": [
        "find_pr_template",
        "_cap_text",
        "log",
        "compute_run_branch",
        "_truncate_diff_sample",
        "_format_run_duration",
        "_final_conformance_payload",
        "_base_health_payload",
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
        "_record_run_health",
        "_compose_pr_via_llm",
        "compute_run_branch"
      ],
      "orchestrate": [
        "_memory_sampler",
        "_zombie_reaper",
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
        "_backstop_capture_prior_runs",
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
        "warn_provider_subset_subtasks",
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
        "_restore_sigchld_default",
        "_become_subreaper",
        "_read_version",
        "resolve_leerie_root",
        "list_runs",
        "report_run",
        "die",
        "resolve_max_workers",
        "resolve_max_parallel",
        "resolve_confidence_rounds",
        "resolve_judgment_check_rounds",
        "resolve_planner_check_rounds",
        "resolve_implementer_confidence_retries",
        "resolve_planner_samples",
        "resolve_worker_memory_max",
        "resolve_worker_pids_max",
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
        "resolve_skip_base_baseline",
        "resolve_skip_repo_map",
        "resolve_dangerously_allow_uncapped",
        "resolve_pr_template",
        "resolve_inspect_dirs",
        "resolve_judge_dir",
        "resolve_heal_dir",
        "resolve_heal_max_rounds",
        "resolve_heal_success_threshold",
        "phase_judge",
        "phase_heal",
        "_install_signal_handlers",
        "orchestrate",
        "_cleanup_on_abnormal_exit",
        "capture_repo_deps",
        "now",
        "_sleep_then_reexec",
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
      "_sleep_then_reexec": [
        "_cleanup_on_abnormal_exit",
        "log",
        "log",
        "log",
        "log",
        "log",
        "log"
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
      "_aggregate_calls": [],
      "_memory_peak": [],
      "report_run": [
        "resolve_run_id",
        "_derive_run_status",
        "_format_run_duration",
        "_aggregate_calls",
        "_memory_peak"
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
      "resolve_worker_pids_max": [
        "_resolve_positive_int_pref"
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
      "resolve_skip_base_baseline": [
        "_resolve_bool_pref"
      ],
      "resolve_skip_repo_map": [
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
        "_sigchld_is_ignored",
        "die",
        "run_proc",
        "die",
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
      "partition_files": [],
      "_migration_child": [],
      "_deterministic_chunk_label": [],
      "_label_migration_chunks": [
        "_deterministic_chunk_label",
        "load_prompt",
        "claude_p",
        "log",
        "_migration_child"
      ],
      "recursive_decompose": [
        "rank_repo_map",
        "load_prompt",
        "claude_p",
        "log",
        "log",
        "partition_files",
        "_label_migration_chunks",
        "load_prompt",
        "claude_p",
        "log",
        "recursive_decompose"
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
      "_repo_map_cache_key": [],
      "_walk_calls": [],
      "_parse_repo_file": [
        "_walk_calls"
      ],
      "_tree_sitter_extraction_works": [
        "_parse_repo_file"
      ],
      "_warn_repo_map_empty_once": [
        "_tree_sitter_extraction_works",
        "log"
      ],
      "build_repo_map": [
        "_parse_repo_file",
        "_warn_repo_map_empty_once"
      ],
      "_pagerank": [],
      "_render_repo_map_subgraph": [],
      "_count_tokens_approx": [],
      "rank_repo_map": [
        "_pagerank",
        "_render_repo_map_subgraph",
        "_count_tokens_approx"
      ],
      "validate_plan": [
        "is_protected_path",
        "die",
        "log"
      ],
      "warn_cross_planner_file_overlap": [
        "log",
        "log"
      ],
      "warn_provider_subset_subtasks": [
        "_build_predecessor_graph",
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
      "probe_criteria_satisfied_on_head": [
        "claude_p",
        "load_prompt",
        "log"
      ],
      "_lockfile_table_entries": [],
      "detect_recipe_from_lockfiles": [
        "_lockfile_table_entries"
      ],
      "_normalize_pip_installs": [
        "_is_pip_install"
      ],
      "_is_pip_install": [],
      "validate_provision_recipe": [],
      "_is_install_command": [],
      "_gather_dep_manifests": [],
      "_normalize_setup_packages": [],
      "_merge_setup_packages": [
        "_normalize_setup_packages"
      ],
      "_dump_language_installs": [],
      "_toml_value": [],
      "_write_config_toml_keys": [
        "_toml_value",
        "_toml_value"
      ],
      "_extract_depcap_commands": [
        "_iter_log_tool_use",
        "_is_install_command"
      ],
      "resolve_capture_deps": [
        "_parse_bool_envtoml",
        "_read_toml_key",
        "_parse_bool_envtoml"
      ],
      "capture_repo_deps": [
        "resolve_capture_deps",
        "log",
        "log",
        "_gather_dep_manifests",
        "_extract_depcap_commands",
        "log",
        "log",
        "load_prompt",
        "claude_p",
        "_normalize_setup_packages",
        "_read_toml_key",
        "_merge_setup_packages",
        "_dump_language_installs",
        "_read_toml_key",
        "_dump_language_installs",
        "_write_config_toml_keys",
        "log",
        "log",
        "log"
      ],
      "_backstop_capture_prior_runs": [
        "log",
        "capture_repo_deps",
        "log"
      ],
      "run_recapture_deps": [
        "resolve_models",
        "resolve_efforts",
        "die",
        "die",
        "die",
        "log",
        "log",
        "capture_repo_deps",
        "log",
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
      "branch_has_commits_ahead": [
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
      "_api_error_category": [],
      "_is_auth_or_quota_failure": [
        "_api_error_category"
      ],
      "_classify_failure_kind": [
        "_api_error_category"
      ],
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
        "_cgroup_stat",
        "_cgroup_destroy",
        "log"
      ],
      "_capture_call": [],
      "_collect_memory_sample": [
        "now"
      ],
      "_restore_sigchld_default": [],
      "_sigchld_is_ignored": [],
      "_become_subreaper": [
        "log",
        "log"
      ],
      "_orphan_zombie_children": [],
      "_zombie_reaper": [
        "_orphan_zombie_children"
      ],
      "_memory_sampler": [
        "_collect_memory_sample",
        "_collect_memory_sample"
      ],
      "claude_p": [
        "_invoke",
        "_get_progress",
        "_classify_failure_kind",
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
        "_normalize_pip_installs",
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
        "build_repo_map",
        "rank_repo_map",
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
        "recursive_decompose",
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
      "_blob_sha": [
        "run_proc"
      ],
      "clobbered_owned_files": [
        "run_proc",
        "_blob_sha",
        "_blob_sha",
        "_blob_sha"
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
        "_format_baseline_section",
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
        "_branch_head_sha",
        "run_conformer",
        "validate_conformance_result",
        "check_diff_scope",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "_uncommitted_paths",
        "clobbered_owned_files",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "_unprefixed_conformer_commits",
        "_emit_bash_axis_warnings",
        "_format_check_feedback",
        "_conformance_clean",
        "_summarize_residuals",
        "_conformance_clean",
        "_summarize_residuals"
      ],
      "_runner_missing": [],
      "_format_baseline_section": [],
      "capture_conformance_baseline": [
        "log",
        "log",
        "resolve_blt",
        "log",
        "log",
        "run_streaming",
        "log",
        "log",
        "run_streaming",
        "_runner_missing",
        "log",
        "_write_run_json",
        "log",
        "_write_run_json"
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
        "compute_run_branch",
        "_branch_head_sha",
        "_branch_head_sha",
        "_format_baseline_section",
        "_format_provision_recipe_section",
        "claude_p",
        "validate_conformance_result",
        "_protected_paths_since",
        "_uncommitted_paths",
        "rollback_conformer_commits",
        "_uncommitted_paths",
        "clobbered_owned_files",
        "_uncommitted_paths",
        "rollback_conformer_commits",
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
        "branch_has_commits_ahead",
        "compute_run_branch",
        "log",
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
        "probe_criteria_satisfied_on_head",
        "log",
        "log",
        "run_proc",
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
        "capture_conformance_baseline",
        "log",
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
      "_base_health_payload": [],
      "_record_run_health": [
        "_write_run_json"
      ],
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
        "_base_health_payload",
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
        "_record_run_health",
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
        "_zombie_reaper",
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
        "_backstop_capture_prior_runs",
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
        "warn_provider_subset_subtasks",
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
        "_restore_sigchld_default",
        "_become_subreaper",
        "_read_version",
        "resolve_leerie_root",
        "list_runs",
        "resolve_leerie_root",
        "report_run",
        "die",
        "resolve_max_workers",
        "resolve_max_parallel",
        "resolve_confidence_rounds",
        "resolve_judgment_check_rounds",
        "resolve_planner_check_rounds",
        "resolve_implementer_confidence_retries",
        "resolve_planner_samples",
        "resolve_worker_memory_max",
        "resolve_worker_pids_max",
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
        "resolve_skip_base_baseline",
        "resolve_skip_repo_map",
        "resolve_dangerously_allow_uncapped",
        "resolve_pr_template",
        "resolve_inspect_dirs",
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
        "log",
        "_cleanup_on_abnormal_exit",
        "log",
        "capture_repo_deps",
        "log",
        "log",
        "now",
        "_sleep_then_reexec",
        "log",
        "capture_repo_deps",
        "log",
        "log",
        "capture_repo_deps",
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
