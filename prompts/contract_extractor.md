# Contract extractor

You type one self-reported risk from a parallel code-change run.

Each subtask in the run was implemented in its own branch by a separate worker. A worker
recorded the risk below with kind `external_contract_assumption`: its change is correct
only if something outside its own subtask holds.

Decide whether the assumption is that ANOTHER SUBTASK OF THIS RUN (listed in SUBTASKS)
must produce or consume one exact value — a string literal, mode/enum value, key, header
name, route path, field name or exported identifier — for this worker's change to work.

- `counterpart_sid`: the id (verbatim from SUBTASKS) of the subtask that must honor the
  value. Null when: the assumption is about an external system (an API, a vendor, a test
  framework, the deployment); it is about code that already exists and nobody in this run
  changes; the risk says the change does NOT depend on the value; or no listed subtask
  plausibly owns it. Do not guess between several equally plausible subtasks: pick the
  one whose title owns the behavior, else null.
- `literal`: the exact value the counterpart must contain, copied character-for-character
  from the risk text, without surrounding quotes. Null if the risk names no exact value.
- `literal_kind`: `string_value` (a quoted string the counterpart's code must emit or
  compare, e.g. a mode, status, key or header value), `identifier` (a field, property,
  function, component, type or query-parameter NAME), `route_path` (a URL path the
  counterpart must serve), or `other` (a shape, prose, a number, anything else).
- `symbol`: an exported constant or identifier the risk says carries that value, copied
  verbatim; null if none is named.

Return only the schema's fields. Do not use tools.
