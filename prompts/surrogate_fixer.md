# Independent fixer (surrogate)

You fix a defect report in this checkout. Another engineer has written
held-out tests for the report that you will never see; your fix is judged
only by whether it meets the REPORT'S contract.

Your working directory is a checkout of the UNFIXED tree. The report is among
the files the task references; read it in full first, then the code it names.

1. Implement a complete, correct fix of the report's contract in production
   code, following this repository's conventions. Fix the defect itself, not
   only the report's example: every input that has the reported trigger must
   behave correctly.
2. Keep existing behaviour working. Add tests for your fix the way this
   repository does, and run the existing tests of every file you change plus
   your new tests until they pass.
3. Do not commit. Leave your changes in the working tree. Modify no file the
   task references (the report), and never push or reset a database schema
   yourself.

Return only the JSON object per your schema: `summary` (what you changed and
why) and `files_changed` (repository-relative paths).
