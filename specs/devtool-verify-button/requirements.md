# Verify button — requirements

Date: 2026-11-22. Status: planned, Charles said build it first
2026-11-22. Part of: devtool panel prompt, step 5, after the Red
button, before the swarm map.

## What it does

A dev-only Verify button on the Run page, for a Run that is paused
or redirected. The human types what they checked, in plain words,
and presses Verify. The button stamps the gate: only a fresh Verify
after the pause or redirect allows a retry. A Correct verdict alone
never unblocks a retry, and this button changes nothing about that.

The button calls the proxy's own verify gate, through the same seam
as the Pause button. The typed words become the human-approved done
clause: approved by human, read back is what the human typed. An
empty read-back is refused, and a Run that is neither paused nor
redirected is refused. A refusal changes nothing and says so on its
own page.

The button shows only where the gate can accept it: a paused or
redirected Run. A flowing Run shows no Verify button. A stopped Run
shows none either: the panel cannot tell from the rows alone whether
the Run has a fresh approval after the Red button, so it does not
offer what it cannot check.

## Acceptance criteria

1. Verify form on the Run page for a paused or redirected Run: one
   field for the read-back, one Verify button.
2. Pressing Verify with words stamps the gate: a retry after it is
   allowed and journaled. The journal carries a verify ok row.
3. Pressing Verify on a flowing Run is refused with 400, and the
   page says refused. Nothing is stamped.
4. Pressing Verify with an empty read-back is refused with 400, and
   the page says refused. Nothing is stamped.
5. With no proxy beside the panel, the route answers 503, the
   watch-only page. Same shape as the Pause button.
6. Full suite stays green, both runners, lints clean.

## Non-goals

Verify read-back of a claim: the panel holds no claim, so the button
stamps the gate only and reads nothing back. Red-button-approved
Runs: the panel cannot see the approval, so the button stays off for
stopped Runs. Verify button on the run list: the Run page is its
home. Swarm map. App mode.

## Decisions

- 2026-11-22, the typed words are the done clause. Choice: the form
  takes the read-back, the seam builds the clause with approved by
  human. Rejected: a fixed canned clause, because the clause must say
  what this human checked on this Run. Why: the human approves the
  clause by typing it.
- 2026-11-22, the button stamps the gate only. Choice: it calls
  verify_run, never verify_read_back. Rejected: reading a claim back
  from the button, because the panel holds no claim to read. Why:
  verdict and permission stay separate by design.
- 2026-11-22, the button shows for paused and redirected Runs only.
  Choice: the Run page reads the run state and shows the form for
  PAUSED and REDIRECTED. Rejected: showing it for stopped Runs,
  because the panel cannot tell a fresh approval from the rows. Why:
  do not offer what cannot be checked.
