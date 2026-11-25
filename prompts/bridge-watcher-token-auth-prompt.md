# Bridge prompt: watcher sends GITHUB_TOKEN to the GitHub API

## Problem
The watcher lists branches and prompt files through the GitHub API without a token. Unauthenticated GitHub allows 60 requests per hour. One poll lists 60+ branches plus each branch's `prompts/` directory, over 1,800 requests per hour. The API answers 403 rate limit exceeded, discovery fails, and the watcher falls back to the config list only. New prompt branches are not discovered until the rate limit clears. A token raises the limit to 5,000 per hour.

The token exists. It is set as the `GITHUB_TOKEN` environment variable on the Dell. The watcher does not read it. The code sends the same unauthenticated headers on every API call.

## Intention / done definition
The watcher sends the token on every GitHub API call when `GITHUB_TOKEN` is set, and behaves exactly as before when it is not set. Auto-discovery works every poll instead of failing on the rate limit. The token is never logged, never printed, never written to a file. A bad token (401) is logged plainly and retried next poll, never a crash. The raw file download never carries the token.

## The fix
In `tools/bridge_watcher.py`:

- Read `GITHUB_TOKEN` from `os.environ` at module load (or at first use, pick the one that matches how the module already reads its config). An empty or missing value means no token.
- Add a headers helper that returns the base headers (`User-Agent`, `Accept`) plus `Authorization: Bearer <token>` when a token is set. Use it in `github_list` (line 269) and `github_list_branches` (line 303) only.
- `download_file` (line 327) must NOT send the token. It fetches from `raw.githubusercontent.com`, not `api.github.com`. Keep it on the base headers only. The token is for the API, not for raw downloads.
- No token set: behavior is unchanged. The watcher still works unauthenticated, with the same rate limit as before.
- Never log or print the token. Not in the poll log, not in an error message, not in a debug line. A 401 from the API surfaces through the existing poll-failure path: log it plainly (without the token), skip that poll's discovery, retry next poll. Do not crash.
- Do not change the dirty-tree check, the checkout logic, the fetch behavior, the run command, or the anti-cascade rules. This fix only changes which headers the API calls carry.

## Tests first
Write the failing tests first, as their own commit, then the fix as its own commit. Add the tests to `tests/test_bridge_watcher.py`, in the existing `RunOnceTest` style (fake lister, downloader, runner; temp dir for state, log, downloads; fake the token via the environment, not by editing the module):

- Token set in the environment: `github_list` sends an `Authorization: Bearer <token>` header on its API call.
- Token set: `github_list_branches` sends the `Authorization` header on its API calls, including paginated follow-ups.
- Token absent: neither `github_list` nor `github_list_branches` sends an `Authorization` header.
- `download_file` never sends the token, even when the token is set.
- The token value never appears in the log file or in any error message.
- A 401 from the API is logged plainly and the poll continues (does not crash, does not mark prompts seen).

## Verification
- Run `git fetch origin` FIRST, then rebase onto current main, and report the new base commit. Verify the base commit matches current main on GitHub before the final verification.
- The new tests fail on the tests-only commit and pass on the fix commit.
- The full suite stays green.
- Ruff and flake8 clean (the repo's selection: E9, F63, F7, F82).
- Exactly two files changed: `tools/bridge_watcher.py` and `tests/test_bridge_watcher.py`. No other files.
- No spec file for this fix, per the fetch-fix precedent (noted here so the reviewer does not flag it).

## Report
Write the report to `prompts/bridge-watcher-token-auth-report.md` on this branch and push it as its own commit, separate from the test and fix commits. The report states the commits, the base commit, the test results (fail-before, pass-after, full suite count), what changed, and what stays open. State plainly that the token is never logged. Do not open a PR. Do not merge. Branch only.

## Frame check
The simpler alternative is to keep running unauthenticated and let discovery fail on the rate limit, falling back to the config list. That works, but it means new prompt branches are not discovered for up to an hour after each rate-limit hit, and the watcher burns its whole hourly budget on the first poll after the reset. The token is already set on the Dell. Sending it on API calls is the durable fix. No further frame challenge is needed; do the fix above.
