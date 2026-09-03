# AI Learning OS Agent Instructions

## Architecture rules

1. Web only renders state and submits user intent. It never owns exam time, grades, correct answers, or mastery truth.
2. Correct answers, explanations, rubrics, and grading results stay server-side until submission.
3. Keep the repository protocol boundary stable; replace the memory repository without changing API routes.
4. Do not add direct model-provider calls from UI components. All model access goes through a future Model Gateway.
5. Voice providers must sit behind an adapter. Browser speech is a temporary local adapter, not the final architecture.
6. Imported public content must pass Source Registry and license checks before reuse.

## Verification

Run from the repository root before declaring work complete:

```powershell
npm run typecheck
npm run lint
npm run build
pytest
.venv\Scripts\python.exe -m ruff check services/api/app services/api/tests
```

Real-PostgreSQL integration tests run only when `AIOS_PG_TEST_URL` points at an
**isolated test database** (`ai_learning_os_test` — create it with
`services/api/scripts/create_pg_test_db.py`). Never point it at the shared/main
database `ai_learning_os` (port 5433): the safety gate in
`services/api/app/db/test_gate.py` auto-skips with a reason and refuses any
connection, and writing test data into the main database is forbidden.

For UI changes, also run a browser flow that creates an exam, answers all questions, submits, and reads the review report.
