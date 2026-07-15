{
  "preprocess": {
    "path": "orchestrator/leerie.py",
    "sha256": "c8e059c09708ecb25b6b44eed99bf27c500129a709b1e234e2aec6ab1c39158c",
    "lines": 19611,
    "bytes": 925649,
    "module_doc": "Leerie \u2014 deterministic task orchestrator for Claude Code."
  },
  "lex": {
    "total_tokens": 105052,
    "token_class_counts": {
      "OP": 38027,
      "NAME": 30702,
      "NL": 8975,
      "NEWLINE": 7491,
      "STRING": 6371,
      "COMMENT": 3602,
      "INDENT": 2422,
      "DEDENT": 2422,
      "FSTRING_MIDDLE": 1980,
      "FSTRING_START": 1149,
      "FSTRING_END": 1149,
      "NUMBER": 761,
      "ENDMARKER": 1
    },
    "keyword_counts": {
      "if": 1334,
      "in": 861,
      "None": 742,
      "return": 656,
      "for": 643,
      "not": 557,
      "or": 490,
      "def": 373,
      "and": 225,
      "is": 195,
      "await": 189,
      "except": 182,
      "try": 168,
      "continue": 165,
      "else": 165,
      "True": 162,
      "False": 130,
      "async": 94,
      "as": 67,
      "raise": 58,
      "break": 44,
      "pass": 41,
      "elif": 39,
      "import": 34,
      "with": 24
    },
    "top_identifiers": {
      "get": 992,
      "str": 972,
      "dict": 522,
      "st": 479,
      "sid": 358,
      "list": 347,
      "s": 315,
      "append": 313,
      "repo_root": 311,
      "r": 306,
      "log": 229,
      "data": 222,
      "len": 209,
      "Path": 206,
      "caps": 203,
      "e": 199,
      "int": 184,
      "out": 151,
      "set": 134,
      "plans": 134,
      "args": 130,
      "plan": 127,
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
      "c": 87,
      "a": 82,
      "output": 81,
      "run_id": 78,
      "leerie_dir": 76,
      "name": 75,
      "ValueError": 75,
      "lines": 74,
      "is_file": 73,
      "json": 72,
      "text": 72,
      "run_dir": 70,
      "tag": 70,
      "tuple": 69,
      "subtasks": 69,
      "OSError": 68,
      "v": 68,
      "n": 68,
      "cli_value": 68,
      "i": 68,
      "save": 68,
      "cwd": 65,
      "asyncio": 63,
      "os": 63,
      "w": 62,
      "cmd": 62
    },
    "top_operators": {
      "(": 5600,
      ")": 5600,
      ",": 5026,
      ":": 4710,
      "=": 3467,
      ".": 3415,
      "[": 2268,
      "]": 2268,
      "{": 1997,
      "}": 1997,
      "->": 363,
      "/": 248,
      "|": 204,
      "==": 191,
      "!": 131,
      "+": 128,
      "*": 70,
      "!=": 66,
      "-": 53,
      "+=": 52,
      ">": 50,
      "<": 42,
      ">=": 27,
      "<=": 21,
      "**": 12
    },
    "comment_chars": 211300,
    "string_literal_count": 6371,
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
      "Load": 19890,
      "Name": 17244,
      "Constant": 9138,
      "Call": 4646,
      "Store": 3611,
      "Attribute": 3414,
      "Assign": 2269,
      "Subscript": 1512,
      "Expr": 1470,
      "FormattedValue": 1351,
      "If": 1165,
      "arg": 870,
      "keyword": 833,
      "Compare": 805,
      "JoinedStr": 785,
      "Return": 656,
      "Tuple": 655,
      "List": 655,
      "BinOp": 654,
      "BoolOp": 636,
      "Dict": 584,
      "Or": 435,
      "For": 413,
      "arguments": 394,
      "UnaryOp": 359,
      "Not": 327,
      "FunctionDef": 288,
      "AnnAssign": 267,
      "Div": 248,
      "comprehension": 227,
      "BitOr": 205,
      "And": 201,
      "Eq": 191,
      "Await": 189,
      "ExceptHandler": 182,
      "Add": 180,
      "Try": 168,
      "Continue": 165,
      "IsNot": 136,
      "In": 124,
      "IfExp": 103,
      "ListComp": 101,
      "NotIn": 94,
      "AsyncFunctionDef": 85,
      "GeneratorExp": 81,
      "NotEq": 66,
      "Slice": 61,
      "Is": 59,
      "Raise": 58,
      "AugAssign": 57,
      "Gt": 50,
      "alias": 44,
      "Break": 44,
      "Lt": 42,
      "Pass": 41,
      "Mult": 33,
      "USub": 32,
      "GtE": 27,
      "Import": 26,
      "DictComp": 24,
      "withitem": 24,
      "Starred": 23,
      "Sub": 23,
      "Lambda": 21,
      "LtE": 21,
      "SetComp": 20,
      "While": 18,
      "Set": 18,
      "With": 18,
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
      "errno",
      "fcntl",
      "json",
      "os",
      "re",
      "shutil",
      "signal",
      "socket",
      "stat",
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
    "constant_count": 176,
    "function_count": 303,
    "class_count": 8
  },
  "constants": [
    {
      "name": "ROOT",
      "lineno": 55,
      "kind": "expr",
      "expr": "Path(__file__).resolve().parent.parent"
    },
    {
      "name": "PROMPTS",
      "lineno": 56,
      "kind": "expr",
      "expr": "ROOT / 'prompts'"
    },
    {
      "name": "SCRIPTS",
      "lineno": 57,
      "kind": "expr",
      "expr": "ROOT / 'scripts'"
    },
    {
      "name": "_PROMPT_INCLUDE_RE",
      "lineno": 75,
      "kind": "expr",
      "expr": "re.compile('\\\\{\\\\{\\\\s*include:\\\\s*(_[a-z0-9_]+\\\\.md)\\\\s*\\\\}\\\\}')"
    },
    {
      "name": "MIN_CLAUDE_CLI",
      "lineno": 94,
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
      "lineno": 97,
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
      "lineno": 237,
      "kind": "tuple",
      "len": 42,
      "values": [
        "task",
        "started_at",
        "finished_at",
        "waves",
        "completed_waves",
        "subtask_status",
        "plan_snapshot",
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
      "lineno": 330,
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
      "lineno": 339,
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
      "lineno": 365,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".leerie/",
        ".git/"
      ]
    },
    {
      "name": "_CLAUDE_DELIVERABLE_PREFIXES",
      "lineno": 366,
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
      "lineno": 381,
      "kind": "str",
      "value": "Read,Grep,Glob,WebSearch,WebFetch"
    },
    {
      "name": "INSPECT_TOOLS",
      "lineno": 405,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(find:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(tree:*),Bash(pwd),Bash(echo:*),Bash(git log:*),Bash(git s"
    },
    {
      "name": "ACT_TOOLS",
      "lineno": 413,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash,Write,Edit'"
    },
    {
      "name": "SATISFIED_PROBE_TOOLS",
      "lineno": 427,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(pwd),Bash(echo:*),Bash(git show HEAD:*),Bash(git diff:*),Bash(git status)'"
    },
    {
      "name": "DISALLOWED_TOOLS",
      "lineno": 441,
      "kind": "str",
      "value": "Agent,SendMessage,ScheduleWakeup,CronCreate,CronDelete,CronList,RemoteTrigger,PushNotification"
    },
    {
      "name": "INSPECT_DIRS_ENV",
      "lineno": 455,
      "kind": "str",
      "value": "LEERIE_INSPECT_DIRS"
    },
    {
      "name": "INSPECT_DIRS_FILE",
      "lineno": 456,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EXIT_NEEDS_ANSWERS",
      "lineno": 458,
      "kind": "int",
      "value": 10
    },
    {
      "name": "EXIT_BUDGET_INFEASIBLE",
      "lineno": 469,
      "kind": "int",
      "value": 11
    },
    {
      "name": "EXIT_LOCKED",
      "lineno": 477,
      "kind": "int",
      "value": 75
    },
    {
      "name": "RATE_LIMIT_RETRY_BACKOFF_SEC",
      "lineno": 486,
      "kind": "int",
      "value": 300
    },
    {
      "name": "SOURCE_OF_TRUTH_VALUES",
      "lineno": 494,
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
      "lineno": 495,
      "kind": "str",
      "value": "LEERIE_SOURCE_OF_TRUTH"
    },
    {
      "name": "SOURCE_OF_TRUTH_FILE",
      "lineno": 496,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "RUNTIME_VALUES",
      "lineno": 501,
      "kind": "tuple",
      "len": 2,
      "values": [
        "local",
        "fly"
      ]
    },
    {
      "name": "RUNTIME_ENV",
      "lineno": 502,
      "kind": "str",
      "value": "LEERIE_RUNTIME"
    },
    {
      "name": "RUNTIME_FILE",
      "lineno": 503,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CONFIDENCE_ROUNDS_ENV",
      "lineno": 510,
      "kind": "str",
      "value": "LEERIE_CONFIDENCE_ROUNDS"
    },
    {
      "name": "CONFIDENCE_ROUNDS_FILE",
      "lineno": 511,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "JUDGMENT_CHECK_ROUNDS_ENV",
      "lineno": 514,
      "kind": "str",
      "value": "LEERIE_JUDGMENT_CHECK_ROUNDS"
    },
    {
      "name": "PLANNER_CHECK_ROUNDS_ENV",
      "lineno": 515,
      "kind": "str",
      "value": "LEERIE_PLANNER_CHECK_ROUNDS"
    },
    {
      "name": "IMPLEMENTER_CONFIDENCE_RETRIES_ENV",
      "lineno": 516,
      "kind": "str",
      "value": "LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES"
    },
    {
      "name": "PLANNER_SAMPLES_ENV",
      "lineno": 517,
      "kind": "str",
      "value": "LEERIE_PLANNER_SAMPLES"
    },
    {
      "name": "MAX_WORKERS_ENV",
      "lineno": 522,
      "kind": "str",
      "value": "LEERIE_MAX_WORKERS"
    },
    {
      "name": "MAX_WORKERS_FILE",
      "lineno": 523,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "MAX_PARALLEL_ENV",
      "lineno": 528,
      "kind": "str",
      "value": "LEERIE_MAX_PARALLEL"
    },
    {
      "name": "MAX_PARALLEL_FILE",
      "lineno": 529,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "WORKER_MEMORY_MAX_ENV",
      "lineno": 536,
      "kind": "str",
      "value": "LEERIE_WORKER_MEMORY_MAX"
    },
    {
      "name": "WORKER_MEMORY_MAX_FILE",
      "lineno": 537,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "WORKER_PIDS_MAX_ENV",
      "lineno": 543,
      "kind": "str",
      "value": "LEERIE_WORKER_PIDS_MAX"
    },
    {
      "name": "WORKER_PIDS_MAX_FILE",
      "lineno": 544,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "NO_PUSH_ENV",
      "lineno": 552,
      "kind": "str",
      "value": "LEERIE_NO_PUSH"
    },
    {
      "name": "NO_PUSH_FILE",
      "lineno": 553,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CLARIFY_ENV",
      "lineno": 561,
      "kind": "str",
      "value": "LEERIE_CLARIFY"
    },
    {
      "name": "CLARIFY_FILE",
      "lineno": 562,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_ENV",
      "lineno": 572,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_SKIP_PERMISSIONS"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_FILE",
      "lineno": 573,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_ENV",
      "lineno": 589,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_ALLOW_UNCAPPED"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_FILE",
      "lineno": 590,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_ENV",
      "lineno": 599,
      "kind": "str",
      "value": "LEERIE_SKIP_OVERLAP_JUDGE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_FILE",
      "lineno": 600,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_BUDGET_CHECK_ENV",
      "lineno": 612,
      "kind": "str",
      "value": "LEERIE_SKIP_BUDGET_CHECK"
    },
    {
      "name": "SKIP_BUDGET_CHECK_FILE",
      "lineno": 613,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "STRICT_CONFORMER_ENV",
      "lineno": 615,
      "kind": "str",
      "value": "LEERIE_STRICT_CONFORMER"
    },
    {
      "name": "STRICT_CONFORMER_FILE",
      "lineno": 616,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_BASE_BASELINE_ENV",
      "lineno": 629,
      "kind": "str",
      "value": "LEERIE_SKIP_BASE_BASELINE"
    },
    {
      "name": "SKIP_BASE_BASELINE_FILE",
      "lineno": 630,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_REPO_MAP_ENV",
      "lineno": 639,
      "kind": "str",
      "value": "LEERIE_SKIP_REPO_MAP"
    },
    {
      "name": "SKIP_REPO_MAP_FILE",
      "lineno": 640,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "REPO_MAP_CACHE_DIR",
      "lineno": 646,
      "kind": "str",
      "value": "repo-map-cache"
    },
    {
      "name": "CAPTURE_DEPS_ENV",
      "lineno": 652,
      "kind": "str",
      "value": "LEERIE_CAPTURE_DEPS"
    },
    {
      "name": "CAPTURE_DEPS_CONFIG",
      "lineno": 653,
      "kind": "str",
      "value": ".leerie/config.toml"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_ENV",
      "lineno": 665,
      "kind": "str",
      "value": "LEERIE_SKIP_SATISFIED_CHECK"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_FILE",
      "lineno": 666,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "PR_TEMPLATE_ENV",
      "lineno": 675,
      "kind": "str",
      "value": "LEERIE_PR_TEMPLATE"
    },
    {
      "name": "PR_TEMPLATE_FILE",
      "lineno": 676,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "VERBOSITY_VALUES",
      "lineno": 684,
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
      "lineno": 685,
      "kind": "str",
      "value": "stream"
    },
    {
      "name": "VERBOSITY_ENV",
      "lineno": 686,
      "kind": "str",
      "value": "LEERIE_VERBOSITY"
    },
    {
      "name": "VERBOSITY_FILE",
      "lineno": 687,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "_TERMINAL_STATUSES",
      "lineno": 690,
      "kind": "expr",
      "expr": "frozenset({'complete', 'failed', 'blocked'})"
    },
    {
      "name": "MODEL_VALUES",
      "lineno": 696,
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
      "lineno": 703,
      "kind": "str",
      "value": "opus"
    },
    {
      "name": "MODEL_DEFAULT_PER_WORKER",
      "lineno": 707,
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
      "lineno": 719,
      "kind": "str",
      "value": "LEERIE_MODEL"
    },
    {
      "name": "MODEL_FILE",
      "lineno": 720,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EFFORT_VALUES",
      "lineno": 728,
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
      "lineno": 729,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "EFFORT_DEFAULT_PER_WORKER",
      "lineno": 730,
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
      "lineno": 742,
      "kind": "str",
      "value": "LEERIE_EFFORT"
    },
    {
      "name": "WORKER_TYPES",
      "lineno": 743,
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
      "lineno": 749,
      "kind": "str",
      "value": "LEERIE_MODEL_JUDGE"
    },
    {
      "name": "MODEL_HEAL_ENV",
      "lineno": 750,
      "kind": "str",
      "value": "LEERIE_MODEL_HEAL"
    },
    {
      "name": "MODEL_PR_WRITER_ENV",
      "lineno": 751,
      "kind": "str",
      "value": "LEERIE_MODEL_PR_WRITER"
    },
    {
      "name": "MODEL_DEP_CAPTURE_ENV",
      "lineno": 752,
      "kind": "str",
      "value": "LEERIE_MODEL_DEP_CAPTURE"
    },
    {
      "name": "JUDGE_DIR_DEFAULT",
      "lineno": 757,
      "kind": "str",
      "value": "judge-out"
    },
    {
      "name": "JUDGE_DIR_ENV",
      "lineno": 758,
      "kind": "str",
      "value": "LEERIE_JUDGE_DIR"
    },
    {
      "name": "JUDGE_DIR_FILE",
      "lineno": 759,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_DIR_DEFAULT",
      "lineno": 764,
      "kind": "str",
      "value": "heal-out"
    },
    {
      "name": "HEAL_DIR_ENV",
      "lineno": 765,
      "kind": "str",
      "value": "LEERIE_HEAL_DIR"
    },
    {
      "name": "HEAL_DIR_FILE",
      "lineno": 766,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_MAX_ROUNDS_DEFAULT",
      "lineno": 771,
      "kind": "int",
      "value": 10
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_DEFAULT",
      "lineno": 772,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "HEAL_PLATEAU_WINDOW_DEFAULT",
      "lineno": 773,
      "kind": "int",
      "value": 3
    },
    {
      "name": "HEAL_PLATEAU_DELTA_DEFAULT",
      "lineno": 774,
      "kind": "float",
      "value": 0.03
    },
    {
      "name": "HEAL_N_REPLAYS_DEFAULT",
      "lineno": 775,
      "kind": "int",
      "value": 5
    },
    {
      "name": "HEAL_MAX_ROUNDS_ENV",
      "lineno": 776,
      "kind": "str",
      "value": "LEERIE_HEAL_MAX_ROUNDS"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_ENV",
      "lineno": 777,
      "kind": "str",
      "value": "LEERIE_HEAL_SUCCESS_THRESHOLD"
    },
    {
      "name": "HEAL_MAX_ROUNDS_FILE",
      "lineno": 778,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_FILE",
      "lineno": 779,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "STATE_DIR_ENV",
      "lineno": 787,
      "kind": "str",
      "value": "LEERIE_STATE_DIR"
    },
    {
      "name": "_CONFORMER_BLT_PROP",
      "lineno": 820,
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
      "lineno": 839,
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
      "lineno": 875,
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
      "lineno": 1703,
      "kind": "expr",
      "expr": "re.compile(\"you've hit your session limit\", re.IGNORECASE)"
    },
    {
      "name": "_SESSION_LIMIT_RESET",
      "lineno": 1705,
      "kind": "expr",
      "expr": "re.compile('resets?\\\\s+(\\\\d{1,2}):(\\\\d{2})\\\\s*([ap]m)\\\\s*\\\\(([^)]+)\\\\)', re.IGNORECASE)"
    },
    {
      "name": "_RATE_LIMIT_ALLOWED_STATUSES",
      "lineno": 1714,
      "kind": "tuple",
      "len": 2,
      "values": [
        "allowed",
        "allowed_warning"
      ]
    },
    {
      "name": "_PROC_TREE_GRACE_SEC",
      "lineno": 1772,
      "kind": "float",
      "value": 2.0
    },
    {
      "name": "_DESCENDANT_POLL_SEC",
      "lineno": 1876,
      "kind": "float",
      "value": 0.5
    },
    {
      "name": "_PID_REAP_HIGH_WATER",
      "lineno": 1883,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "_PID_REAP_LOW_WATER",
      "lineno": 1884,
      "kind": "float",
      "value": 0.75
    },
    {
      "name": "_PID_REAP_MIN_AGE_SEC",
      "lineno": 1885,
      "kind": "int",
      "value": 60
    },
    {
      "name": "_PID_EXHAUSTION_WINDOW",
      "lineno": 1900,
      "kind": "int",
      "value": 6
    },
    {
      "name": "_PID_EXHAUSTION_ERROR_THRESHOLD",
      "lineno": 1901,
      "kind": "int",
      "value": 3
    },
    {
      "name": "RUN_STATUSES",
      "lineno": 2835,
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
      "lineno": 3199,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".txt",
        ".md"
      ]
    },
    {
      "name": "_DEFINITE_ABSENCE",
      "lineno": 3206,
      "kind": "expr",
      "expr": "frozenset({errno.ENOENT, errno.ENOTDIR})"
    },
    {
      "name": "_MEMORY_SUFFIX_MULTIPLIER",
      "lineno": 3474,
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
      "lineno": 4446,
      "kind": "expr",
      "expr": "frozenset((f'{v}-' for v in CATEGORY_ABBREV.values()))"
    },
    {
      "name": "_VALID_EXTENTS",
      "lineno": 4449,
      "kind": "expr",
      "expr": "frozenset({'in_plan', 'external'})"
    },
    {
      "name": "_MIGRATION_SIGNAL_RE",
      "lineno": 4532,
      "kind": "expr",
      "expr": "re.compile('replac(?:es?|ing)\\\\s+(?:direct\\\\s+)?[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|migrat(?:es?|ing)\\\\s+from\\\\s+[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|extract(?:s|ing)\\\\s+[`\\'\\\\\"]?([a-zA"
    },
    {
      "name": "_MIGRATION_SURFACE_THRESHOLD",
      "lineno": 4540,
      "kind": "int",
      "value": 5
    },
    {
      "name": "_GLOB_CHARS",
      "lineno": 5235,
      "kind": "expr",
      "expr": "frozenset('*?[{')"
    },
    {
      "name": "_BRACE_RE",
      "lineno": 5236,
      "kind": "expr",
      "expr": "re.compile('\\\\{([^}]+)\\\\}')"
    },
    {
      "name": "_MAX_COVERAGE_ITEMS",
      "lineno": 5324,
      "kind": "int",
      "value": 50
    },
    {
      "name": "_SOURCE_EXTS",
      "lineno": 5466,
      "kind": "expr",
      "expr": "frozenset({'.py', '.pyi', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.go', '.rs', '.rb', '.java', '.kt', '.c', '.h', '.cc', '.cpp', '.hpp', '.cs', '.php', '.swift', '.scala', '.sh', '.lua'})"
    },
    {
      "name": "_repo_map_empty_warned",
      "lineno": 5472,
      "kind": "bool",
      "value": false
    },
    {
      "name": "_ENV_TAG_KEYWORDS",
      "lineno": 6012,
      "kind": "expr",
      "expr": "frozenset({'env', 'bootstrap', 'secret', 'config-key', 'credential'})"
    },
    {
      "name": "_PROVISION_ARGV0_ALLOW",
      "lineno": 6384,
      "kind": "expr",
      "expr": "frozenset({'pnpm', 'npm', 'yarn', 'pip', 'pip3', 'uv', 'poetry', 'pipenv', 'go', 'cargo', 'bundle', 'gem', 'mvn', 'gradle', 'gradlew', 'make', 'composer', 'dotnet'})"
    },
    {
      "name": "_PROVISION_SHELL_METACHARS",
      "lineno": 6395,
      "kind": "expr",
      "expr": "frozenset(set('|&;$`><\\n\\r'))"
    },
    {
      "name": "_README_SECTION_RE",
      "lineno": 6648,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(install|getting[\\\\s-]?started|quick[\\\\s-]?start|setup|usage|\\\\brun\\\\b|develop|build(ing)?( from source| instructions)?|compil(e|ing)( from source)?|download|from source|requirement"
    },
    {
      "name": "_HEADER_DECOR_RE",
      "lineno": 6673,
      "kind": "expr",
      "expr": "re.compile('^[^\\\\w]+', flags=re.UNICODE)"
    },
    {
      "name": "_INSTALL_CMD_HINT_RE",
      "lineno": 6678,
      "kind": "expr",
      "expr": "re.compile('\\\\b(pip|pip3|npm|pnpm|yarn|uv|poetry|cargo|brew|apt|apt-get|dnf|yum|pacman|go install|make|bundle install|gem install|mise install)\\\\b')"
    },
    {
      "name": "_DEPCAP_TOTAL_BUDGET",
      "lineno": 6689,
      "kind": "int",
      "value": 307200
    },
    {
      "name": "_DEP_MANIFEST_NAMES",
      "lineno": 6700,
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
      "lineno": 6707,
      "kind": "int",
      "value": 16384
    },
    {
      "name": "_DEPCAP_MANIFEST_TOTAL_BUDGET",
      "lineno": 6708,
      "kind": "int",
      "value": 131072
    },
    {
      "name": "_DEPCAP_INSTALL_RE",
      "lineno": 6715,
      "kind": "expr",
      "expr": "re.compile('(?:^|[\\\\s;&|(])(?:sudo\\\\s+)?(?:apt-get\\\\s+install|apt\\\\s+install|yum\\\\s+install|dnf\\\\s+install|apk\\\\s+add|pip3?\\\\s+install|pipx\\\\s+install|poetry\\\\s+add|uv\\\\s+(?:add|pip\\\\s+install)|npm\\\\s"
    },
    {
      "name": "_DEPCAP_TEXT_TOOLS",
      "lineno": 6726,
      "kind": "expr",
      "expr": "frozenset({'grep', 'rg', 'git', 'sed', 'awk', 'echo', 'cat', 'printf', 'ag', 'ack'})"
    },
    {
      "name": "_DEPCAP_SEGMENT_RE",
      "lineno": 6733,
      "kind": "expr",
      "expr": "re.compile('[\\\\n;]|&&|\\\\|\\\\||[|&]')"
    },
    {
      "name": "_README_INTRO_BUDGET",
      "lineno": 7340,
      "kind": "int",
      "value": 1024
    },
    {
      "name": "_README_EXTRACT_BUDGET",
      "lineno": 7341,
      "kind": "int",
      "value": 8192
    },
    {
      "name": "_README_FALLBACK_BUDGET",
      "lineno": 7342,
      "kind": "int",
      "value": 6144
    },
    {
      "name": "_FIXTURE_TOTAL_BUDGET",
      "lineno": 7343,
      "kind": "int",
      "value": 24576
    },
    {
      "name": "_PROVISION_ROOT_MANIFESTS",
      "lineno": 7417,
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
      "lineno": 7422,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(ci|test|build|release)\\\\b')"
    },
    {
      "name": "_PROVISION_WORKFLOW_SKIP_RE",
      "lineno": 7423,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(codeql|stale|dependabot)\\\\b')"
    },
    {
      "name": "_CHECKPOINT_SECTIONS",
      "lineno": 7579,
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
      "lineno": 7592,
      "kind": "set",
      "len": 2,
      "values": [
        "## Decisions made",
        "## Open unknowns"
      ]
    },
    {
      "name": "_NOISE_TOKENS",
      "lineno": 7600,
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
      "lineno": 8136,
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
      "lineno": 8530,
      "kind": "str",
      "value": "/run/leerie-cgroup.sock"
    },
    {
      "name": "_CGROUP_PROBE_RESULT",
      "lineno": 8531,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_CGROUP_HIERARCHY",
      "lineno": 8532,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_PR_SET_CHILD_SUBREAPER",
      "lineno": 9302,
      "kind": "int",
      "value": 36
    },
    {
      "name": "_PR_GET_CHILD_SUBREAPER",
      "lineno": 9303,
      "kind": "int",
      "value": 37
    },
    {
      "name": "_ASYNCIO_MANAGED_PIDS",
      "lineno": 9315,
      "kind": "expr",
      "expr": "set()"
    },
    {
      "name": "_DOCS_ONLY_CATEGORIES",
      "lineno": 10749,
      "kind": "expr",
      "expr": "frozenset({'documentation'})"
    },
    {
      "name": "_GO_MOD_VERSION_RE",
      "lineno": 10826,
      "kind": "expr",
      "expr": "re.compile('^\\\\s*go\\\\s+(\\\\d+(?:\\\\.\\\\d+){0,2})\\\\s*$', re.MULTILINE)"
    },
    {
      "name": "_LEADING_V_RE",
      "lineno": 10881,
      "kind": "expr",
      "expr": "re.compile('^[vV]+')"
    },
    {
      "name": "_IDIOMATIC_VERSION_FILES",
      "lineno": 10892,
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
      "lineno": 10907,
      "kind": "dict",
      "len": 2,
      "keys": [
        "nodejs",
        "python3"
      ]
    },
    {
      "name": "_MISE_SIGNAL_FILES",
      "lineno": 11134,
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
      "lineno": 15692,
      "kind": "expr",
      "expr": "frozenset({'no_commits', 'dirty_worktree', 'empty_handoff'})"
    },
    {
      "name": "_RULES_FILE_CANDIDATES",
      "lineno": 15742,
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
      "lineno": 16342,
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
      "lineno": 16366,
      "kind": "str",
      "value": "Command running in background with ID:"
    },
    {
      "name": "_BG_ID_RE",
      "lineno": 16367,
      "kind": "expr",
      "expr": "re.compile('Command running in background with ID:\\\\s*(\\\\w+)')"
    },
    {
      "name": "_PR_TEMPLATE_SINGLE_LOCATIONS",
      "lineno": 17795,
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
      "lineno": 17803,
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
      "lineno": 17864,
      "kind": "int",
      "value": 80000
    },
    {
      "name": "PR_WRITER_TEMPLATE_MAX_BYTES",
      "lineno": 17865,
      "kind": "int",
      "value": 32000
    },
    {
      "name": "PR_WRITER_DIFF_SAMPLE_MAX_LINES",
      "lineno": 17866,
      "kind": "int",
      "value": 500
    },
    {
      "name": "PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES",
      "lineno": 17872,
      "kind": "int",
      "value": 8000
    },
    {
      "name": "_LEERIE_PREFIX_RE",
      "lineno": 17900,
      "kind": "expr",
      "expr": "re.compile('^leerie:\\\\s*', re.IGNORECASE)"
    }
  ],
  "functions": [
    {
      "name": "_read_version",
      "qualname": "_read_version",
      "async": false,
      "lineno": 60,
      "end_lineno": 69,
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
      "lineno": 78,
      "end_lineno": 87,
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
      "lineno": 371,
      "end_lineno": 379,
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
      "lineno": 792,
      "end_lineno": 807,
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
      "lineno": 850,
      "end_lineno": 873,
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
      "lineno": 1624,
      "end_lineno": 1625,
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
      "lineno": 1628,
      "end_lineno": 1631,
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
      "lineno": 1634,
      "end_lineno": 1636,
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
      "lineno": 1717,
      "end_lineno": 1756,
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
      "lineno": 1759,
      "end_lineno": 1769,
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
      "lineno": 1775,
      "end_lineno": 1814,
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
      "lineno": 1817,
      "end_lineno": 1831,
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
      "lineno": 1834,
      "end_lineno": 1873,
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
      "lineno": 2019,
      "end_lineno": 2087,
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
      "lineno": 2090,
      "end_lineno": 2205,
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
      "lineno": 2208,
      "end_lineno": 2231,
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
      "lineno": 2234,
      "end_lineno": 2304,
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
      "lineno": 2307,
      "end_lineno": 2312,
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
      "lineno": 2315,
      "end_lineno": 2345,
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
      "lineno": 2359,
      "end_lineno": 2370,
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
      "lineno": 2373,
      "end_lineno": 2382,
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
      "lineno": 2387,
      "end_lineno": 2483,
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
      "lineno": 2488,
      "end_lineno": 2509,
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
      "lineno": 2512,
      "end_lineno": 2594,
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
      "lineno": 2597,
      "end_lineno": 2622,
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
      "lineno": 2627,
      "end_lineno": 2714,
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
      "lineno": 2717,
      "end_lineno": 2751,
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
      "lineno": 2754,
      "end_lineno": 2799,
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
      "lineno": 2802,
      "end_lineno": 2819,
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
      "lineno": 2851,
      "end_lineno": 2938,
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
      "lineno": 2941,
      "end_lineno": 2978,
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
      "lineno": 2981,
      "end_lineno": 2998,
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
      "lineno": 3001,
      "end_lineno": 3035,
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
      "lineno": 3038,
      "end_lineno": 3069,
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
      "lineno": 3072,
      "end_lineno": 3096,
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
      "lineno": 3099,
      "end_lineno": 3177,
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
      "lineno": 3180,
      "end_lineno": 3196,
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
      "lineno": 3209,
      "end_lineno": 3248,
      "span": 40,
      "decorators": [],
      "signature": "(raw: str)",
      "returns": "str",
      "doc": "Resolve the positional `task` argument to the task string."
    },
    {
      "name": "resolve_leerie_root",
      "qualname": "resolve_leerie_root",
      "async": false,
      "lineno": 3251,
      "end_lineno": 3266,
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
      "lineno": 3269,
      "end_lineno": 3288,
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
      "lineno": 3291,
      "end_lineno": 3303,
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
      "lineno": 3306,
      "end_lineno": 3317,
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
      "lineno": 3320,
      "end_lineno": 3334,
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
      "lineno": 3337,
      "end_lineno": 3349,
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
      "lineno": 3352,
      "end_lineno": 3378,
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
      "lineno": 3381,
      "end_lineno": 3393,
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
      "lineno": 3396,
      "end_lineno": 3408,
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
      "lineno": 3411,
      "end_lineno": 3423,
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
      "lineno": 3426,
      "end_lineno": 3435,
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
      "lineno": 3438,
      "end_lineno": 3447,
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
      "lineno": 3450,
      "end_lineno": 3460,
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
      "lineno": 3463,
      "end_lineno": 3471,
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
      "lineno": 3479,
      "end_lineno": 3503,
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
      "lineno": 3506,
      "end_lineno": 3541,
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
      "lineno": 3544,
      "end_lineno": 3564,
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
      "lineno": 3567,
      "end_lineno": 3579,
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
      "lineno": 3582,
      "end_lineno": 3620,
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
      "lineno": 3623,
      "end_lineno": 3635,
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
      "lineno": 3638,
      "end_lineno": 3665,
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
      "lineno": 3668,
      "end_lineno": 3675,
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
      "lineno": 3678,
      "end_lineno": 3702,
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
      "lineno": 3705,
      "end_lineno": 3712,
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
      "lineno": 3715,
      "end_lineno": 3734,
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
      "lineno": 3737,
      "end_lineno": 3752,
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
      "lineno": 3755,
      "end_lineno": 3772,
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
      "lineno": 3775,
      "end_lineno": 3794,
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
      "lineno": 3797,
      "end_lineno": 3819,
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
      "lineno": 3822,
      "end_lineno": 3836,
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
      "lineno": 3839,
      "end_lineno": 3854,
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
      "lineno": 3857,
      "end_lineno": 3872,
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
      "lineno": 3875,
      "end_lineno": 3884,
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
      "lineno": 3887,
      "end_lineno": 3906,
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
      "lineno": 3909,
      "end_lineno": 3927,
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
      "lineno": 3930,
      "end_lineno": 4011,
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
      "lineno": 4014,
      "end_lineno": 4075,
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
      "lineno": 4078,
      "end_lineno": 4087,
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
      "lineno": 4090,
      "end_lineno": 4099,
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
      "lineno": 4102,
      "end_lineno": 4111,
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
      "lineno": 4114,
      "end_lineno": 4141,
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
      "lineno": 4144,
      "end_lineno": 4188,
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
      "lineno": 4191,
      "end_lineno": 4314,
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
      "lineno": 4317,
      "end_lineno": 4338,
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
      "lineno": 4341,
      "end_lineno": 4343,
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
      "lineno": 4356,
      "end_lineno": 4443,
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
      "lineno": 4458,
      "end_lineno": 4476,
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
      "lineno": 4479,
      "end_lineno": 4525,
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
      "lineno": 4543,
      "end_lineno": 4565,
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
      "lineno": 4568,
      "end_lineno": 4596,
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
      "lineno": 4603,
      "end_lineno": 4619,
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
      "lineno": 4622,
      "end_lineno": 4639,
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
      "lineno": 4642,
      "end_lineno": 4654,
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
      "lineno": 4657,
      "end_lineno": 4723,
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
      "lineno": 4726,
      "end_lineno": 4908,
      "span": 183,
      "decorators": [],
      "signature": "(subtask: dict, depth: int, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None], repo_root: Path, *, repo_map: dict | None = None, _parent_score: float | None = None, _noprogress_count: int = 0)",
      "returns": "list[dict]",
      "doc": "Recursively decompose *subtask* until leaves pass the P1 fit threshold."
    },
    {
      "name": "_remap_vanished_deps",
      "qualname": "_remap_vanished_deps",
      "async": false,
      "lineno": 4911,
      "end_lineno": 4943,
      "span": 33,
      "decorators": [],
      "signature": "(subtasks: list[dict], mapping: dict[str, list[str]])",
      "returns": "None",
      "doc": "In-place: rewrite `depends_on` refs to ids that vanished from the plan."
    },
    {
      "name": "check_planner_output",
      "qualname": "check_planner_output",
      "async": false,
      "lineno": 4946,
      "end_lineno": 5053,
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
      "lineno": 5056,
      "end_lineno": 5087,
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
      "lineno": 5090,
      "end_lineno": 5151,
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
      "lineno": 5154,
      "end_lineno": 5193,
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
      "lineno": 5196,
      "end_lineno": 5199,
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
      "lineno": 5202,
      "end_lineno": 5224,
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
      "lineno": 5239,
      "end_lineno": 5252,
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
      "lineno": 5255,
      "end_lineno": 5284,
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
      "lineno": 5287,
      "end_lineno": 5321,
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
      "lineno": 5327,
      "end_lineno": 5358,
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
      "lineno": 5361,
      "end_lineno": 5372,
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
      "lineno": 5386,
      "end_lineno": 5392,
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
      "lineno": 5395,
      "end_lineno": 5418,
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
      "lineno": 5421,
      "end_lineno": 5460,
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
      "lineno": 5475,
      "end_lineno": 5490,
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
      "lineno": 5493,
      "end_lineno": 5511,
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
      "lineno": 5514,
      "end_lineno": 5622,
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
      "lineno": 5625,
      "end_lineno": 5673,
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
      "lineno": 5676,
      "end_lineno": 5698,
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
      "lineno": 5701,
      "end_lineno": 5703,
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
      "lineno": 5706,
      "end_lineno": 5796,
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
      "lineno": 5799,
      "end_lineno": 5908,
      "span": 110,
      "decorators": [],
      "signature": "(subtasks: dict)",
      "returns": "None",
      "doc": "Structural validation of the merged plan \u2014 pure Python set operations."
    },
    {
      "name": "warn_cross_planner_file_overlap",
      "qualname": "warn_cross_planner_file_overlap",
      "async": false,
      "lineno": 5911,
      "end_lineno": 5942,
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
      "lineno": 5945,
      "end_lineno": 6009,
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
      "lineno": 6016,
      "end_lineno": 6054,
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
      "lineno": 6057,
      "end_lineno": 6069,
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
      "lineno": 6072,
      "end_lineno": 6136,
      "span": 65,
      "decorators": [],
      "signature": "(plans: list[dict], repo_root: Path, inspect_dirs: list[str], st: 'State')",
      "returns": "None",
      "doc": "Mutate `plans` in place: drop any subtask whose `files_likely_touched`"
    },
    {
      "name": "filter_satisfied_subtasks",
      "qualname": "filter_satisfied_subtasks",
      "async": true,
      "lineno": 6139,
      "end_lineno": 6295,
      "span": 157,
      "decorators": [],
      "signature": "(plans: list[dict], repo_root: Path, st: 'State', caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "dict[str, str] | None",
      "doc": "Mutate `plans` in place: drop any subtask whose success criteria"
    },
    {
      "name": "probe_criteria_satisfied_on_head",
      "qualname": "probe_criteria_satisfied_on_head",
      "async": true,
      "lineno": 6298,
      "end_lineno": 6372,
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
      "lineno": 6398,
      "end_lineno": 6511,
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
      "lineno": 6514,
      "end_lineno": 6519,
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
      "lineno": 6522,
      "end_lineno": 6553,
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
      "lineno": 6556,
      "end_lineno": 6575,
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
      "lineno": 6578,
      "end_lineno": 6639,
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
      "lineno": 6736,
      "end_lineno": 6759,
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
      "lineno": 6762,
      "end_lineno": 6788,
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
      "lineno": 6791,
      "end_lineno": 6795,
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
      "lineno": 6798,
      "end_lineno": 6815,
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
      "lineno": 6818,
      "end_lineno": 6829,
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
      "lineno": 6832,
      "end_lineno": 6846,
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
      "lineno": 6849,
      "end_lineno": 6888,
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
      "lineno": 6891,
      "end_lineno": 6917,
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
      "lineno": 6920,
      "end_lineno": 6943,
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
      "lineno": 6946,
      "end_lineno": 7107,
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
      "lineno": 7110,
      "end_lineno": 7145,
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
      "lineno": 7148,
      "end_lineno": 7230,
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
      "lineno": 7233,
      "end_lineno": 7282,
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
      "lineno": 7285,
      "end_lineno": 7292,
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
      "lineno": 7295,
      "end_lineno": 7334,
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
      "lineno": 7346,
      "end_lineno": 7403,
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
      "lineno": 7406,
      "end_lineno": 7413,
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
      "lineno": 7426,
      "end_lineno": 7463,
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
      "lineno": 7466,
      "end_lineno": 7574,
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
      "lineno": 7607,
      "end_lineno": 7623,
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
      "lineno": 7626,
      "end_lineno": 7675,
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
      "lineno": 7678,
      "end_lineno": 7690,
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
      "lineno": 7693,
      "end_lineno": 7707,
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
      "lineno": 7710,
      "end_lineno": 7730,
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
      "lineno": 7735,
      "end_lineno": 7803,
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
      "lineno": 7808,
      "end_lineno": 7851,
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
      "lineno": 7856,
      "end_lineno": 7879,
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
      "lineno": 7882,
      "end_lineno": 7895,
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
      "lineno": 7900,
      "end_lineno": 7923,
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
      "lineno": 7926,
      "end_lineno": 7958,
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
      "lineno": 7963,
      "end_lineno": 7981,
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
      "lineno": 7986,
      "end_lineno": 8011,
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
      "lineno": 8021,
      "end_lineno": 8033,
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
      "lineno": 8036,
      "end_lineno": 8066,
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
      "lineno": 8069,
      "end_lineno": 8110,
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
      "lineno": 8113,
      "end_lineno": 8126,
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
      "lineno": 8144,
      "end_lineno": 8148,
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
      "lineno": 8151,
      "end_lineno": 8174,
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
      "lineno": 8177,
      "end_lineno": 8212,
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
      "lineno": 8215,
      "end_lineno": 8254,
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
      "lineno": 8257,
      "end_lineno": 8421,
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
      "lineno": 8424,
      "end_lineno": 8447,
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
      "lineno": 8450,
      "end_lineno": 8493,
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
      "lineno": 8535,
      "end_lineno": 8543,
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
      "lineno": 8546,
      "end_lineno": 8575,
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
      "lineno": 8578,
      "end_lineno": 8597,
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
      "lineno": 8600,
      "end_lineno": 8613,
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
      "lineno": 8616,
      "end_lineno": 8623,
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
      "lineno": 8626,
      "end_lineno": 8654,
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
      "lineno": 8657,
      "end_lineno": 8708,
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
      "lineno": 8711,
      "end_lineno": 9231,
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
      "lineno": 9234,
      "end_lineno": 9244,
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
      "lineno": 9247,
      "end_lineno": 9296,
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
      "lineno": 9318,
      "end_lineno": 9342,
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
      "lineno": 9345,
      "end_lineno": 9362,
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
      "lineno": 9365,
      "end_lineno": 9398,
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
      "lineno": 9401,
      "end_lineno": 9438,
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
      "lineno": 9441,
      "end_lineno": 9468,
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
      "lineno": 9471,
      "end_lineno": 9513,
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
      "lineno": 9516,
      "end_lineno": 9791,
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
      "lineno": 9794,
      "end_lineno": 9858,
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
      "lineno": 9861,
      "end_lineno": 9871,
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
      "lineno": 10059,
      "end_lineno": 10107,
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
      "lineno": 10110,
      "end_lineno": 10178,
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
      "lineno": 10233,
      "end_lineno": 10312,
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
      "lineno": 10315,
      "end_lineno": 10338,
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
      "lineno": 10341,
      "end_lineno": 10440,
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
      "lineno": 10443,
      "end_lineno": 10503,
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
      "lineno": 10506,
      "end_lineno": 10558,
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
      "lineno": 10561,
      "end_lineno": 10648,
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
      "lineno": 10651,
      "end_lineno": 10736,
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
      "lineno": 10752,
      "end_lineno": 10821,
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
      "lineno": 10829,
      "end_lineno": 10841,
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
      "lineno": 10844,
      "end_lineno": 10876,
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
      "lineno": 10913,
      "end_lineno": 10935,
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
      "lineno": 10938,
      "end_lineno": 10998,
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
      "lineno": 11001,
      "end_lineno": 11127,
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
      "lineno": 11145,
      "end_lineno": 11156,
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
      "lineno": 11159,
      "end_lineno": 11246,
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
      "lineno": 11252,
      "end_lineno": 11305,
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
      "lineno": 11308,
      "end_lineno": 11353,
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
      "lineno": 11356,
      "end_lineno": 11418,
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
      "lineno": 11421,
      "end_lineno": 11466,
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
      "lineno": 11469,
      "end_lineno": 11497,
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
      "lineno": 11500,
      "end_lineno": 11658,
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
      "lineno": 11661,
      "end_lineno": 11683,
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
      "lineno": 11686,
      "end_lineno": 11879,
      "span": 194,
      "decorators": [],
      "signature": "(task: str, st: State, caps: dict, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "list[dict]",
      "doc": "Phase 2: one planner per category, run in parallel (bounded by"
    },
    {
      "name": "_promote_external_collisions",
      "qualname": "_promote_external_collisions",
      "async": false,
      "lineno": 11882,
      "end_lineno": 11906,
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
      "lineno": 11909,
      "end_lineno": 11939,
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
      "lineno": 11942,
      "end_lineno": 11981,
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
      "lineno": 11984,
      "end_lineno": 12039,
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
      "lineno": 12042,
      "end_lineno": 12061,
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
      "lineno": 12064,
      "end_lineno": 12457,
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
      "lineno": 12460,
      "end_lineno": 13055,
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
      "lineno": 13058,
      "end_lineno": 13126,
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
      "lineno": 13129,
      "end_lineno": 13192,
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
      "lineno": 13195,
      "end_lineno": 13235,
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
      "lineno": 13238,
      "end_lineno": 13253,
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
      "lineno": 13256,
      "end_lineno": 13286,
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
      "lineno": 13289,
      "end_lineno": 13484,
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
      "lineno": 13487,
      "end_lineno": 13507,
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
      "lineno": 13510,
      "end_lineno": 13547,
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
      "lineno": 13550,
      "end_lineno": 13638,
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
      "lineno": 13641,
      "end_lineno": 13709,
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
      "lineno": 13712,
      "end_lineno": 13726,
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
      "lineno": 13729,
      "end_lineno": 13771,
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
      "lineno": 13774,
      "end_lineno": 13791,
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
      "lineno": 13794,
      "end_lineno": 13891,
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
      "lineno": 13894,
      "end_lineno": 14047,
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
      "lineno": 14050,
      "end_lineno": 14182,
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
      "lineno": 14212,
      "end_lineno": 14234,
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
      "lineno": 14237,
      "end_lineno": 14334,
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
      "lineno": 14337,
      "end_lineno": 14443,
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
      "lineno": 14446,
      "end_lineno": 14638,
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
      "lineno": 14641,
      "end_lineno": 14671,
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
      "lineno": 14674,
      "end_lineno": 14892,
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
      "lineno": 14895,
      "end_lineno": 15113,
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
      "lineno": 15116,
      "end_lineno": 15177,
      "span": 62,
      "decorators": [],
      "signature": "(subtasks: dict[str, dict])",
      "returns": "tuple[dict[str, set[str]], dict[str, list[str]], dict[tuple[str, str], str]]",
      "doc": "Build the predecessor graph from a merged subtasks dict."
    },
    {
      "name": "detect_no_work",
      "qualname": "detect_no_work",
      "async": false,
      "lineno": 15180,
      "end_lineno": 15208,
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
      "lineno": 15211,
      "end_lineno": 15254,
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
      "lineno": 15257,
      "end_lineno": 15306,
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
      "lineno": 15309,
      "end_lineno": 15370,
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
      "lineno": 15373,
      "end_lineno": 15394,
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
      "lineno": 15397,
      "end_lineno": 15417,
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
      "lineno": 15420,
      "end_lineno": 15449,
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
      "lineno": 15452,
      "end_lineno": 15479,
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
      "lineno": 15482,
      "end_lineno": 15507,
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
      "lineno": 15510,
      "end_lineno": 15563,
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
      "lineno": 15566,
      "end_lineno": 15681,
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
      "lineno": 15711,
      "end_lineno": 15725,
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
      "lineno": 15758,
      "end_lineno": 15770,
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
      "lineno": 15773,
      "end_lineno": 15782,
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
      "lineno": 15785,
      "end_lineno": 15797,
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
      "lineno": 15800,
      "end_lineno": 15805,
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
      "lineno": 15808,
      "end_lineno": 15887,
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
      "lineno": 15890,
      "end_lineno": 15908,
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
      "lineno": 15911,
      "end_lineno": 15940,
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
      "lineno": 15943,
      "end_lineno": 15996,
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
      "lineno": 15999,
      "end_lineno": 16005,
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
      "lineno": 16008,
      "end_lineno": 16029,
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
      "lineno": 16032,
      "end_lineno": 16046,
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
      "lineno": 16049,
      "end_lineno": 16086,
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
      "lineno": 16089,
      "end_lineno": 16100,
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
      "lineno": 16103,
      "end_lineno": 16117,
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
      "lineno": 16120,
      "end_lineno": 16142,
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
      "lineno": 16145,
      "end_lineno": 16211,
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
      "lineno": 16214,
      "end_lineno": 16227,
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
      "lineno": 16230,
      "end_lineno": 16241,
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
      "lineno": 16244,
      "end_lineno": 16264,
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
      "lineno": 16267,
      "end_lineno": 16320,
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
      "lineno": 16323,
      "end_lineno": 16333,
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
      "lineno": 16370,
      "end_lineno": 16424,
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
      "lineno": 16427,
      "end_lineno": 16438,
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
      "lineno": 16441,
      "end_lineno": 16502,
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
      "lineno": 16505,
      "end_lineno": 16530,
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
      "lineno": 16533,
      "end_lineno": 16688,
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
      "lineno": 16691,
      "end_lineno": 16698,
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
      "lineno": 16701,
      "end_lineno": 16774,
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
      "lineno": 16777,
      "end_lineno": 16917,
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
      "lineno": 16920,
      "end_lineno": 17149,
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
      "lineno": 17152,
      "end_lineno": 17547,
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
      "lineno": 17550,
      "end_lineno": 17663,
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
      "lineno": 17666,
      "end_lineno": 17773,
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
      "lineno": 17810,
      "end_lineno": 17855,
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
      "lineno": 17875,
      "end_lineno": 17894,
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
      "lineno": 17903,
      "end_lineno": 17913,
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
      "lineno": 17916,
      "end_lineno": 17930,
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
      "lineno": 17933,
      "end_lineno": 17963,
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
      "lineno": 17966,
      "end_lineno": 18030,
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
      "lineno": 18033,
      "end_lineno": 18089,
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
      "lineno": 18092,
      "end_lineno": 18278,
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
      "lineno": 18281,
      "end_lineno": 18419,
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
      "lineno": 18425,
      "end_lineno": 18452,
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
      "lineno": 18455,
      "end_lineno": 18729,
      "span": 275,
      "decorators": [],
      "signature": "(args, caps: dict, leerie_dir: Path, st: State, sot_pref: str, verbosity: str, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "The phase sequence of one run. Split out from `orchestrate()`"
    },
    {
      "name": "main",
      "qualname": "main",
      "async": false,
      "lineno": 18732,
      "end_lineno": 19607,
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
      "lineno": 1639,
      "end_lineno": 1651,
      "span": 13,
      "doc": "Raised by signal handlers (SIGTERM, SIGHUP) installed in main().",
      "methods": []
    },
    {
      "name": "RateLimitedExit",
      "bases": [
        "BaseException"
      ],
      "lineno": 1654,
      "end_lineno": 1690,
      "span": 37,
      "doc": "Raised when claude -p reports the Claude Code subscription",
      "methods": [
        {
          "name": "__init__",
          "qualname": "RateLimitedExit.__init__",
          "async": false,
          "lineno": 1685,
          "end_lineno": 1690,
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
      "lineno": 1904,
      "end_lineno": 2016,
      "span": 113,
      "doc": "Background poller that accumulates every PID ever observed as a",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_DescendantTracker.__init__",
          "async": false,
          "lineno": 1933,
          "end_lineno": 1939,
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
          "lineno": 1941,
          "end_lineno": 1944,
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
          "lineno": 1946,
          "end_lineno": 1993,
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
          "lineno": 1995,
          "end_lineno": 2016,
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
      "lineno": 8017,
      "end_lineno": 8018,
      "span": 2,
      "doc": null,
      "methods": []
    },
    {
      "name": "_ReplayState",
      "bases": [],
      "lineno": 9874,
      "end_lineno": 9902,
      "span": 29,
      "doc": "Minimal State-alike for replay_capture: no persistent writes.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_ReplayState.__init__",
          "async": false,
          "lineno": 9883,
          "end_lineno": 9892,
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
          "lineno": 9894,
          "end_lineno": 9895,
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
          "lineno": 9897,
          "end_lineno": 9898,
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
          "lineno": 9900,
          "end_lineno": 9902,
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
      "lineno": 9905,
      "end_lineno": 9917,
      "span": 13,
      "doc": "Raised when State.__init__ cannot acquire the per-run-directory",
      "methods": [
        {
          "name": "__init__",
          "qualname": "StateLockedError.__init__",
          "async": false,
          "lineno": 9915,
          "end_lineno": 9917,
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
      "lineno": 9923,
      "end_lineno": 10052,
      "span": 130,
      "doc": "In-memory run state with atomic on-disk persistence.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "State.__init__",
          "async": false,
          "lineno": 9952,
          "end_lineno": 9972,
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
          "lineno": 9974,
          "end_lineno": 9988,
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
          "lineno": 9990,
          "end_lineno": 10005,
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
          "lineno": 10007,
          "end_lineno": 10018,
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
          "lineno": 10020,
          "end_lineno": 10024,
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
          "lineno": 10026,
          "end_lineno": 10035,
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
          "lineno": 10037,
          "end_lineno": 10045,
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
          "lineno": 10047,
          "end_lineno": 10052,
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
      "lineno": 10185,
      "end_lineno": 10230,
      "span": 46,
      "doc": "Persistent state for one heal-loop run scoped to a single call_type.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "HealState.__init__",
          "async": false,
          "lineno": 10198,
          "end_lineno": 10206,
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
          "lineno": 10208,
          "end_lineno": 10219,
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
          "lineno": 10221,
          "end_lineno": 10230,
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
      "log": 57,
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
      "_remap_vanished_deps": 4,
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
      "_cleanup_on_abnormal_exit": 2
    },
    "fan_out_top": {
      "main": 49,
      "_run_phases": 33,
      "phase_reconcile": 22,
      "run_final_conformance": 21,
      "settle_subtask": 18,
      "phase_plan": 15,
      "_run_conformance_phase": 15,
      "phase_provision": 14,
      "_compose_pr_via_llm": 12,
      "capture_repo_deps": 11,
      "phase_overlap_judge": 11,
      "integrate_wave": 11,
      "phase_finalize": 11,
      "_invoke": 10,
      "phase_execute": 9,
      "recursive_decompose": 8,
      "claude_p": 7,
      "phase_heal": 7,
      "preflight": 6,
      "phase_classify": 6,
      "run_implementer": 6,
      "run_conformer": 6,
      "report_run": 5,
      "_label_migration_chunks": 5,
      "filter_satisfied_subtasks": 5,
      "run_recapture_deps": 5,
      "_summarize_stream_event": 5,
      "_build_cycle_retry_prompt": 5,
      "_apply_overlap_collisions": 5,
      "capture_conformance_baseline": 5,
      "_DescendantTracker._poll_loop": 4,
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
        "recursive_decompose",
        "_remap_vanished_deps"
      ],
      "_remap_vanished_deps": [],
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
        "_remap_vanished_deps",
        "log"
      ],
      "filter_satisfied_subtasks": [
        "log",
        "load_prompt",
        "claude_p",
        "gather_or_cancel",
        "_remap_vanished_deps"
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
        "recursive_decompose",
        "_remap_vanished_deps"
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
      "_build_predecessor_graph": [
        "log"
      ],
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
        "recursive_decompose",
        "_remap_vanished_deps"
      ],
      "_remap_vanished_deps": [],
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
        "_remap_vanished_deps",
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
        "_remap_vanished_deps",
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
        "_remap_vanished_deps",
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
      "_build_predecessor_graph": [
        "log"
      ],
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
