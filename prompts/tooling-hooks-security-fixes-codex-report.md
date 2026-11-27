# Iris hook security fixes

Windows verification used Python 3.14.10, Git 2.55.0, pytest 9.2.0, Ruff 0.17.6, and Pyright 1.1.414.

## Fixes and checks

| Fix | Check |
| --- | --- |
| Muse denies direct changes to the approved plan and denies file mutations when the plan is missing. | `test_denies_write_to_plan_file_itself`, `test_missing_plan_denies_normal_write`, and post-fix checks for file-tool and shell removal. |
| Muse checks patch targets and move destinations, notebook path fields, and namespaced writers. It denies known writers without a target. | `test_supported_write_shapes_require_an_approved_target` and post-fix checks for denied targets. |
| Muse exits 2 on unexpected errors and reads UTF-8 plans with a BOM and UTF-16 plans with a BOM. | `test_unreadable_plan_denies_normal_write`, `test_plan_with_bom_allows_listed_file`. |
| Shell denials omit the command and its arguments. | `test_shell_denial_omits_user_arguments`. |
| The shell check recognizes Git global options and banned option spellings. It keeps quoted commit messages together as arguments. The removal check handles long and combined flags and uppercase recursion flags. | `test_commit_message_can_describe_an_option`, the five existing shell denial tests, and post-fix argument checks. |
| The installer backs up a custom hook, resolves the active Git hooks path, and reports installation errors. | `test_backs_up_existing_hook_once`, `test_installs_in_configured_hooks_path`, `test_installs_in_common_hooks_path_from_linked_worktree`, `test_installer_reports_write_errors`. |
| Both filename checks allow the requested source and example names and block the listed credential filenames. | `test_allows_source_and_example_names`, `test_blocks_credentials_and_mixed_case_directories` for the pre-commit rule. Muse uses the same filename rules. |
| Both filename checks match banned directory names without regard to case. | `test_blocks_credentials_and_mixed_case_directories` for the pre-commit rule. |
| The lint hook checks a temporary copy of the index and passes `--force-exclude` to both Ruff checks. | `test_checks_index_even_when_working_file_differs`, `test_ruff_honors_staged_excludes`. |
| The plan gate checks staged deletions and blocks deletion of the plan itself. | `test_checks_deleted_files_against_plan`, `test_blocks_staged_plan_deletion`. |
| Muse checks the repository's plan when the tool runs from a subdirectory. | `test_subdirectory_uses_root_plan`. |
| The shell check blocks recursive forced removal through PowerShell. | Post-fix PowerShell argument check. |
| The wrapper quotes the interpreter and hook script paths as POSIX shell literals. | `test_wrapper_preserves_literal_interpreter_path` and Git invoking the wrapper from a configured path that contains a space. |

## Baseline

The hook suites ran with the new tests and the original hooks at `3ad7654`. Pytest reported **22 failures**, including five parameterized subtest failures, and **32 passes**.

- Filenames. `test_allows_source_and_example_names` and `test_blocks_credentials_and_mixed_case_directories`.
- Staged lint. `test_checks_index_even_when_working_file_differs` and `test_ruff_honors_staged_excludes`.
- Staged deletions. `test_checks_deleted_files_against_plan` and `test_blocks_staged_plan_deletion`.
- Installer. All five installer checks in the table failed.
- Shell. `test_shell_denial_omits_user_arguments` and `test_commit_message_can_describe_an_option`.
- Muse plan. `test_denies_write_to_plan_file_itself`, `test_missing_plan_denies_normal_write`, `test_unreadable_plan_denies_normal_write`, and `test_subdirectory_uses_root_plan`.
- Muse subtests. Both BOM encodings and all three write-tool shapes in the table failed.

## Verification

- Full pytest suite. `python -m pytest tests/ -q -p no:cacheprovider` reported **452 passed, 0 failed, and 51 subtests passed**. The cache provider was disabled because the existing cache directory was unwritable.
- Hook suites. 49 passed, including the 31 original tests with the approved expectation changed. All five subtests passed.
- Lint. The staged lint hook exited 0 in a temporary Git repository with the changed Python files staged. Ruff check passed across all tracked Python files. Ruff format passed for all seven changed Python files. Pyright reported zero errors and zero warnings.
- Independent Git check. Git invoked the installed wrapper from a configured hooks directory and blocked a commit with a banned fixture.
- Post-fix policy checks. Ten additional decisions passed. The checks evaluated hook inputs and confirmed the plan file stayed intact. The final shell check confirmed six denials and two allowed commands.
- Demo scripts. The visualizer and report ran against six seeded attempts across three demo traces. Two attempts succeeded and three were retries. The fixture uses synthetic costs.
- Secret check. A targeted scan of added text found zero possible credential matches.

## Review

- The staged lint test gives the index and working copy different contents. The hook reports the staged formatting error and preserves the working file. With no staged Python files, the hook exits before creating the snapshot.
- The installer tests cover an existing wrapper, a second install, a configured hooks path, a linked worktree, and a permission error. The installer writes the wrapper to a temporary file before replacing the existing one.
- The nine changed paths are the five hook and installer scripts, the two test modules, the hook requirements, and this report. This report is the only new file.

## Open items

- The exact bypass sequences and executable injection proof were omitted from the regression suite. The coverage above uses defensive assertions and post-fix policy checks.
- Repository-wide Ruff formatting flagged 81 files outside the change. The seven changed Python files passed Ruff formatting.
- The shell check covers the listed command forms. It does not enforce a general shell sandbox.
- Verification is local to Windows. The workspace restricts writes to `.git`. The changes are uncommitted and there is no push or CI result for this patch.
