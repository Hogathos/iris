# Hooks path canonicalization for PR #66

Resolve paths at the filesystem boundary. Resolving them at each comparison would repeat filesystem work and make it easier to miss a path. The installer can resolve the Git hooks directory once. Muse can resolve the working directory and Git root before it derives paths relative to the plan. Plan entries stay as repository-relative names so they describe the paths a human approved.

## Changes

- `hooks/install.py` resolves the destination from Git before copying the hooks and writing the wrapper. `realpath` follows symlinks and junctions. On Windows it also expands 8.3 names where the filesystem provides a long name.
- `.muse/hooks/iris-pre-tool-use.py` resolves the working directory, Git root, and target before it checks plan membership. It uses the resolved plan file for write and removal protection. Filename rules check the resolved path and preserve checks on a supplied name inside the repository.
- `tests/test_hooks.py` resolves the expected install directory for the configured path and linked worktree cases. The linked worktree assertion had compared two spellings of the same directory. The Muse regression test uses its original assertion.

## Before the fix

- CI run `45954936875`, as reported in the request. Windows and macOS Python 3.12 each reported 2 failed, 450 passed, and 51 subtests passed. The Ubuntu quick checks passed.
- The full Windows suite from an archive of `fa9c79d` through the junction reported 2 failed, 450 passed, and 51 subtests passed in 244.83 seconds. The failures were the two named in the request.
- On Windows with Python 3.13.15, both named tests passed with an ordinary temporary path. Both original tests failed with a Windows directory junction as the temporary path. The installer returned the physical directory. Muse computed a path that began with `../` and denied the planned write.
- With the installer assertions updated and the original hook code, the configured-path installer test and the Muse test failed through the junction. The linked worktree assertion passed because Git had already resolved that destination.

## Local verification

- The full-suite comparison used `python -m pytest tests/ -q` with Python 3.13.15. `TEMP` and `TMP` pointed to a junction outside the checkout. Initial runs with temporary paths inside the checkout caused standalone Muse fixtures to inherit the parent Git repository and produce unrelated failures. `PYTEST_ADDOPTS` disabled the unwritable pytest cache.
- The full suite after the fix reported 452 passed, 0 failed, and 51 subtests passed in 242.49 seconds. Both PR failures passed with the same junction setup used for the baseline.
- Hook suites through the junction. 49 passed and 5 subtests passed.
- Ruff check with the repository rules. 0 violations. The broader `E,F` warnings pass reported 83 findings before and after the patch. One is a pre-existing unused import in `tests/test_hooks.py`.
- Ruff format and format check. All 3 changed Python files were already formatted.
- Pyright. 31 repository files checked, with 0 errors, 0 warnings, and 0 information diagnostics. A separate check of both hook sources reported the same counts. That check included the Muse script from a temporary copy because the repository run excludes hidden directories.
- Git read-back. Git invoked the installed wrapper in a temporary repository. Installing through both directory spellings returned the same canonical location and created no backup for an unchanged wrapper.
- Windows path read-back. `realpath` returned the same long path for the local Git installation and its 8.3 name.
- Muse read-back. Six subprocess decisions matched expectations. The inputs covered a relative target from an aliased working directory, an absolute target through an alias, an extended-length Windows path, a missing parent directory, the root `iris.spec` exception, an unplanned target, and the protected plan file.
- `visualizer.py` and `report.py`. Both ran on a temporary seeded database. The fixture had 6 attempts, 3 traces, 2 successes, and 3 retries. Its synthetic cost was $0.0120, which the report displayed as $0.01.
- The diff whitespace check passed. A targeted scan of added text found 0 credential-like strings.

## Open items

- GitHub Actions verification for the patch is pending. The local junction check used Windows Python 3.13.15. It exercised the alias mismatch seen in CI. The macOS and Windows Python 3.12 runners still need to verify the patch with their own temp path spellings.
- The worktree changes are uncommitted. Git is read-only in this workspace.
