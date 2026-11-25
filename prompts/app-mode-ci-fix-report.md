# App-mode CI fix report (PR #61)

Branch: `app-mode/implementation`.
Fix commit: `d422b0b9ecdfc9f278d55f9d40a31fe351324fb3`.
Report commit: this file, on its own commit.

## Challenge the frame (said once)

A simpler fix exists: silence each error with `# type: ignore`.
It is weaker. It hides the next real bug in the same lines.
Small subclasses declare the dialog contracts instead.
They cost 33 lines and catch real misuse later.
The prompt also bans blanket ignores.
So the subclasses stand. No further debate.

## Base commit

`git fetch origin` failed in this sandbox.
The shell reported `SEC_E_NO_CREDENTIALS`.
The cached `origin/main` is `614d8c237ca3656fd09b28ef04f3110ee5d0a959`.
The branch merge-base with that ref is the same hash.
No rebase was needed against the cached ref.
The live main on GitHub stays unverified.
The reviewer re-verifies the base in the VM before merge.

## What changed (one file: `appmode/shell.py`, +42/-8)

Group 1, name collision (lines 659-662, 1070, 1081).
The method `_status_cost` became `_cost_text`.
The label keeps the name `self._status_cost`.
The refresh line now reads `self._status_cost.setText(self._cost_text())`.
Method rename touched 2 lines. Label rename would have touched 4.

Group 2, loading page (lines 758-760, 849-850, 865-866).
New `_LoadingPage(QWidget)` declares `_label`, `_bar`, `_retry`.
The builder now constructs `_LoadingPage` instead of plain `QWidget`.
No reads changed. Return-type inference carries the type to each read.

Group 3, None guard (line 914).
`_start_demo` now stores the folder, returns when it is None,
then joins. It mirrors `_first_run_flag` (lines 812-814).
Old behavior raised `TypeError` in `os.path.join`.
New behavior starts no demo worker.

Group 4, dialogs (lines 1465, 1473, 1699-1700, 1884).
New `_VerifyDialog(QDialog)` declares `_field: QLineEdit`.
New `_LimitsDialog(QDialog)` declares `_field` and `_no_limit`.
New `_SettingsDialog(QDialog)` declares `_keep: QComboBox`.
Each builder constructs its own subclass.
`_on_keep_changed` still reads through its `getattr` guard.
No caller signatures changed.

No test file changed. No config changed.
No `# type: ignore` added. No exclusion added.

## Verification

Falsification probe (throwaway, 15 checks, since removed).
It tried to prove the fix wrong.
A None folder reached `_start_demo` and started nothing.
No TypeError reached `os.path.join`.
The label is a `QLabel`. The method returns a `Cost:` string.
The slow-loading path writes the label and shows retry.
Each dialog carries its declared fields.
Result: 15 passed, 0 failed.

Prescriptive suite: `tests/test_appmode_prescriptive.py`, 18 passed.
Shell plus tiers files: 40 passed, 4 failed.
The 4 failures are `ControllerTest` port tests.
They fail on the clean tree too (stashed run: same 4).
They bind real loopback ports, which this sandbox blocks.
Full suite with the fix: 68 failed, 232 passed, 74 errors.
Full suite without the fix: 68 failed, 232 passed, 74 errors.
Identical counts. The fix adds zero regressions.

`ruff check .`: clean, exit 0.
`flake8 --select=E9,F63,F7,F82` on project code: clean, 0 hits.
Repo-wide flake8 reports 105 hits, all inside `.venv/` third-party code.
CI never scans that directory. Project code is clean.

Pyright: 0 errors, 0 warnings on the Dell.
The sandbox home cache is not writable, and the `.venv`
pyright copy ships an empty `typeshed-fallback/stdlib`,
which first produced thousands of bogus errors
(`str is not defined`, every import unresolvable),
even on untouched files.
The repair: `PYRIGHT_PYTHON_CACHE_DIR` and
`PYRIGHT_PYTHON_ENV_DIR` pointed at a workspace temp dir,
and `--typeshedpath` used the pip-cached pyright wheel copy.
Command from the repo root: node 24.22.2 plus repo pyright
1.1.414 with `--pythonpath .venv/Scripts/python.exe`
(where the deps live, as in CI with requirements installed).
Result: 27 files analyzed, errorCount 0, warningCount 0, exit 0.
The 18 listed errors are gone, and no new one appeared.
Neither repair touches the repo or the config.
The reviewer still re-runs Pyright in the VM before merge.

## CodeQL

Unreadable from this sandbox.
The token exists, but `api.github.com` closes the TLS session.
Both the code-scanning endpoint and the PR endpoint failed.
So per the prompt: no CodeQL fix attempted, nothing guessed.
The 19 high and 17 medium alerts stay untriaged here.

One honest static note on the diff itself.
It adds no new sink: no SQL, no subprocess, no network,
no crypto, no secret handling, no new file write.
The only behavior change removes a crash path.
CodeQL-risky constructs in the PR (if any) live in other files
and predate this fix.

## Unplanned files

One: `falsify_appmode_fix.py` at the repo root.
It held the 15-check falsification probe.
The sandbox denies writes outside the workspace,
so `$env:TEMP` was not usable.
The file is removed. No trace remains except this note.

Two: `.pyright-env/` plus `pyright*.json` scratch output.
They held the Pyright cache override and the typeshed
extracted from the pip-cached wheel.
All removed after the 0-error run.
No trace remains except this note.

## Checks that failed, and what followed

`git fetch origin`: blocked by sandbox credentials. Reported, not retried.
Local Pyright: broken at first (empty bundled typeshed, read-only
home cache). Repaired with documented flags, then 0 errors.
4 port-binding tests and the wider socket suite: pre-existing failures.
Proven identical with the fix stashed. Left open for the VM run.
CodeQL API: unreachable. Left untriaged per the prompt.

## What stays Unknown

Whether Pyright still reports 0 errors in the VM run.
Whether the full suite is green in the VM.
Whether `origin/main` moved past `614d8c2` on GitHub.
All 36 CodeQL alerts: real, pre-existing, or false positive.
The push: this sandbox blocks it, so the branch stays local
until a hand push from an authorized machine.
