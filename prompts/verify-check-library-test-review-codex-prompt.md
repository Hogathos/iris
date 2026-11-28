# Codex prompt: review the Verify check library tests (independent review, report only)

## Role

You are an independent reviewer, not the test-writer and not the implementer.
Your job is to read the three test files, run them against the real engine
and the real journal, find the holes, and report them. You do NOT change
anything. You do NOT implement the library. You report; Charles decides
what to change.

Before you start, if a simpler or sturdier way exists, say so once, up
front, then do what this prompt asks.

## What you are reviewing

The Verify check library, build order item 3, one slice. The spec is
`specs/verify-check-library/requirements.md` (approved by Charles
2026-11-28). The three test files are:

- `tests/test_verify_check_library.py` — 8 tests: the catalog, the secrets
  profile (SandHive secret-redaction-in-logs, MIT), the verdict rules,
  provenance (the journal records each check's source and license).
- `tests/test_verify_state_machine_properties.py` — 7 tests: property
  tests over the state machine in `devpanel/states.py` (7 states,
  32 transitions), by deterministic exhaustive enumeration.
- `tests/test_verify_drill_partial_write.py` — 4 tests: Drill 1
  (Partial write) end to end, plus the control and the journal check.

The existing Verify engine they build on is already in the repo:
`proxy.py` (Verify is a plain method on the proxy state, not an HTTP
route), `db.py` (the journal), `devpanel/states.py` (the state machine),
and the existing tests `tests/test_verify_read_back_local.py` and
`tests/test_bench_verify_incorrect_stops.py` (the conventions to match).

## Why you, and why now

You run on the Dell with DB access. Use it. Do not just read the test
files — run them, run the existing verify tests, and inspect the real
engine and the real journal. A read-only review would miss the holes
that only show up when the tests execute. Run the tests with the repo's
venv (`.venv314`, Python 3.14) and the repo's runner.

## Steps

1. Read the spec (`specs/verify-check-library/requirements.md`), the
   three test files, and the engine code they call (`proxy.py` verify
   method, `db.py` journal schema, `devpanel/states.py`).
2. Run the three new test files. Each must fail with
   `ModuleNotFoundError: No module named 'verify_library'` — the
   feature does not exist yet. Confirm that failure, and confirm
   nothing else fails (no import error in an existing module, no wrong
   function name, no syntax error). If a test fails for a different
   reason, that is a hole: report it.
3. Run the existing verify tests (`tests/test_verify_read_back_local.py`,
   `tests/test_bench_verify_incorrect_stops.py`, `tests/test_bench_verify_fold.py`,
   `tests/test_bench_verify_no_trace.py`). They must pass. If any
   fails, that is a hole in the environment or a wrong assumption in
   the new tests: report it.
4. Check every expectation the new tests hold about the real engine:
   - Does `proxy.state.verify_read_back` exist with the signature the
     tests call, and does it return the verdict and card the tests
     expect? Does an Incorrect verdict stop the Run the way the tests
     assert (paused, new tunnel 503, nothing upstream)?
   - Does the journal (`db.py`, `attempts` table) carry the fields the
     provenance test reads, and can a check's source and license be
     recorded in a journal row? Is there a field for them, or does the
     test assume one that does not exist?
   - Does `devpanel/states.py` have exactly the 7 states, 32
     transitions, and 7-event alphabet the property tests pin? Does
     `run_state` fold journal rows the way the property tests' synthetic
     rows assume (including the `verify_ok` from FLOWING case, and
     traffic rows moving nothing)?
   - Does the secrets check's concrete read-back (a local-file claim
     whose expected text is the redacted log) match what the Verify
     engine can actually read back? Is the fake secret
     (`sk-test-fake-00000`) clearly fake and never a real key?
   - Does Drill 1's fault (3 of 10 settings removed) produce a verdict
     of Unknown or Incorrect through the real engine, and does the
     control (all 10 settings) produce Correct?
5. Look for coverage holes: a spec acceptance criterion with no test, a
   test that cannot fail (it passes whether or not the feature works),
   a test that asserts something the spec does not require, or an edge
   the tests miss (for example: a check whose steps list is empty, a
   profile with no checks, a drill whose check has no source, a
   transition the enumeration never exercises).
6. Write the report to
   `prompts/verify-check-library-test-review-codex-report.md` and commit
   it on this branch. Do NOT modify the test files, do NOT create
   `verify_library.py`, do NOT modify any source file. Review and report
   only.

## The report

For each hole found, give: what the hole is, which test file and which
spec acceptance criterion it affects, the evidence (the exact test
output, the exact engine behavior, or the exact line), and the specific
fix — the test to add (with a name and what it should assert) or the
specific modification to an existing test. Rank the holes: must-fix
before implementation, or nice-to-have. If you find no holes, say so
explicitly and say what you checked. End the report with the exact
failure output of the three new test files (proving they fail for the
missing feature) and the exact result of the existing verify tests
(proving the engine behaves as the new tests assume).

## Rules

- Review only. No code, no test changes, no new files except the report.
- Stage named files only (`git add -- prompts/verify-check-library-test-review-codex-report.md`). Never `git add -A`, never `git commit -a`.
- Never commit `.exe`, `dist/`, `build/`, `*.db`, `.pytest_cache/`, `*.spec`, or secrets. The checked-in `iris.spec` is the one exception and you are not touching it.
- Report the commit hash and the test results, then stop.
- Keep verbose comments and docstrings where you write them. Do not strip them.
