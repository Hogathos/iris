# Blind reviewer — requirements

## What it does

A fresh-context blind reviewer joins the review pass. The reviewer is an agent that has not seen how the implementation was built. It gets only the original task, the acceptance criteria, and the exact diff. It never sees the implementer's summary. It is read-only. It is asked to falsify, not to confirm.

Two reviewers run in parallel, blind to each other, on different model tiers. Main synthesises their findings. Agreement between the two reviewers is the signal. A finding only one reviewer raises is flagged for Main to judge.

## Acceptance criteria

- Each reviewer gets a fresh session. The reviewer has no context from the implementation.
- Each reviewer gets the same input: the original task, the acceptance criteria, and the exact diff. Neither reviewer sees the implementer's summary. Neither reviewer sees the other's findings.
- Each reviewer is read-only. It cannot change code.
- Each reviewer is asked to falsify: write the test that proves the implementation wrong.
- The reviewer prompt is reusable. Charles pastes it into a fresh session. It is the same prompt for both reviewers.
- Main synthesises the two reviewers' findings. A finding both reviewers raise is the signal. A finding only one reviewer raises is flagged for Main to judge.
- The blind review runs when the plan gate applies: 2 or more modules, or a new feature, or a behaviour change. A one-file, few-line fix with no new behaviour does not get a blind review.
- The review informs. It does not block. The one exception is a catastrophic finding (a security hole, data loss, a broken core invariant). A catastrophic finding blocks the change. Main flags it as a stop.
- Repo AGENTS.md documents the blind review step: when it runs, what the reviewers get, that it informs, and the catastrophic exception.

## Non-goals

- Automating the review as a watcher prompt. That is a follow-up after Muse Code usage returns (Nov 29).
- A Muse Code Stop hook. The Stop layer stays CI plus Main's verification gate.
- Mutation testing plus Hypothesis properties. That is a separate steal.
- Changing the review pass itself. Main's falsification stance and the unplanned-files check stay. The blind reviewer is the vehicle for falsification, not a replacement.

## Decisions

- Two reviewers, blind to each other, on different model tiers. (Charles, Nov 25.)
- Tiered: the blind review runs when the plan gate applies. A one-line fix does not get a blind review. (Charles, Nov 25.)
- Informs, does not block. The 80/20 rule: fix the 20 percent that is bad, keep the 80 percent that works. The one exception is a catastrophic finding, which blocks. (Charles, Nov 25.)
- Pairing: Claude first, Sonnet (medium effort) plus Haiku (medium effort), because it is simpler and cheaper. Haiku low is the cheaper option if medium proves overkill. Fallback if the Haiku tier cannot be selected on free Claude: Claude (Sonnet) plus Anti-Gravity (Gemini). (Charles, Nov 25.)
- Trial run: the hooks implementation (PR #63, now on main) is the first candidate diff. Charles runs the two reviewers by hand on that diff and brings the findings to Main.
