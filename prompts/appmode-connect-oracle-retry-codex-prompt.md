# App mode: live-traffic oracle for one-click connect — Codex prompt

## Setup first

Work on branch `main`. Record the head with `git log -1 --oneline` and report it as the base commit you ran on. Do NOT require an exact commit: filing this prompt advances main (the prompt commit itself is the newest commit), so the head will be `0da3cdf` or later, not `fe98613` — that mismatch is expected and is not a stop condition. Proceed as long as you are on `main` and `appmode/connect.py` and `proxy.py` are present. Only stop if you are not on `main`, or if either of those files is missing.

Challenge the frame before you start: if a simpler or sturdier way to verify the connect path end to end exists, say so once, up front, in your report, then do the oracle as written. Do not nag the point. Silence means you checked and this is the best path.

## The job

You are verifying, not implementing. Do not change any code. Run the live-traffic oracle below against main and report the results. The oracle proves two things: (1) the one-click connect routes a real agent's traffic through Iris (the journal shows it), and (2) disconnect restores the agent's settings file byte for byte.

The connect backend is merged (PR #50) and the Connect button is merged (PR #61). This oracle drives the backend directly with Python calls, per the by-hand checklist — Codex cannot click a GUI button, and the backend is the thing being proven. The button path gets its own oracle run later.

## Hard limits for this run

- Muse Code quota is EXHAUSTED until Nov 29, 2026, 19:00 EST. The traffic step uses `muse exec`, and it may fail before sending any request (quota checked client-side). That is a finding, not a failure of Iris.
- Phase 2 (traffic flowing, http 200) is OUT OF SCOPE for this run. It needs a live model answer, which needs quota. Do not attempt it. Record the model host from Phase 1 for the Nov 29 run, and state clearly that Phase 2 waits for Muse.
- If a step does not match its expected result, stop and report the step number and what you saw. Do not force past it.
- Never paste the contents of the settings file anywhere — it may contain keys. Record ONLY its hash.
- Do not commit. Do not push. Charles commits and pushes. Stage named files only if you stage anything (you should not need to).

## Environment

- Repo: `C:\Users\Quant\Documents\iris`, branch `main`.
- Python: call `.\.venv314\Scripts\python.exe` directly. Do not use `run-proxy.bat` or `iris.py` — they look for `.venv\Scripts\python.exe`, which no longer exists.
- You have one terminal, not two. Run the proxy as a background process (Start-Process, output to a log file) and stop it when done. There is no second terminal to keep it in.
- The agent settings file is `C:\Users\Quant\.config\muse\settings.json`. The backup is `settings.json.iris-backup` next to it.
- The journal is the repo's `attempts.db`, via `db.py`.

## The oracle

### Phase 0 — setup
1. Confirm Muse Code has run once, so the settings file exists. If it does not exist, stop and report it.
2. Record the settings file hash. `(Get-FileHash "$env:USERPROFILE\.config\muse\settings.json").Hash`
3. Confirm there is no backup file. This must be False: `Test-Path "$env:USERPROFILE\.config\muse\settings.json.iris-backup"`. If True, a previous connect is still open — run the disconnect command from Phase 3 first, then start over.
4. Record the journal count. `python -c "import db; c=db.get_connection(); print(c.execute('SELECT COUNT(*) FROM attempts').fetchone()[0])"`

### Phase 1 — connect, prove the proxy sees the traffic
5. Start the proxy in the background: `python proxy.py --port 8888 --db attempts.db --allowlist "127.0.0.1,localhost,::1" --rate-limit 100 --rate-window 10 --relay-timeout 30`. Leave it running. If it exits with an error, stop and report the error.
6. Connect Muse Code: `python -c "import os; from appmode.connect import connect_agent; print(connect_agent('muse-code', 'http://127.0.0.1:8888', os.path.expanduser('~')))"`. Expected: a message saying Muse Code now routes through `http://127.0.0.1:8888`, naming the backup. If it refuses, stop and report the refusal message.
7. Confirm the backup file now exists. This must be True.
8. Run the traffic step: write `Say hello in one word.` to a temp file and run `muse exec --prompt-file <tempfile> --disable-approval --trust-workspace`. Each `muse exec` invocation is a fresh process that reads settings at startup, so this IS the restart step — the new session picks up the proxy setting. Expect the prompt to fail: the model host is not in the proxy's allowlist yet, so Iris blocks it. If `muse exec` fails with a quota error before sending any request, record the exact error and stop Phase 1 here — do not proceed to step 9. Report that no journal row can be expected and why.
9. Check the journal — newest rows: `python -c "import db; c=db.get_connection(); rows=c.execute('SELECT id, trace_id, dest_host, endpoint, method, http_status, outcome, reason FROM attempts ORDER BY id DESC LIMIT 5').fetchall(); [print(r) for r in rows]"`. Expected: a new row with outcome `blocked`, reason `not_allowlisted`, and dest_host set to the model host. Write down that host — it is the proof the proxy intercepted the traffic, and the Nov 29 run needs it for Phase 2. If there is no new row, the call never reached Iris. Stop and report it.

### Phase 3 — disconnect, prove the restore is byte for byte
(Phase 2 is out of scope for this run — see Hard limits.)
10. Disconnect Muse Code: `python -c "import os; from appmode.connect import disconnect_agent; print(disconnect_agent('muse-code', os.path.expanduser('~')))"`. Expected: a message saying Muse Code is disconnected and the file was restored from the backup.
11. Confirm the backup file is gone. This must be False.
12. Hash the settings file again. It must match the hash from step 2 exactly.
13. Stop the proxy.

## Pass conditions for this run
- Phase 1: the journal shows the blocked row with the model host. OR: `muse exec` failed on quota before sending a request, with the exact error recorded and no journal row expected — report this as the outcome, not a failure.
- Phase 3: the backup is gone and the hash matches step 2 exactly.
- Phase 2: not attempted; waits for the Nov 29 Muse run.
- If any step does not match, stop and report the step number and what you saw. Do not force past it.

## Report

Write your report to `prompts/appmode-connect-oracle-codex-report.md`. It must state: the base commit you ran on; the frame challenge (what you checked, one line); the Phase 0 values (settings hash, journal count, backup absent); the Phase 1 result (blocked row with dest_host, outcome, reason — or the quota error and why no row was expected); the model host if observed; the Phase 3 result (backup gone, hash matches step 2); that Phase 2 was not attempted and why; the proxy log location if it errored; and what is still unknown. Do not include settings file contents, keys, or request bodies — hashes, counts, and journal row summaries only.
