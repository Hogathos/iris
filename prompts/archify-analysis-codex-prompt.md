# Archify analysis (Codex)

## Task

Read the Archify repository (public, https://github.com/tt-a1i/archify) and write up how it works and what Iris could take from it.

Archify turns an idea, a plan, or a codebase into an interactive diagram or a one-page answer. It is an agent skill for Claude Code, Codex, Cursor, and more. Iris turns an agent's run into something verifiable, and the swarm map already diagrams a run. The question is what Iris can take from Archify's approach.

## Challenge the frame

Before you start, if a simpler or sturdier way to answer this exists, say so once up front, then do what this prompt asks.

## Steps

1. Clone https://github.com/tt-a1i/archify into a temporary directory (do not commit it into the Iris repo). Read the README, the skill definition, and the core code that turns an input into a diagram or a one-page answer.
2. Answer these four questions in the report:
   - How does Archify turn an idea, plan, or codebase into an interactive diagram or a one-page answer? Describe the pipeline and the mechanism, not just the marketing.
   - What are its key design choices? What does it show, what does it leave out, and how does it decide?
   - What could Iris take from this for the swarm map and for diagramming a Run? Be concrete: how would you diagram a Run (its tool calls, destinations, verdicts) the way Archify diagrams a codebase? Name the specific ideas that transfer.
   - Where does the analogy break? What does Archify do that Iris should not copy, and why?
3. Write the report to `prompts/archify-analysis-codex-report.md` in this repo. Plain words, short sentences. Lead with the findings, then the detail.

## Constraints

- Read-only analysis. Do not change any Iris code, tests, or config. The only file you add is the report.
- Do not commit the Archify clone or any of its files into the Iris repo.
- Do not invent Archify features or Iris features. Ground every claim in what you read in the repo.
- If a part of Archify is unclear from the repo alone, say so in the report and name what would settle it.

## Report

Write the report to `prompts/archify-analysis-codex-report.md`. Include: what Archify does and how, the design choices, what transfers to Iris (concrete), where the analogy breaks, and anything you could not verify.
