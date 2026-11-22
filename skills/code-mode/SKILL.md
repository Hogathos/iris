# Code mode: decide, then write code that does the work

The model decides. Code executes. Never do by hand, in context, what a script can do.

Source: Anthropic's MCP "code mode" pattern, via rari (@0xwhrrari, Nov 21, 2026) — an agent's tool context cut from 150,000 tokens to 2,000 by writing code that calls tools instead of calling them one by one. This skill is the behavior half of that pattern. It cannot rewire the tool system itself — tools are not files the agent can list and open. It shapes behavior, not runtime architecture.

## When to use

Any task where the agent would otherwise make many similar tool calls, read large amounts of data into context, poll or retry repeatedly, or handle sensitive data.

## The six parts

- **Load on demand.** Open only the files and tools the task needs. Do not load every tool definition or read every file up front. The model sees what the decision needs, nothing more.
- **Filter in code.** When the data is large, filter it in the script and bring back only the result. A 10,000-row sheet gets filtered in the sandbox; the model sees 5 rows. Never paste a raw dump into context and sift it by reading.
- **Loops in code.** Polling, retries, and repeated checks run as a loop in the script, not as repeated model round trips. Sleep in code, not in turns. Every loop states its bound (max iterations or timeout) before it runs.
- **Private by default.** Redact sensitive values (emails, phones, keys, tokens, addresses) to placeholders before they enter model context — [EMAIL_1], never the address. The real data stays in the script's local handling. The model never sees a body or a key.
- **Save working code as skills.** When a script works and is reusable, save it with a SKILL.md so the next run opens it instead of reinventing it. The agent builds its own toolbox.
- **The gate.** No outside effect runs without a human sign-off in words. No send, no write to a shared file, no commit, no publish, no tool call that changes state outside the sandbox, unless the human approved it. Code mode makes the agent faster, not more trustworthy. The sign-off is the product.

## Checks, not vibes

Every rule above is a check that can fail:

- A run that reads more than a page of data must name the filter it used — what the script returned versus what existed.
- A loop with no stated bound does not run.
- An outside effect with no named approval (whose words, when) stops, and the agent asks.
- A script that handles sensitive data must show the redaction it applied before its output reaches the model.

If a check cannot be written, the rule is a wish, not a rule.

## Limits

- This is the behavior half only. The architecture half — tools as files in a folder, loaded on demand — needs runtime support the agent does not have.
- Code mode does not verify the code the agent wrote. A wrong script run without a check is the same failure as a wrong tool call, just faster and cheaper. Verification (read-back, test-first, human sign-off) still applies.
