# AGENTS.md — Iris

Rules for any agent that works in this repo. Charles decides. His word wins. If a rule here conflicts with a prompt, this file wins unless Charles says otherwise in words.

## What Iris is

Iris is one product. Watch, then Verify, then Steer, then Meter. Then TLS, MCP, TCP, UDP, and gateway.

Iris watches agent traffic through a local proxy. Iris checks a claim against what actually happened. Iris stops a Run when a human has not verified it is fine.

In conversation, Iris means the finished product. In grant text only, keep built and planned separate. A spec is not a build. A demo trace is not a real trace. A listing is not availability.

## Who decides

Charles Brugh is the human. He writes every word of public prose in final form. Agents coach, outline, source, debug, and draft candidates. He approves each test before it is implemented. He approves each merge in words, one at a time. No merge without that approval. No agent merges to main on its own.

## Writing rule

Technical text only: docs, READMEs, PR descriptions, commit messages, specs, and explanations. Write 80% of the way to ASD-STE100 Simplified Technical English. Then run an unslop pass.

- One idea per sentence.
- Short sentences.
- Active voice.
- One word for one meaning.
- Keep product names, code names, and domain terms as they are.
- Cut AI tells: delve, crucial, tapestry, pivotal, additionally, enhance, foster, showcase, landscape, intricate, interplay. Cut the em dash. Cut "not just X, but Y". Cut the rule of three. Cut chatbot phrases.
- Do not claim formal ASD-STE100 compliance. This is 80%, not the full standard.

This rule never applies to Charles's prose. He writes his essays, Substack posts, and pitches himself. No unslop pass touches them.

## Challenge the frame

Before non-trivial work, if a simpler or sturdier way exists, say so once, up front. "Are you sure you want X? Y is simpler because Z." Then do what Charles chooses. Say it once. Do not nag.

Challenge bad, weak, rushed, or rule-breaking ideas before building. Give the plain verdict first, then why, then a better option, then the trade-off. If Charles overrules you, his word wins. File the decision with date, choice, rejected alternative, and why, in `graphs/` and in the spec.

## Work rules

- Plan first for any change that touches two or more modules. List files, order, risks, and out of scope. No code yet. Charles approves the plan.
- Each feature keeps a short spec in `specs/<feature>/requirements.md`. State what it does, its success criteria, and its non-goals. Keep it a few hundred words. No implementation detail in the spec.
- New behavior: write the failing test first. Show it. Charles reviews the test. Then implement. Use this for new behavior, not for refactors.
- After implementation: one review pass that names specific risks. Security, edge cases, efficiency. Not a generic "review this".
- After correctness and teach-back, check efficiency. Spot unnecessary, redundant, or over-processing code. Steer generated code toward high performance.
- Keep verbose comments and docstrings. A human found or wrote them. They are part of the code, not slop. Do not strip them to look terse.
- Do not use Linear for Iris. GitHub Issues plus specs already do it.
- Do not use React for the dev panel in a cleanup step. The panel as built is Python, not React.

## Git rules (Co-Work)

- Stage named files only. Never `git add -A`. Never `git commit -a`.
- Never commit `.exe`, `dist/`, `build/`, `attempts.db`, `*.db`, `.pytest_cache/`, `*.spec`, or secrets.
- Check `git check-ignore -v` before any commit that touches those paths.
- Check `git status` first. Check CI for the exact commit before claiming green.
- Report the commit hash, the push result, and the test results. Then stop.
- Work on a branch. Open a pull request. Merge only after CI is green for the exact commit and Charles approves in words.

## Tests

- Every change keeps the full suite green, plus new tests for the change.
- Do not commit with a failing or skipped test you have not disclosed.
- Run the suite end to end, including any report or visualizer script.
- Report files changed, test count, demo numbers, secrets result, and what is still open. Then stop.
- Run tests with: `python -m unittest discover -s tests -v` and `python -m pytest tests/ -q`. Both must pass.
- Do not quote a test count from memory. Run the suite and read the result. Check CI Actions for the exact commit.

## Iris principles

- Iris must not have a brain. No LLM-as-judge. No scores as verdicts.
- Verify is a deterministic read, then a plain-words digest. State the claim, what was read, from where, and as of when. Give a verdict: Correct, Incorrect, or Unverified. Give one line why. Give a next step.
- Write the journal row before the call. Incorrect pauses the Run.
- Remote effects stay Unverified unless local proof establishes them.
- No trace: an agent claims it did X, and Iris finds no proxy row, no bytes, no tool log, no effect, and no error. Verdict stays Unverified. Reason is No trace. A human investigates.
- Iris stops unless a human verifies everything is fine. Never say "fail closed". Say the full sentence.
- Do not use "fresh Verify" as a noun. Say "A run cannot retry until a human checks it again."
- Do not use "ground truth". Say "what actually happened, checked against the real effect".
- Use "success criteria", not "done definition" or "done task".
- Evaluator means a model tester or ML scientist who runs evals.
- Internal enum stays `unknown`. Human-visible text says "Unverified", never "failed".
- "Red button" is "Emergency stop" in human-visible text only. Code, identifiers, function names, test files, and specs keep red button. Do not rename in code.

## Human-visible wording bans

Do not use these words in GUI text, docs, or user-facing strings: billable vendor, billable cost, priced cost, attempt_cost, trace_id, dest_host. Use plain words instead, such as Money spent and What you paid.

## Proxy contract

- Local demo traffic only. Loopback only, unless Charles explicitly says otherwise.
- Metadata only: trace_id, destination host, endpoint and method, status, latency, bytes in and out, outcome, and reason. Never log, print, or commit Authorization headers, API keys, prompt bodies, response bodies, real hostnames, or any secret.
- Cost is $0 unless a real API actually reports usage. Never invent token counts or costs.
- Never paste a key, token, or password in chat, in a file, or in a prompt.
- A friend's key is never used without that friend's explicit confirmation plus a cap, in words.
- No API credit loaded, no paid model started, without a separate explicit go-ahead.
- Never commit `*.db`, `attempts.db`, or `demo_*.db`.

## Proxy facts

- Port 8888. Database `attempts.db`. Allowlist is loopback only by default.
- A non-allowlisted destination is blocked at the proxy and never forwarded. HTTP gets 403. A CONNECT tunnel is never opened.
- Rate limit is 100 per 10 seconds per destination. Over the limit gets 429.
- CONNECT is a raw byte relay. Count bytes. Never look inside. No TLS decryption. No MITM. One metadata row per tunnel. A failed connect is 502 plus a zero-byte row.
- Preserve a trace ID the proxy receives. Do not inject one into a downstream call.
- Outcome accounting must always reconcile: allowed plus blocked plus rate_limited plus other equals total attempts.
- Register a live tunnel before answering 200. Re-check Pause after registration and before 200, so a Pause that landed while the tunnel was still connecting cuts it instead of opening it. Redirect is not handled in that re-check. A route changed under a connecting tunnel needs its own test and its own decision.

## Cost rules

- Blocked, rate-limited, paused, redirected, stopped, verified, and
  warned runs always cost $0.00.
- A nonzero recorded cost always wins for a real attempt row. A
  control row carries the total it notes, and costs $0.00.
- Loopback is $0.00.
- Everything else stays Unverified, never guessed.
- The warn threshold is rounded to cents. Money is counted in cents,
  and an unrounded 80% threshold can be unreachable in float.
- Automate price data where coherent: auto-detect model and destination, auto-fetch published vendor prices into a local cached price file, update on startup or periodically. Offline fallback is Unverified, never guessed. No manual price tables.

## User protection

Local first by default. No bodies, keys, or scores. Stops unless a human verifies everything is fine. Open design plus defensive publication. AGPL core to stop SaaS capture. Trademarked name. Paid cloud only for sync and sharing, never for the verdict itself.

## Dev versus consumer

- App mode is one `.exe`. Double-click it. Proxy and API start inside Iris. The dashboard sits inside the Iris window, not in a browser, not as a separate proxy launch. No `.bat` for normal use.
- Test buttons, diagnostics, and dev tools are not in the final consumer app. Keep dev-only helpers in a separate dev-only file or panel, or remove them before push and final. Never put them in the consumer dashboard.
- Consumer launch is a desktop shortcut to Iris.exe.

## Names

- License is AGPLv3.
- Intended mark is Iris Guardian. Tier B filing is $1,400. No filing made yet. Get a lawyer before filing.
- Do not use: Eidolon, Argus, Heimdall, Valkyrie, Odin, Agentia Iris Guardian, Veridion, Verilock, Verilok, Veriq.
- Name survivors, with holes still open: Veriloc, Checkloq, Loqward.

## Docs

- README is a quick summary. Keep it current. No stale milestones.
- `docs/` is so detailed that a new agent can know where Charles's head was: decisions, dead ends, open questions, next steps.
- Update docs in the same change as code, not later.
- Use files as memory. Write a handoff file before context compacts.

## Gotchas

- Do not assume a file you cannot see is committed.
- Do not treat a demo trace as a real trace, a listing as availability, or a spec as a build.
- Platform matrix is part of the bench, not an afterthought. Windows and Ubuntu CI with one Python version is not a matrix.
