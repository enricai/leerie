{
  "preprocess": {
    "path": "orchestrator/leerie.py",
    "sha256": "59e1ff7b81a0fa13233639098775c15d6d39cc2e1d3f2d594df6ea9b0effef43",
    "lines": 17411,
    "bytes": 818505,
    "module_doc": "Leerie \u2014 deterministic task orchestrator for Claude Code."
  },
  "lex": {
    "total_tokens": 93038,
    "token_class_counts": {
      "OP": 33567,
      "NAME": 27193,
      "NL": 7964,
      "NEWLINE": 6679,
      "STRING": 5612,
      "COMMENT": 3181,
      "INDENT": 2148,
      "DEDENT": 2148,
      "FSTRING_MIDDLE": 1807,
      "FSTRING_START": 1053,
      "FSTRING_END": 1053,
      "NUMBER": 632,
      "ENDMARKER": 1
    },
    "keyword_counts": {
      "if": 1193,
      "in": 757,
      "None": 667,
      "return": 582,
      "for": 560,
      "not": 493,
      "or": 416,
      "def": 332,
      "and": 194,
      "is": 179,
      "await": 162,
      "except": 157,
      "True": 152,
      "continue": 148,
      "try": 146,
      "else": 139,
      "False": 108,
      "async": 87,
      "raise": 57,
      "as": 55,
      "break": 41,
      "pass": 38,
      "elif": 35,
      "import": 27,
      "with": 19
    },
    "top_identifiers": {
      "str": 845,
      "get": 825,
      "dict": 454,
      "st": 425,
      "sid": 326,
      "s": 299,
      "list": 296,
      "repo_root": 296,
      "append": 291,
      "r": 283,
      "log": 206,
      "data": 206,
      "len": 192,
      "Path": 186,
      "e": 183,
      "caps": 176,
      "int": 167,
      "out": 142,
      "plans": 127,
      "set": 125,
      "args": 123,
      "plan": 116,
      "die": 112,
      "self": 109,
      "strip": 108,
      "parts": 99,
      "join": 97,
      "bool": 92,
      "entry": 92,
      "f": 91,
      "models": 91,
      "efforts": 91,
      "p": 90,
      "line": 86,
      "c": 81,
      "output": 81,
      "is_file": 74,
      "name": 73,
      "leerie_dir": 73,
      "run_id": 72,
      "tag": 70,
      "ValueError": 69,
      "cli_value": 69,
      "lines": 65,
      "save": 65,
      "v": 64,
      "asyncio": 63,
      "os": 63,
      "tuple": 62,
      "w": 62,
      "json": 61,
      "text": 61,
      "result": 60,
      "subtasks": 60,
      "OSError": 59,
      "run_dir": 59,
      "proc": 58,
      "i": 58,
      "isinstance": 57,
      "n": 57
    },
    "top_operators": {
      "(": 4956,
      ")": 4956,
      ",": 4410,
      ":": 4106,
      "=": 3086,
      ".": 3052,
      "[": 1980,
      "]": 1980,
      "{": 1771,
      "}": 1771,
      "->": 322,
      "/": 226,
      "|": 183,
      "==": 174,
      "!": 132,
      "+": 104,
      "!=": 59,
      "*": 56,
      ">": 47,
      "-": 47,
      "+=": 43,
      "<": 40,
      ">=": 23,
      "<=": 17,
      "**": 10
    },
    "comment_chars": 185637,
    "string_literal_count": 5612,
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
      "Load": 17593,
      "Name": 15231,
      "Constant": 8085,
      "Call": 4113,
      "Store": 3179,
      "Attribute": 3051,
      "Assign": 2018,
      "Expr": 1338,
      "Subscript": 1311,
      "FormattedValue": 1237,
      "If": 1046,
      "arg": 777,
      "keyword": 750,
      "Compare": 731,
      "JoinedStr": 703,
      "List": 584,
      "Return": 582,
      "Tuple": 577,
      "BinOp": 567,
      "BoolOp": 533,
      "Dict": 485,
      "For": 365,
      "Or": 361,
      "arguments": 350,
      "UnaryOp": 313,
      "Not": 283,
      "FunctionDef": 254,
      "AnnAssign": 227,
      "Div": 226,
      "comprehension": 192,
      "BitOr": 184,
      "Eq": 174,
      "And": 172,
      "Await": 162,
      "ExceptHandler": 157,
      "Continue": 148,
      "Add": 147,
      "Try": 146,
      "IsNot": 127,
      "In": 114,
      "IfExp": 90,
      "ListComp": 85,
      "NotIn": 83,
      "AsyncFunctionDef": 78,
      "GeneratorExp": 72,
      "NotEq": 59,
      "Raise": 57,
      "Is": 52,
      "AugAssign": 48,
      "Slice": 48,
      "Gt": 47,
      "Break": 41,
      "Lt": 40,
      "Pass": 38,
      "alias": 37,
      "USub": 30,
      "Mult": 23,
      "GtE": 23,
      "Import": 20,
      "Starred": 20,
      "withitem": 19,
      "Sub": 19,
      "SetComp": 18,
      "Lambda": 18,
      "While": 17,
      "LtE": 17,
      "DictComp": 16,
      "Set": 15,
      "With": 13,
      "ClassDef": 10,
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
    "constant_count": 174,
    "function_count": 269,
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
      "lineno": 208,
      "kind": "tuple",
      "len": 39,
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
        "leerie_version",
        "dep_capture_done"
      ]
    },
    {
      "name": "CATEGORIES",
      "lineno": 298,
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
      "lineno": 307,
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
      "lineno": 333,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".leerie/",
        ".git/"
      ]
    },
    {
      "name": "_CLAUDE_DELIVERABLE_PREFIXES",
      "lineno": 334,
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
      "lineno": 349,
      "kind": "str",
      "value": "Read,Grep,Glob,WebSearch,WebFetch"
    },
    {
      "name": "INSPECT_TOOLS",
      "lineno": 373,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(find:*),Bash(cat:*),Bash(head:*),Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(tree:*),Bash(pwd),Bash(echo:*),Bash(git log:*),Bash(git s"
    },
    {
      "name": "ACT_TOOLS",
      "lineno": 381,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash,Write,Edit'"
    },
    {
      "name": "SATISFIED_PROBE_TOOLS",
      "lineno": 395,
      "kind": "expr",
      "expr": "f'{_READ_BASE},Bash(ls:*),Bash(cat:*),Bash(head:*),Bash(wc:*),Bash(grep:*),Bash(rg:*),Bash(file:*),Bash(stat:*),Bash(pwd),Bash(echo:*),Bash(git show HEAD:*),Bash(git diff:*),Bash(git status)'"
    },
    {
      "name": "DISALLOWED_TOOLS",
      "lineno": 409,
      "kind": "str",
      "value": "Agent,SendMessage,ScheduleWakeup,CronCreate,CronDelete,CronList,RemoteTrigger,PushNotification"
    },
    {
      "name": "INSPECT_DIRS_ENV",
      "lineno": 423,
      "kind": "str",
      "value": "LEERIE_INSPECT_DIRS"
    },
    {
      "name": "INSPECT_DIRS_FILE",
      "lineno": 424,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EXIT_NEEDS_ANSWERS",
      "lineno": 426,
      "kind": "int",
      "value": 10
    },
    {
      "name": "EXIT_BUDGET_INFEASIBLE",
      "lineno": 437,
      "kind": "int",
      "value": 11
    },
    {
      "name": "EXIT_LOCKED",
      "lineno": 445,
      "kind": "int",
      "value": 75
    },
    {
      "name": "RATE_LIMIT_RETRY_BACKOFF_SEC",
      "lineno": 454,
      "kind": "int",
      "value": 300
    },
    {
      "name": "SOURCE_OF_TRUTH_VALUES",
      "lineno": 462,
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
      "lineno": 463,
      "kind": "str",
      "value": "LEERIE_SOURCE_OF_TRUTH"
    },
    {
      "name": "SOURCE_OF_TRUTH_FILE",
      "lineno": 464,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "RUNTIME_VALUES",
      "lineno": 469,
      "kind": "tuple",
      "len": 2,
      "values": [
        "local",
        "fly"
      ]
    },
    {
      "name": "RUNTIME_ENV",
      "lineno": 470,
      "kind": "str",
      "value": "LEERIE_RUNTIME"
    },
    {
      "name": "RUNTIME_FILE",
      "lineno": 471,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CONFIDENCE_ROUNDS_ENV",
      "lineno": 478,
      "kind": "str",
      "value": "LEERIE_CONFIDENCE_ROUNDS"
    },
    {
      "name": "CONFIDENCE_ROUNDS_FILE",
      "lineno": 479,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "JUDGMENT_CHECK_ROUNDS_ENV",
      "lineno": 482,
      "kind": "str",
      "value": "LEERIE_JUDGMENT_CHECK_ROUNDS"
    },
    {
      "name": "PLANNER_CHECK_ROUNDS_ENV",
      "lineno": 483,
      "kind": "str",
      "value": "LEERIE_PLANNER_CHECK_ROUNDS"
    },
    {
      "name": "IMPLEMENTER_CONFIDENCE_RETRIES_ENV",
      "lineno": 484,
      "kind": "str",
      "value": "LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES"
    },
    {
      "name": "PLANNER_SAMPLES_ENV",
      "lineno": 485,
      "kind": "str",
      "value": "LEERIE_PLANNER_SAMPLES"
    },
    {
      "name": "MAX_WORKERS_ENV",
      "lineno": 490,
      "kind": "str",
      "value": "LEERIE_MAX_WORKERS"
    },
    {
      "name": "MAX_WORKERS_FILE",
      "lineno": 491,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "MAX_PARALLEL_ENV",
      "lineno": 496,
      "kind": "str",
      "value": "LEERIE_MAX_PARALLEL"
    },
    {
      "name": "MAX_PARALLEL_FILE",
      "lineno": 497,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "WORKER_MEMORY_MAX_ENV",
      "lineno": 504,
      "kind": "str",
      "value": "LEERIE_WORKER_MEMORY_MAX"
    },
    {
      "name": "WORKER_MEMORY_MAX_FILE",
      "lineno": 505,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "WORKER_PIDS_MAX_ENV",
      "lineno": 511,
      "kind": "str",
      "value": "LEERIE_WORKER_PIDS_MAX"
    },
    {
      "name": "WORKER_PIDS_MAX_FILE",
      "lineno": 512,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "NO_PUSH_ENV",
      "lineno": 520,
      "kind": "str",
      "value": "LEERIE_NO_PUSH"
    },
    {
      "name": "NO_PUSH_FILE",
      "lineno": 521,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CLARIFY_ENV",
      "lineno": 529,
      "kind": "str",
      "value": "LEERIE_CLARIFY"
    },
    {
      "name": "CLARIFY_FILE",
      "lineno": 530,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_ENV",
      "lineno": 540,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_SKIP_PERMISSIONS"
    },
    {
      "name": "DANGEROUS_SKIP_PERMS_FILE",
      "lineno": 541,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_ENV",
      "lineno": 555,
      "kind": "str",
      "value": "LEERIE_DANGEROUSLY_ALLOW_UNCAPPED"
    },
    {
      "name": "DANGEROUS_ALLOW_UNCAPPED_FILE",
      "lineno": 556,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_ENV",
      "lineno": 565,
      "kind": "str",
      "value": "LEERIE_SKIP_OVERLAP_JUDGE"
    },
    {
      "name": "SKIP_OVERLAP_JUDGE_FILE",
      "lineno": 566,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "SKIP_BUDGET_CHECK_ENV",
      "lineno": 578,
      "kind": "str",
      "value": "LEERIE_SKIP_BUDGET_CHECK"
    },
    {
      "name": "SKIP_BUDGET_CHECK_FILE",
      "lineno": 579,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "STRICT_CONFORMER_ENV",
      "lineno": 581,
      "kind": "str",
      "value": "LEERIE_STRICT_CONFORMER"
    },
    {
      "name": "STRICT_CONFORMER_FILE",
      "lineno": 582,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "CAPTURE_DEPS_ENV",
      "lineno": 588,
      "kind": "str",
      "value": "LEERIE_CAPTURE_DEPS"
    },
    {
      "name": "CAPTURE_DEPS_CONFIG",
      "lineno": 589,
      "kind": "str",
      "value": ".leerie/config.toml"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_ENV",
      "lineno": 600,
      "kind": "str",
      "value": "LEERIE_SKIP_SATISFIED_CHECK"
    },
    {
      "name": "SKIP_SATISFIED_CHECK_FILE",
      "lineno": 601,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "PR_TEMPLATE_ENV",
      "lineno": 610,
      "kind": "str",
      "value": "LEERIE_PR_TEMPLATE"
    },
    {
      "name": "PR_TEMPLATE_FILE",
      "lineno": 611,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "VERBOSITY_VALUES",
      "lineno": 619,
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
      "lineno": 620,
      "kind": "str",
      "value": "stream"
    },
    {
      "name": "VERBOSITY_ENV",
      "lineno": 621,
      "kind": "str",
      "value": "LEERIE_VERBOSITY"
    },
    {
      "name": "VERBOSITY_FILE",
      "lineno": 622,
      "kind": "expr",
      "expr": "SOURCE_OF_TRUTH_FILE"
    },
    {
      "name": "_TERMINAL_STATUSES",
      "lineno": 625,
      "kind": "expr",
      "expr": "frozenset({'complete', 'failed', 'blocked'})"
    },
    {
      "name": "MODEL_VALUES",
      "lineno": 631,
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
      "lineno": 638,
      "kind": "str",
      "value": "opus"
    },
    {
      "name": "MODEL_DEFAULT_PER_WORKER",
      "lineno": 642,
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
      "lineno": 654,
      "kind": "str",
      "value": "LEERIE_MODEL"
    },
    {
      "name": "MODEL_FILE",
      "lineno": 655,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "EFFORT_VALUES",
      "lineno": 663,
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
      "lineno": 664,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "EFFORT_DEFAULT_PER_WORKER",
      "lineno": 665,
      "kind": "dict",
      "len": 8,
      "keys": [
        "classifier",
        "planner",
        "reconciler",
        "plan_overlap_judge",
        "provision",
        "integrator",
        "pr_writer",
        "dep_capture"
      ]
    },
    {
      "name": "EFFORT_ENV",
      "lineno": 675,
      "kind": "str",
      "value": "LEERIE_EFFORT"
    },
    {
      "name": "WORKER_TYPES",
      "lineno": 676,
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
      "lineno": 682,
      "kind": "str",
      "value": "LEERIE_MODEL_JUDGE"
    },
    {
      "name": "MODEL_HEAL_ENV",
      "lineno": 683,
      "kind": "str",
      "value": "LEERIE_MODEL_HEAL"
    },
    {
      "name": "MODEL_PR_WRITER_ENV",
      "lineno": 684,
      "kind": "str",
      "value": "LEERIE_MODEL_PR_WRITER"
    },
    {
      "name": "MODEL_DEP_CAPTURE_ENV",
      "lineno": 685,
      "kind": "str",
      "value": "LEERIE_MODEL_DEP_CAPTURE"
    },
    {
      "name": "TELEMETRY_DEFAULT",
      "lineno": 692,
      "kind": "bool",
      "value": true
    },
    {
      "name": "TELEMETRY_ENV",
      "lineno": 693,
      "kind": "str",
      "value": "LEERIE_TELEMETRY"
    },
    {
      "name": "TELEMETRY_FILE",
      "lineno": 694,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "TELEMETRY_SUBDIR_DEFAULT",
      "lineno": 699,
      "kind": "str",
      "value": "events"
    },
    {
      "name": "TELEMETRY_SUBDIR_ENV",
      "lineno": 700,
      "kind": "str",
      "value": "LEERIE_TELEMETRY_DIR"
    },
    {
      "name": "TELEMETRY_SUBDIR_FILE",
      "lineno": 701,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "JUDGE_DIR_DEFAULT",
      "lineno": 706,
      "kind": "str",
      "value": "judge-out"
    },
    {
      "name": "JUDGE_DIR_ENV",
      "lineno": 707,
      "kind": "str",
      "value": "LEERIE_JUDGE_DIR"
    },
    {
      "name": "JUDGE_DIR_FILE",
      "lineno": 708,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_DIR_DEFAULT",
      "lineno": 713,
      "kind": "str",
      "value": "heal-out"
    },
    {
      "name": "HEAL_DIR_ENV",
      "lineno": 714,
      "kind": "str",
      "value": "LEERIE_HEAL_DIR"
    },
    {
      "name": "HEAL_DIR_FILE",
      "lineno": 715,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_MAX_ROUNDS_DEFAULT",
      "lineno": 720,
      "kind": "int",
      "value": 10
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_DEFAULT",
      "lineno": 721,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "HEAL_PLATEAU_WINDOW_DEFAULT",
      "lineno": 722,
      "kind": "int",
      "value": 3
    },
    {
      "name": "HEAL_PLATEAU_DELTA_DEFAULT",
      "lineno": 723,
      "kind": "float",
      "value": 0.03
    },
    {
      "name": "HEAL_N_REPLAYS_DEFAULT",
      "lineno": 724,
      "kind": "int",
      "value": 5
    },
    {
      "name": "HEAL_MAX_ROUNDS_ENV",
      "lineno": 725,
      "kind": "str",
      "value": "LEERIE_HEAL_MAX_ROUNDS"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_ENV",
      "lineno": 726,
      "kind": "str",
      "value": "LEERIE_HEAL_SUCCESS_THRESHOLD"
    },
    {
      "name": "HEAL_MAX_ROUNDS_FILE",
      "lineno": 727,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "HEAL_SUCCESS_THRESHOLD_FILE",
      "lineno": 728,
      "kind": "str",
      "value": "leerie.toml"
    },
    {
      "name": "STATE_DIR_ENV",
      "lineno": 736,
      "kind": "str",
      "value": "LEERIE_STATE_DIR"
    },
    {
      "name": "_CONFORMER_BLT_PROP",
      "lineno": 769,
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
      "lineno": 788,
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
      "lineno": 824,
      "kind": "dict",
      "len": 13,
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
        "satisfied_probe"
      ]
    },
    {
      "name": "_SESSION_LIMIT_PREFIX",
      "lineno": 1576,
      "kind": "expr",
      "expr": "re.compile(\"you've hit your session limit\", re.IGNORECASE)"
    },
    {
      "name": "_SESSION_LIMIT_RESET",
      "lineno": 1578,
      "kind": "expr",
      "expr": "re.compile('resets?\\\\s+(\\\\d{1,2}):(\\\\d{2})\\\\s*([ap]m)\\\\s*\\\\(([^)]+)\\\\)', re.IGNORECASE)"
    },
    {
      "name": "_RATE_LIMIT_ALLOWED_STATUSES",
      "lineno": 1587,
      "kind": "tuple",
      "len": 2,
      "values": [
        "allowed",
        "allowed_warning"
      ]
    },
    {
      "name": "_PROC_TREE_GRACE_SEC",
      "lineno": 1645,
      "kind": "float",
      "value": 2.0
    },
    {
      "name": "_DESCENDANT_POLL_SEC",
      "lineno": 1792,
      "kind": "float",
      "value": 0.5
    },
    {
      "name": "_PID_REAP_HIGH_WATER",
      "lineno": 1799,
      "kind": "float",
      "value": 0.9
    },
    {
      "name": "_PID_REAP_LOW_WATER",
      "lineno": 1800,
      "kind": "float",
      "value": 0.75
    },
    {
      "name": "_PID_REAP_MIN_AGE_SEC",
      "lineno": 1801,
      "kind": "int",
      "value": 60
    },
    {
      "name": "_PID_EXHAUSTION_WINDOW",
      "lineno": 1816,
      "kind": "int",
      "value": 6
    },
    {
      "name": "_PID_EXHAUSTION_ERROR_THRESHOLD",
      "lineno": 1817,
      "kind": "int",
      "value": 3
    },
    {
      "name": "RUN_STATUSES",
      "lineno": 2755,
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
      "lineno": 2959,
      "kind": "tuple",
      "len": 2,
      "values": [
        ".txt",
        ".md"
      ]
    },
    {
      "name": "_MEMORY_SUFFIX_MULTIPLIER",
      "lineno": 3216,
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
      "lineno": 4163,
      "kind": "expr",
      "expr": "frozenset((f'{v}-' for v in CATEGORY_ABBREV.values()))"
    },
    {
      "name": "_VALID_EXTENTS",
      "lineno": 4166,
      "kind": "expr",
      "expr": "frozenset({'in_plan', 'external'})"
    },
    {
      "name": "_MIGRATION_SIGNAL_RE",
      "lineno": 4249,
      "kind": "expr",
      "expr": "re.compile('replac(?:es?|ing)\\\\s+(?:direct\\\\s+)?[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|migrat(?:es?|ing)\\\\s+from\\\\s+[`\\'\\\\\"]?([a-zA-Z_][a-zA-Z0-9_.]*)[`\\'\\\\\"]?|extract(?:s|ing)\\\\s+[`\\'\\\\\"]?([a-zA"
    },
    {
      "name": "_MIGRATION_SURFACE_THRESHOLD",
      "lineno": 4257,
      "kind": "int",
      "value": 5
    },
    {
      "name": "_GLOB_CHARS",
      "lineno": 4600,
      "kind": "expr",
      "expr": "frozenset('*?[{')"
    },
    {
      "name": "_BRACE_RE",
      "lineno": 4601,
      "kind": "expr",
      "expr": "re.compile('\\\\{([^}]+)\\\\}')"
    },
    {
      "name": "_MAX_COVERAGE_ITEMS",
      "lineno": 4689,
      "kind": "int",
      "value": 50
    },
    {
      "name": "_ENV_TAG_KEYWORDS",
      "lineno": 4883,
      "kind": "expr",
      "expr": "frozenset({'env', 'bootstrap', 'secret', 'config-key', 'credential'})"
    },
    {
      "name": "_PROVISION_ARGV0_ALLOW",
      "lineno": 5163,
      "kind": "expr",
      "expr": "frozenset({'pnpm', 'npm', 'yarn', 'pip', 'pip3', 'uv', 'poetry', 'pipenv', 'go', 'cargo', 'bundle', 'gem', 'mvn', 'gradle', 'gradlew', 'make', 'composer', 'dotnet'})"
    },
    {
      "name": "_PROVISION_SHELL_METACHARS",
      "lineno": 5174,
      "kind": "expr",
      "expr": "frozenset(set('|&;$`><\\n\\r'))"
    },
    {
      "name": "_README_SECTION_RE",
      "lineno": 5371,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(install|getting[\\\\s-]?started|quick[\\\\s-]?start|setup|usage|\\\\brun\\\\b|develop|build(ing)?( from source| instructions)?|compil(e|ing)( from source)?|download|from source|requirement"
    },
    {
      "name": "_HEADER_DECOR_RE",
      "lineno": 5396,
      "kind": "expr",
      "expr": "re.compile('^[^\\\\w]+', flags=re.UNICODE)"
    },
    {
      "name": "_INSTALL_CMD_HINT_RE",
      "lineno": 5401,
      "kind": "expr",
      "expr": "re.compile('\\\\b(pip|pip3|npm|pnpm|yarn|uv|poetry|cargo|brew|apt|apt-get|dnf|yum|pacman|go install|make|bundle install|gem install|mise install)\\\\b')"
    },
    {
      "name": "_DEPCAP_TOTAL_BUDGET",
      "lineno": 5412,
      "kind": "int",
      "value": 307200
    },
    {
      "name": "_DEP_MANIFEST_NAMES",
      "lineno": 5423,
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
      "lineno": 5430,
      "kind": "int",
      "value": 16384
    },
    {
      "name": "_DEPCAP_MANIFEST_TOTAL_BUDGET",
      "lineno": 5431,
      "kind": "int",
      "value": 131072
    },
    {
      "name": "_DEPCAP_INSTALL_RE",
      "lineno": 5438,
      "kind": "expr",
      "expr": "re.compile('(?:^|[\\\\s;&|(])(?:sudo\\\\s+)?(?:apt-get\\\\s+install|apt\\\\s+install|yum\\\\s+install|dnf\\\\s+install|apk\\\\s+add|pip3?\\\\s+install|pipx\\\\s+install|poetry\\\\s+add|uv\\\\s+(?:add|pip\\\\s+install)|npm\\\\s"
    },
    {
      "name": "_DEPCAP_TEXT_TOOLS",
      "lineno": 5449,
      "kind": "expr",
      "expr": "frozenset({'grep', 'rg', 'git', 'sed', 'awk', 'echo', 'cat', 'printf', 'ag', 'ack'})"
    },
    {
      "name": "_DEPCAP_SEGMENT_RE",
      "lineno": 5456,
      "kind": "expr",
      "expr": "re.compile('[\\\\n;]|&&|\\\\|\\\\||[|&]')"
    },
    {
      "name": "_README_INTRO_BUDGET",
      "lineno": 6063,
      "kind": "int",
      "value": 1024
    },
    {
      "name": "_README_EXTRACT_BUDGET",
      "lineno": 6064,
      "kind": "int",
      "value": 8192
    },
    {
      "name": "_README_FALLBACK_BUDGET",
      "lineno": 6065,
      "kind": "int",
      "value": 6144
    },
    {
      "name": "_FIXTURE_TOTAL_BUDGET",
      "lineno": 6066,
      "kind": "int",
      "value": 24576
    },
    {
      "name": "_PROVISION_ROOT_MANIFESTS",
      "lineno": 6140,
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
      "lineno": 6145,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(ci|test|build|release)\\\\b')"
    },
    {
      "name": "_PROVISION_WORKFLOW_SKIP_RE",
      "lineno": 6146,
      "kind": "expr",
      "expr": "re.compile('(?i)\\\\b(codeql|stale|dependabot)\\\\b')"
    },
    {
      "name": "_CHECKPOINT_SECTIONS",
      "lineno": 6302,
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
      "lineno": 6315,
      "kind": "set",
      "len": 2,
      "values": [
        "## Decisions made",
        "## Open unknowns"
      ]
    },
    {
      "name": "_NOISE_TOKENS",
      "lineno": 6323,
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
      "lineno": 6768,
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
      "lineno": 7156,
      "kind": "str",
      "value": "/run/leerie-cgroup.sock"
    },
    {
      "name": "_CGROUP_PROBE_RESULT",
      "lineno": 7157,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_CGROUP_HIERARCHY",
      "lineno": 7158,
      "kind": "NoneType",
      "value": null
    },
    {
      "name": "_PR_SET_CHILD_SUBREAPER",
      "lineno": 7875,
      "kind": "int",
      "value": 36
    },
    {
      "name": "_PR_GET_CHILD_SUBREAPER",
      "lineno": 7876,
      "kind": "int",
      "value": 37
    },
    {
      "name": "_ASYNCIO_MANAGED_PIDS",
      "lineno": 7888,
      "kind": "expr",
      "expr": "set()"
    },
    {
      "name": "_DOCS_ONLY_CATEGORIES",
      "lineno": 9269,
      "kind": "expr",
      "expr": "frozenset({'documentation'})"
    },
    {
      "name": "_GO_MOD_VERSION_RE",
      "lineno": 9346,
      "kind": "expr",
      "expr": "re.compile('^\\\\s*go\\\\s+(\\\\d+(?:\\\\.\\\\d+){0,2})\\\\s*$', re.MULTILINE)"
    },
    {
      "name": "_LEADING_V_RE",
      "lineno": 9401,
      "kind": "expr",
      "expr": "re.compile('^[vV]+')"
    },
    {
      "name": "_IDIOMATIC_VERSION_FILES",
      "lineno": 9412,
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
      "lineno": 9427,
      "kind": "dict",
      "len": 2,
      "keys": [
        "nodejs",
        "python3"
      ]
    },
    {
      "name": "_MISE_SIGNAL_FILES",
      "lineno": 9654,
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
      "lineno": 14141,
      "kind": "expr",
      "expr": "frozenset({'no_commits', 'dirty_worktree', 'empty_handoff'})"
    },
    {
      "name": "_RULES_FILE_CANDIDATES",
      "lineno": 14191,
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
      "lineno": 14730,
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
      "lineno": 14754,
      "kind": "str",
      "value": "Command running in background with ID:"
    },
    {
      "name": "_BG_ID_RE",
      "lineno": 14755,
      "kind": "expr",
      "expr": "re.compile('Command running in background with ID:\\\\s*(\\\\w+)')"
    },
    {
      "name": "_PR_TEMPLATE_SINGLE_LOCATIONS",
      "lineno": 15753,
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
      "lineno": 15761,
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
      "lineno": 15822,
      "kind": "int",
      "value": 80000
    },
    {
      "name": "PR_WRITER_TEMPLATE_MAX_BYTES",
      "lineno": 15823,
      "kind": "int",
      "value": 32000
    },
    {
      "name": "PR_WRITER_DIFF_SAMPLE_MAX_LINES",
      "lineno": 15824,
      "kind": "int",
      "value": 500
    },
    {
      "name": "PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES",
      "lineno": 15830,
      "kind": "int",
      "value": 8000
    },
    {
      "name": "_LEERIE_PREFIX_RE",
      "lineno": 15858,
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
      "lineno": 339,
      "end_lineno": 347,
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
      "lineno": 741,
      "end_lineno": 756,
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
      "lineno": 799,
      "end_lineno": 822,
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
      "lineno": 1497,
      "end_lineno": 1498,
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
      "lineno": 1501,
      "end_lineno": 1504,
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
      "lineno": 1507,
      "end_lineno": 1509,
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
      "lineno": 1590,
      "end_lineno": 1629,
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
      "lineno": 1632,
      "end_lineno": 1642,
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
      "lineno": 1648,
      "end_lineno": 1687,
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
      "lineno": 1690,
      "end_lineno": 1704,
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
      "lineno": 1707,
      "end_lineno": 1746,
      "span": 40,
      "decorators": [],
      "signature": "(seen: set[int])",
      "returns": "list[int]",
      "doc": "Return PIDs from `seen` that are currently alive, reparented to"
    },
    {
      "name": "_exit_reap_candidates",
      "qualname": "_exit_reap_candidates",
      "async": false,
      "lineno": 1749,
      "end_lineno": 1789,
      "span": 41,
      "decorators": [],
      "signature": "(seen: set[int], exclude: set[int])",
      "returns": "set[int]",
      "doc": "Return PIDs from `seen` that are safe to SIGKILL at worker exit:"
    },
    {
      "name": "_terminate_proc_tree",
      "qualname": "_terminate_proc_tree",
      "async": true,
      "lineno": 1950,
      "end_lineno": 2018,
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
      "lineno": 2021,
      "end_lineno": 2136,
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
      "lineno": 2139,
      "end_lineno": 2162,
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
      "lineno": 2165,
      "end_lineno": 2235,
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
      "lineno": 2238,
      "end_lineno": 2243,
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
      "lineno": 2246,
      "end_lineno": 2276,
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
      "lineno": 2290,
      "end_lineno": 2301,
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
      "lineno": 2304,
      "end_lineno": 2313,
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
      "lineno": 2318,
      "end_lineno": 2414,
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
      "lineno": 2419,
      "end_lineno": 2440,
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
      "lineno": 2443,
      "end_lineno": 2514,
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
      "lineno": 2517,
      "end_lineno": 2542,
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
      "lineno": 2547,
      "end_lineno": 2634,
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
      "lineno": 2637,
      "end_lineno": 2671,
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
      "lineno": 2674,
      "end_lineno": 2719,
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
      "lineno": 2722,
      "end_lineno": 2739,
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
      "lineno": 2771,
      "end_lineno": 2858,
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
      "lineno": 2861,
      "end_lineno": 2886,
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
      "lineno": 2889,
      "end_lineno": 2899,
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
      "lineno": 2902,
      "end_lineno": 2936,
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
      "lineno": 2940,
      "end_lineno": 2956,
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
      "lineno": 2962,
      "end_lineno": 2990,
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
      "lineno": 2993,
      "end_lineno": 3008,
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
      "lineno": 3011,
      "end_lineno": 3030,
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
      "lineno": 3033,
      "end_lineno": 3045,
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
      "lineno": 3048,
      "end_lineno": 3059,
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
      "lineno": 3062,
      "end_lineno": 3076,
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
      "lineno": 3079,
      "end_lineno": 3091,
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
      "lineno": 3094,
      "end_lineno": 3120,
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
      "lineno": 3123,
      "end_lineno": 3135,
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
      "lineno": 3138,
      "end_lineno": 3150,
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
      "lineno": 3153,
      "end_lineno": 3165,
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
      "lineno": 3168,
      "end_lineno": 3177,
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
      "lineno": 3180,
      "end_lineno": 3189,
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
      "lineno": 3192,
      "end_lineno": 3202,
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
      "lineno": 3205,
      "end_lineno": 3213,
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
      "lineno": 3221,
      "end_lineno": 3245,
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
      "lineno": 3248,
      "end_lineno": 3273,
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
      "lineno": 3276,
      "end_lineno": 3296,
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
      "lineno": 3299,
      "end_lineno": 3311,
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
      "lineno": 3314,
      "end_lineno": 3352,
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
      "lineno": 3355,
      "end_lineno": 3367,
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
      "lineno": 3370,
      "end_lineno": 3397,
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
      "lineno": 3400,
      "end_lineno": 3407,
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
      "lineno": 3410,
      "end_lineno": 3434,
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
      "lineno": 3437,
      "end_lineno": 3444,
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
      "lineno": 3447,
      "end_lineno": 3466,
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
      "lineno": 3469,
      "end_lineno": 3484,
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
      "lineno": 3487,
      "end_lineno": 3504,
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
      "lineno": 3507,
      "end_lineno": 3526,
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
      "lineno": 3529,
      "end_lineno": 3548,
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
      "lineno": 3551,
      "end_lineno": 3565,
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
      "lineno": 3568,
      "end_lineno": 3577,
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
      "lineno": 3580,
      "end_lineno": 3599,
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
      "lineno": 3602,
      "end_lineno": 3620,
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
      "lineno": 3623,
      "end_lineno": 3704,
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
      "lineno": 3707,
      "end_lineno": 3768,
      "span": 62,
      "decorators": [],
      "signature": "(repo_root: Path, args)",
      "returns": "dict[str, str | None]",
      "doc": "Resolve the --effort value for each worker type. Mirrors"
    },
    {
      "name": "resolve_telemetry_enabled",
      "qualname": "resolve_telemetry_enabled",
      "async": false,
      "lineno": 3771,
      "end_lineno": 3800,
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
      "lineno": 3803,
      "end_lineno": 3813,
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
      "lineno": 3816,
      "end_lineno": 3825,
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
      "lineno": 3828,
      "end_lineno": 3837,
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
      "lineno": 3840,
      "end_lineno": 3849,
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
      "lineno": 3852,
      "end_lineno": 3879,
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
      "lineno": 3882,
      "end_lineno": 3926,
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
      "lineno": 3929,
      "end_lineno": 4052,
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
      "lineno": 4055,
      "end_lineno": 4076,
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
      "lineno": 4079,
      "end_lineno": 4081,
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
      "lineno": 4094,
      "end_lineno": 4160,
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
      "lineno": 4175,
      "end_lineno": 4193,
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
      "lineno": 4196,
      "end_lineno": 4242,
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
      "lineno": 4260,
      "end_lineno": 4282,
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
      "lineno": 4285,
      "end_lineno": 4313,
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
      "lineno": 4316,
      "end_lineno": 4418,
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
      "lineno": 4421,
      "end_lineno": 4452,
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
      "lineno": 4455,
      "end_lineno": 4516,
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
      "lineno": 4519,
      "end_lineno": 4558,
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
      "lineno": 4561,
      "end_lineno": 4564,
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
      "lineno": 4567,
      "end_lineno": 4589,
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
      "lineno": 4604,
      "end_lineno": 4617,
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
      "lineno": 4620,
      "end_lineno": 4649,
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
      "lineno": 4652,
      "end_lineno": 4686,
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
      "lineno": 4692,
      "end_lineno": 4723,
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
      "lineno": 4726,
      "end_lineno": 4737,
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
      "lineno": 4740,
      "end_lineno": 4846,
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
      "lineno": 4849,
      "end_lineno": 4880,
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
      "lineno": 4887,
      "end_lineno": 4925,
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
      "lineno": 4928,
      "end_lineno": 4940,
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
      "lineno": 4943,
      "end_lineno": 5000,
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
      "lineno": 5003,
      "end_lineno": 5151,
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
      "lineno": 5177,
      "end_lineno": 5290,
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
      "lineno": 5293,
      "end_lineno": 5298,
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
      "lineno": 5301,
      "end_lineno": 5362,
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
      "lineno": 5459,
      "end_lineno": 5482,
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
      "lineno": 5485,
      "end_lineno": 5511,
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
      "lineno": 5514,
      "end_lineno": 5518,
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
      "lineno": 5521,
      "end_lineno": 5538,
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
      "lineno": 5541,
      "end_lineno": 5552,
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
      "lineno": 5555,
      "end_lineno": 5569,
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
      "lineno": 5572,
      "end_lineno": 5611,
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
      "lineno": 5614,
      "end_lineno": 5640,
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
      "lineno": 5643,
      "end_lineno": 5666,
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
      "lineno": 5669,
      "end_lineno": 5830,
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
      "lineno": 5833,
      "end_lineno": 5868,
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
      "lineno": 5871,
      "end_lineno": 5953,
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
      "lineno": 5956,
      "end_lineno": 6005,
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
      "lineno": 6008,
      "end_lineno": 6015,
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
      "lineno": 6018,
      "end_lineno": 6057,
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
      "lineno": 6069,
      "end_lineno": 6126,
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
      "lineno": 6129,
      "end_lineno": 6136,
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
      "lineno": 6149,
      "end_lineno": 6186,
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
      "lineno": 6189,
      "end_lineno": 6297,
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
      "lineno": 6330,
      "end_lineno": 6346,
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
      "lineno": 6349,
      "end_lineno": 6398,
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
      "lineno": 6401,
      "end_lineno": 6413,
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
      "lineno": 6416,
      "end_lineno": 6430,
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
      "lineno": 6433,
      "end_lineno": 6453,
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
      "lineno": 6458,
      "end_lineno": 6526,
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
      "lineno": 6531,
      "end_lineno": 6574,
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
      "lineno": 6579,
      "end_lineno": 6602,
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
      "lineno": 6605,
      "end_lineno": 6618,
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
      "lineno": 6623,
      "end_lineno": 6648,
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
      "lineno": 6653,
      "end_lineno": 6671,
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
      "lineno": 6676,
      "end_lineno": 6701,
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
      "lineno": 6711,
      "end_lineno": 6742,
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
      "lineno": 6745,
      "end_lineno": 6758,
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
      "lineno": 6776,
      "end_lineno": 6780,
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
      "lineno": 6783,
      "end_lineno": 6806,
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
      "lineno": 6809,
      "end_lineno": 6844,
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
      "lineno": 6847,
      "end_lineno": 6886,
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
      "lineno": 6889,
      "end_lineno": 7053,
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
      "lineno": 7056,
      "end_lineno": 7079,
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
      "lineno": 7082,
      "end_lineno": 7125,
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
      "lineno": 7161,
      "end_lineno": 7169,
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
      "lineno": 7172,
      "end_lineno": 7201,
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
      "lineno": 7204,
      "end_lineno": 7223,
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
      "lineno": 7226,
      "end_lineno": 7239,
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
      "lineno": 7242,
      "end_lineno": 7249,
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
      "lineno": 7252,
      "end_lineno": 7276,
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
      "lineno": 7279,
      "end_lineno": 7328,
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
      "lineno": 7331,
      "end_lineno": 7804,
      "span": 474,
      "decorators": [],
      "signature": "(cmd: list[str], cwd: str, timeout: int, sid: str, leerie_dir: Path, verbosity: str, progress: Callable[[], tuple[int, int, int, int] | None] | None = None, idle_warn_sec: float | None = None, worker_memory_max_bytes: int | None = None, worker_pids_max: int | None = None)",
      "returns": "dict",
      "doc": "Run a `claude -p` command, streaming events as they arrive."
    },
    {
      "name": "_capture_call",
      "qualname": "_capture_call",
      "async": false,
      "lineno": 7807,
      "end_lineno": 7817,
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
      "lineno": 7820,
      "end_lineno": 7869,
      "span": 50,
      "decorators": [],
      "signature": "(st: 'State')",
      "returns": "dict",
      "doc": "Snapshot orchestrator RSS / current phase / worker count / open FDs /"
    },
    {
      "name": "_become_subreaper",
      "qualname": "_become_subreaper",
      "async": false,
      "lineno": 7891,
      "end_lineno": 7924,
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
      "lineno": 7927,
      "end_lineno": 7964,
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
      "lineno": 7967,
      "end_lineno": 7994,
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
      "lineno": 7997,
      "end_lineno": 8039,
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
      "lineno": 8042,
      "end_lineno": 8311,
      "span": 270,
      "decorators": [],
      "signature": "(user_prompt: str, system_prompt: str, *, schema_key: str, cwd: str, allowed_tools: str, max_turns: int, autonomous: bool, caps: dict, st: 'State', model: str, sid: str, add_dirs: list[str] | None = None, effort: str | None = None, _suppress_capture: bool = False)",
      "returns": "dict",
      "doc": "Run one headless Claude Code worker and return its validated"
    },
    {
      "name": "replay_capture",
      "qualname": "replay_capture",
      "async": true,
      "lineno": 8314,
      "end_lineno": 8378,
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
      "lineno": 8381,
      "end_lineno": 8391,
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
      "lineno": 8579,
      "end_lineno": 8627,
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
      "lineno": 8630,
      "end_lineno": 8698,
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
      "lineno": 8753,
      "end_lineno": 8832,
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
      "lineno": 8835,
      "end_lineno": 8858,
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
      "lineno": 8861,
      "end_lineno": 8960,
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
      "lineno": 8963,
      "end_lineno": 9023,
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
      "lineno": 9026,
      "end_lineno": 9078,
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
      "lineno": 9081,
      "end_lineno": 9168,
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
      "lineno": 9171,
      "end_lineno": 9256,
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
      "lineno": 9272,
      "end_lineno": 9341,
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
      "lineno": 9349,
      "end_lineno": 9361,
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
      "lineno": 9364,
      "end_lineno": 9396,
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
      "lineno": 9433,
      "end_lineno": 9455,
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
      "lineno": 9458,
      "end_lineno": 9518,
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
      "lineno": 9521,
      "end_lineno": 9647,
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
      "lineno": 9665,
      "end_lineno": 9676,
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
      "lineno": 9679,
      "end_lineno": 9766,
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
      "lineno": 9772,
      "end_lineno": 9825,
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
      "lineno": 9828,
      "end_lineno": 9873,
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
      "lineno": 9876,
      "end_lineno": 9938,
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
      "lineno": 9941,
      "end_lineno": 9986,
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
      "lineno": 9989,
      "end_lineno": 10017,
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
      "lineno": 10020,
      "end_lineno": 10171,
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
      "lineno": 10174,
      "end_lineno": 10196,
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
      "lineno": 10199,
      "end_lineno": 10339,
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
      "lineno": 10342,
      "end_lineno": 10366,
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
      "lineno": 10369,
      "end_lineno": 10399,
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
      "lineno": 10402,
      "end_lineno": 10441,
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
      "lineno": 10444,
      "end_lineno": 10499,
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
      "lineno": 10502,
      "end_lineno": 10521,
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
      "lineno": 10524,
      "end_lineno": 10917,
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
      "lineno": 10920,
      "end_lineno": 11515,
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
      "lineno": 11518,
      "end_lineno": 11586,
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
      "lineno": 11589,
      "end_lineno": 11652,
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
      "lineno": 11655,
      "end_lineno": 11695,
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
      "lineno": 11698,
      "end_lineno": 11713,
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
      "lineno": 11716,
      "end_lineno": 11746,
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
      "lineno": 11749,
      "end_lineno": 11944,
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
      "lineno": 11947,
      "end_lineno": 11967,
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
      "lineno": 11970,
      "end_lineno": 12007,
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
      "lineno": 12010,
      "end_lineno": 12098,
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
      "lineno": 12101,
      "end_lineno": 12169,
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
      "lineno": 12172,
      "end_lineno": 12186,
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
      "lineno": 12189,
      "end_lineno": 12231,
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
      "lineno": 12234,
      "end_lineno": 12251,
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
      "lineno": 12254,
      "end_lineno": 12351,
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
      "lineno": 12354,
      "end_lineno": 12507,
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
      "lineno": 12510,
      "end_lineno": 12642,
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
      "lineno": 12672,
      "end_lineno": 12694,
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
      "lineno": 12697,
      "end_lineno": 12794,
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
      "lineno": 12797,
      "end_lineno": 12903,
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
      "lineno": 12906,
      "end_lineno": 13098,
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
      "lineno": 13101,
      "end_lineno": 13131,
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
      "lineno": 13134,
      "end_lineno": 13352,
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
      "lineno": 13355,
      "end_lineno": 13573,
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
      "lineno": 13576,
      "end_lineno": 13627,
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
      "lineno": 13630,
      "end_lineno": 13658,
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
      "lineno": 13661,
      "end_lineno": 13703,
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
      "lineno": 13706,
      "end_lineno": 13755,
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
      "lineno": 13758,
      "end_lineno": 13819,
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
      "lineno": 13822,
      "end_lineno": 13843,
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
      "lineno": 13846,
      "end_lineno": 13866,
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
      "lineno": 13869,
      "end_lineno": 13898,
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
      "lineno": 13901,
      "end_lineno": 13928,
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
      "lineno": 13931,
      "end_lineno": 13956,
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
      "lineno": 13959,
      "end_lineno": 14012,
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
      "lineno": 14015,
      "end_lineno": 14130,
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
      "lineno": 14160,
      "end_lineno": 14174,
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
      "lineno": 14207,
      "end_lineno": 14219,
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
      "lineno": 14222,
      "end_lineno": 14231,
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
      "lineno": 14234,
      "end_lineno": 14246,
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
      "lineno": 14249,
      "end_lineno": 14254,
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
      "lineno": 14257,
      "end_lineno": 14336,
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
      "lineno": 14339,
      "end_lineno": 14357,
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
      "lineno": 14360,
      "end_lineno": 14389,
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
      "lineno": 14392,
      "end_lineno": 14445,
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
      "lineno": 14448,
      "end_lineno": 14454,
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
      "lineno": 14457,
      "end_lineno": 14478,
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
      "lineno": 14481,
      "end_lineno": 14492,
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
      "lineno": 14495,
      "end_lineno": 14509,
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
      "lineno": 14512,
      "end_lineno": 14534,
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
      "lineno": 14537,
      "end_lineno": 14599,
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
      "lineno": 14602,
      "end_lineno": 14615,
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
      "lineno": 14618,
      "end_lineno": 14629,
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
      "lineno": 14632,
      "end_lineno": 14652,
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
      "lineno": 14655,
      "end_lineno": 14708,
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
      "lineno": 14711,
      "end_lineno": 14721,
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
      "lineno": 14758,
      "end_lineno": 14812,
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
      "lineno": 14815,
      "end_lineno": 14826,
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
      "lineno": 14829,
      "end_lineno": 14890,
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
      "lineno": 14893,
      "end_lineno": 14918,
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
      "lineno": 14921,
      "end_lineno": 15030,
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
      "lineno": 15033,
      "end_lineno": 15221,
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
      "lineno": 15224,
      "end_lineno": 15519,
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
      "lineno": 15522,
      "end_lineno": 15635,
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
      "lineno": 15638,
      "end_lineno": 15731,
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
      "lineno": 15768,
      "end_lineno": 15813,
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
      "lineno": 15833,
      "end_lineno": 15852,
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
      "lineno": 15861,
      "end_lineno": 15871,
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
      "lineno": 15874,
      "end_lineno": 15888,
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
      "lineno": 15891,
      "end_lineno": 15947,
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
      "lineno": 15950,
      "end_lineno": 16133,
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
      "lineno": 16136,
      "end_lineno": 16263,
      "span": 128,
      "decorators": [],
      "signature": "(leerie_dir: Path, st: State, no_push: bool, no_verify: bool, caps: dict | None = None, models: dict[str, str] | None = None, efforts: dict[str, str | None] | None = None, pr_template_override: str | None = None, host_no_push: bool | None = None)",
      "returns": "None",
      "doc": "Phase 6: verify the run branch and record finalize state."
    },
    {
      "name": "orchestrate",
      "qualname": "orchestrate",
      "async": true,
      "lineno": 16269,
      "end_lineno": 16296,
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
      "lineno": 16299,
      "end_lineno": 16551,
      "span": 253,
      "decorators": [],
      "signature": "(args, caps: dict, leerie_dir: Path, st: State, sot_pref: str, verbosity: str, models: dict[str, str], efforts: dict[str, str | None])",
      "returns": "None",
      "doc": "The phase sequence of one run. Split out from `orchestrate()`"
    },
    {
      "name": "main",
      "qualname": "main",
      "async": false,
      "lineno": 16554,
      "end_lineno": 17407,
      "span": 854,
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
      "lineno": 1512,
      "end_lineno": 1524,
      "span": 13,
      "doc": "Raised by signal handlers (SIGTERM, SIGHUP) installed in main().",
      "methods": []
    },
    {
      "name": "RateLimitedExit",
      "bases": [
        "BaseException"
      ],
      "lineno": 1527,
      "end_lineno": 1563,
      "span": 37,
      "doc": "Raised when claude -p reports the Claude Code subscription",
      "methods": [
        {
          "name": "__init__",
          "qualname": "RateLimitedExit.__init__",
          "async": false,
          "lineno": 1558,
          "end_lineno": 1563,
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
      "lineno": 1820,
      "end_lineno": 1947,
      "span": 128,
      "doc": "Background poller that accumulates every PID ever observed as a",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_DescendantTracker.__init__",
          "async": false,
          "lineno": 1849,
          "end_lineno": 1855,
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
          "lineno": 1857,
          "end_lineno": 1860,
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
          "lineno": 1862,
          "end_lineno": 1909,
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
          "lineno": 1911,
          "end_lineno": 1947,
          "span": 37,
          "decorators": [],
          "signature": "(self)",
          "returns": "int",
          "doc": "Stop polling, SIGKILL genuinely-orphaned accumulated PIDs,"
        }
      ]
    },
    {
      "name": "WorkerError",
      "bases": [
        "RuntimeError"
      ],
      "lineno": 6707,
      "end_lineno": 6708,
      "span": 2,
      "doc": null,
      "methods": []
    },
    {
      "name": "_ReplayState",
      "bases": [],
      "lineno": 8394,
      "end_lineno": 8422,
      "span": 29,
      "doc": "Minimal State-alike for replay_capture: no persistent writes.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "_ReplayState.__init__",
          "async": false,
          "lineno": 8403,
          "end_lineno": 8412,
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
          "lineno": 8414,
          "end_lineno": 8415,
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
          "lineno": 8417,
          "end_lineno": 8418,
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
          "lineno": 8420,
          "end_lineno": 8422,
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
      "lineno": 8425,
      "end_lineno": 8437,
      "span": 13,
      "doc": "Raised when State.__init__ cannot acquire the per-run-directory",
      "methods": [
        {
          "name": "__init__",
          "qualname": "StateLockedError.__init__",
          "async": false,
          "lineno": 8435,
          "end_lineno": 8437,
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
      "lineno": 8443,
      "end_lineno": 8572,
      "span": 130,
      "doc": "In-memory run state with atomic on-disk persistence.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "State.__init__",
          "async": false,
          "lineno": 8472,
          "end_lineno": 8492,
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
          "lineno": 8494,
          "end_lineno": 8508,
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
          "lineno": 8510,
          "end_lineno": 8525,
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
          "lineno": 8527,
          "end_lineno": 8538,
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
          "lineno": 8540,
          "end_lineno": 8544,
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
          "lineno": 8546,
          "end_lineno": 8555,
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
          "lineno": 8557,
          "end_lineno": 8565,
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
          "lineno": 8567,
          "end_lineno": 8572,
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
      "lineno": 8705,
      "end_lineno": 8750,
      "span": 46,
      "doc": "Persistent state for one heal-loop run scoped to a single call_type.",
      "methods": [
        {
          "name": "__init__",
          "qualname": "HealState.__init__",
          "async": false,
          "lineno": 8718,
          "end_lineno": 8726,
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
          "lineno": 8728,
          "end_lineno": 8739,
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
          "lineno": 8741,
          "end_lineno": 8750,
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
      "log": 50,
      "die": 36,
      "run_proc": 18,
      "claude_p": 15,
      "load_prompt": 14,
      "_read_toml_key": 13,
      "now": 11,
      "_resolve_positive_int_pref": 9,
      "compute_run_branch": 8,
      "_resolve_bool_pref": 8,
      "_confidence_issues": 6,
      "gather_or_cancel": 6,
      "_run_checked_loop": 6,
      "_cgroup_request": 5,
      "_build_predecessor_graph": 5,
      "_write_run_json": 5,
      "_resolve_str_pref": 4,
      "is_protected_path": 4,
      "capture_repo_deps": 4,
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
      "_cleanup_on_abnormal_exit": 2,
      "_format_run_duration": 2,
      "_validate_run_json": 2,
      "discover_runs": 2,
      "_derive_run_status": 2
    },
    "fan_out_top": {
      "main": 47,
      "_run_phases": 32,
      "phase_reconcile": 22,
      "run_final_conformance": 18,
      "settle_subtask": 16,
      "_run_conformance_phase": 14,
      "phase_provision": 13,
      "capture_repo_deps": 11,
      "phase_plan": 11,
      "phase_overlap_judge": 11,
      "integrate_wave": 11,
      "_compose_pr_via_llm": 11,
      "_invoke": 10,
      "phase_finalize": 10,
      "phase_execute": 8,
      "phase_heal": 7,
      "claude_p": 6,
      "phase_classify": 6,
      "run_implementer": 6,
      "preflight": 5,
      "run_recapture_deps": 5,
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
      "_DescendantTracker.stop_and_reap": 3,
      "resolve_run_id": 3,
      "_format_run_for_disambiguation": 3,
      "_collect_run_rows": 3,
      "resolve_worker_memory_max": 3,
      "_resolve_bool_pref": 3,
      "resolve_telemetry_enabled": 3,
      "check_planner_output": 3
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
      "_exit_reap_candidates": [],
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
        "_exit_reap_candidates",
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
        "_become_subreaper",
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
      "_exit_reap_candidates": [],
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
        "_exit_reap_candidates",
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
        "_become_subreaper",
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
