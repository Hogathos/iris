# Hooks security fixes (Codex)

## Task

Fix the security vulnerabilities in the Iris hooks that two independent blind reviewers found. The hooks are live on main (PR #63 merged), so these vulnerabilities are live and need fixing now. Both reviewers independently reached BLOCK.

The hooks:
- `hooks/iris-check-banned-files.py` — hook 1, banned files (pre-commit)
- `hooks/iris-check-lint.py` — hook 2, fast lint + types (pre-commit)
- `hooks/iris-check-plan.py` — hook 3, unplanned files (pre-commit)
- `.muse/hooks/iris-pre-tool-use.py` — hook 4, Muse Code PreToolUse hook
- `hooks/install.py` — installer

Tests live in `tests/test_hooks.py` and `tests/test_muse_hooks.py` (31 tests).

## Challenge the frame

Before you start, if a simpler or sturdier way to fix this exists, say so once up front, then do what this prompt asks.

## Steps

1. Write failing tests first that prove each vulnerability below, using the exact inputs given. Add them to `tests/test_hooks.py` and `tests/test_muse_hooks.py`.
2. Implement the fixes, in priority order (catastrophic first).
3. Run the full pytest suite. All 31 existing hook tests plus your new tests must pass, and the full suite stays green.
4. Run the lint gate (ruff + pyright) clean.
5. Write the report.

## Fixes, in priority order (catastrophic first)

1. **Protect the plan file.** The agent must not be able to write to or delete `approved-plan.txt` to approve its own files. Remove the exemption that lets a write to `approved-plan.txt` pass the plan check in hook 4, and make `delete_file` on the plan file denied. A missing plan file must fail closed (deny), not open. Proof inputs: `write_file` to `approved-plan.txt` adding `src/evil.py` then `write_file` to `src/evil.py` (both must be denied); `delete_file approved-plan.txt` then `write_file` to `anything/evil.py` (must be denied); `rm approved-plan.txt` then `write_file` (must be denied).
2. **Handle all write-tool path shapes.** `evaluate_payload` in hook 4 reads only `path` or `file_path`. `apply_patch` carries its target inside patch text, `notebookedit` has its own shape, and namespaced names like `mcp__fs__write_file` do not match. Extract the target path from every write-tool shape. If the path cannot be determined, deny. Proof inputs: `apply_patch` with `{"input": "*** Add File: dist/app.exe"}` (must be denied); `{"filename": "dist/app.exe"}` (must be denied); `mcp__fs__write_file` with `path: dist/x.exe` (must be denied).
3. **Wrap the hook in try/except so it fails closed.** Any exception in hook 4 must exit 2 (deny), not exit 1 (allow). Handle a plan file saved as UTF-16 (the Windows PowerShell `>` default) and a UTF-8 BOM in the plan (open with `utf-8-sig`). Proof inputs: an `approved-plan.txt` containing byte `\xff` then a write (must deny, not crash with exit 1); a plan saved as UTF-16 then a write (must deny or parse cleanly, not crash).
4. **Do not echo the full command in deny reasons.** `_deny` and `_check_shell_command` in hook 4 write the whole command to stdout and stderr, so an inline secret in a denied command lands in the log. Redact or omit the command from the deny reason. Proof input: `powershell` with command `curl -H 'X-Key: TESTVALUE' host; git push --force` — the string `TESTVALUE` must not appear in stdout or stderr.
5. **Fix the shell ban patterns.** They are trivially defeated by global options and flag variants. Handle `git -C . add -A`, `git -c user.name=x commit -a -m msg`, `git commit --all -m msg`, `rm --recursive --force build`, `rm -rfv build`, `rm -Rf build`. All must be denied. Also fix the false deny: `git commit -m "document the -a option"` must be allowed (the `-a` pattern must not scan inside quotes).
6. **Installer must back up and respect the environment.** `install()` in `hooks/install.py` overwrites an existing `.git/hooks/pre-commit` with no backup — back it up first. Respect `core.hooksPath` (hooks must run when it is set). Use `git rev-parse --git-path hooks` instead of `--git-dir`, so the install is not silently inert in a linked worktree. Handle `install()` errors (e.g. a `PermissionError` on a locked hook file on Windows) instead of producing a traceback.

## Also fix in the same pass (ordinary findings)

- **Secret-name patterns block legitimate files.** `*_key*` and `*_secret*` match ordinary names like `tests/test_api_key_parser.py`, `tests/test_secrets.py`, `src/api_key_utils.py`, `.env.example`. Tighten the patterns so legitimate source files pass. The ban list is also loose on the other side: `secrets.json`, `secret.txt`, `credentials.json`, `token.txt` currently pass — they must be banned.
- **Directory bans are case-sensitive on Windows.** `BUILD/x.py` and `Dist/a.txt` pass on a case-insensitive filesystem. Make directory bans case-insensitive.
- **Hook 2 lints the working tree, not the index.** Lint the staged version, not the working-tree copy. Pass `--force-exclude` (or the equivalent) so config exclude entries are honoured.
- **Deletions are invisible to hook 3.** `--diff-filter=d` drops deletions, so `git rm src/unplanned.py` then commit passes the plan gate, and a staged deletion of `approved-plan.txt` is skipped. Deletions must be visible to the plan check.
- **Hook 4 looks for the plan in the payload cwd, not the repo root.** If the session cwd is a subdirectory, the plan check is skipped. Resolve the repo root the way hook 3 does (`git rev-parse --show-toplevel`).
- **Destructive PowerShell is not covered.** `Remove-Item -Recurse -Force build` returns allow. Cover it.
- **The generated shell wrapper is not quoted safely.** `python_exe` is embedded in double quotes in `hooks/install.py`. A path containing `$(...)` or a double quote runs as shell code when the hook fires. Quote it safely.

## Constraints

- Stdlib-only Python, no new dependencies.
- Do not change the hooks' external contract (exit codes, output format) except where the fix requires it (exit 2 on exception, deny instead of allow).
- Keep all 31 existing hook tests passing. Add a new test for each fix above, using the proof inputs given.
- Full suite green, ruff + pyright clean.
- The hooks must work on Windows (the Dell), where commits happen.
- Do not invent fixes for findings not listed here. Do not redesign the hooks.

## Report

Write the report to `prompts/tooling-hooks-security-fixes-codex-report.md`. Include: what was fixed (one line per fix above, with the test that proves it), the full test results (count passed/failed), the lint results, and anything not fixed with why.
