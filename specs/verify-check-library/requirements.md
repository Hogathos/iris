# Verify check library — requirements

Date: 2026-11-28. Status: approved by Charles 2026-11-28.
Part of: build order item 3 (Verify check library). First profile: secrets lane.

## What it does

A catalog of checks. Each check is a case: a plain-words claim, the steps
to test it, what evidence counts, and its source with license.

The first profile is the secrets lane: a bundle of adapted checks for
secrets handling, starting with secret redaction in logs (adapted from
SandHive, MIT). A library check runs through the existing Verify engine:
claim plus independent read-back plus a verdict (Correct, Incorrect, or
Unverified). Incorrect stops the Run.

Property-based tests over the Iris state machine: every transition
reachable, no invalid states.

Drill 1 (Partial write) runs end to end: the agent writes a config file
with 10 settings, the drill removes 3, Iris must say Unknown or
Incorrect, never Correct.

## Acceptance criteria

1. The catalog holds checks as structured data (claim, steps, evidence,
   source, license). A check with a missing source or license does not load.
2. The secrets profile contains the secret-redaction-in-logs check,
   adapted from SandHive, and it runs through the Verify engine with a
   verdict.
3. A library check's verdict follows the existing Verify rules: Correct
   only when the read-back matches the claim; Incorrect when the
   read-back contradicts it (and the Run stops); Unverified when the
   read-back cannot be done.
4. The journal records each check's source and license (provenance per
   case).
5. Property tests cover the state machine: every transition reachable
   from the start state, no transition to an invalid state.
6. Drill 1 passes: the verdict is Unknown or Incorrect, never Correct,
   when 3 of 10 settings are removed after the write.
7. Full suite stays green, no failing or skipped test undisclosed.
   Outcome accounting reconciles.

## Non-goals

- Other profiles (session revocation, upload containment).
- Threat-library conversion (AgentDojo, Halluci-Action, tau2/AppWorld,
  LLMail-Inject, CVE-Bench). Deferred, not dropped — see the check
  library file's "Suggested build order" section.
- The full model-based-testing walk (model built from the journal,
  coverage check).
- Compliance packs, cage, taint tracking, swarm map.
- New read profiles. Local file plus SQLite read-back only.
- Any change to the Verify verdict rules or the human-visible wording.

## Decisions

- 2026-11-28, one slice, not two. The catalog plus secrets check, the
  property tests, and Drill 1 land in a single slice. Test-first
  de-risks the SandHive adaptation (the failing test for the secrets
  check is written before any implementation), so the two-slice safety
  net is redundant. One PR, one review, one merge; the property tests
  and Drill 1 are the falsification for the catalog, built in.
- 2026-11-28, the tests are reviewed by Codex (not Claude) before
  implementation, because Codex runs on the Dell with DB access: it
  can run the tests against the real engine and the real journal,
  not just read them. Any hole it finds becomes a new test or a
  modification to an existing test, and Charles re-approves the
  updated tests before implementation.
