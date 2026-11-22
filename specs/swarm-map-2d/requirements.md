# Swarm map, 2D — requirements

Date: 2026-11-22. Status: planned, Charles approved the scope
2026-11-22. Part of: devtool panel prompt, step 6, after the buttons.

## What it does

A dev-only 2D map of the swarm, at `/map` on the dev panel, loopback
only. It shows what Iris sees as a picture, not a table: one node per
Run, one node per destination, one edge per Run that called that
destination. Clicking a Run node opens that Run's page. Clicking a
destination node opens the run list filtered to that destination.

Nodes are Runs, not named agents. The journal carries no agent
identity, so the map never invents a name for one. A node reads
"Run XXXXXXXX", the same short name the run list uses.

Time runs left to right. A node's place on the x axis comes from the
time of its first journal row. Each node sits on its own lane, so no
two nodes share a place. Edge color follows the outcome: green for
allowed, red for blocked or failed, amber for rate-limited or retry.
Each edge reads how many calls it carries and their seconds in total.

The map shows the latest 20 Runs and says so on the page. It is a
read of the journal, nothing more. It collects no new data.

## Acceptance criteria

1. `/map` returns 200, one inline SVG, zero JavaScript on the page.
2. Same journal gives the same node places, every time.
3. No two nodes share a place.
4. Every Run node links to its run page. Every destination node
   links to the run list filtered to that destination.
5. Edge colors match outcomes: green allowed, red blocked or
   failed, amber rate-limited or retry.
6. Full suite stays green, both runners, lints clean.

## Non-goals

3D map, or a 3D toggle. Verify button. Live updating: the page
refreshes, nothing polls. Force-directed layout. App mode, consumer
app, .exe. Named agents. New data collection.

## Decisions

- 2026-11-22, server-rendered SVG, zero JavaScript. Choice: the
  server builds the SVG, forms and links only, the same as the panel.
  Rejected: a JS canvas with a force layout, because the panel spec
  already decided zero JavaScript, and a force layout puts the same
  journal in different places on different loads. Why: a read of the
  journal must be reproducible.
- 2026-11-22, time on x, one lane per node on y. Choice: x comes
  from the first row's time, each node keeps its own lane. Rejected:
  two columns, Runs left and destinations right, because it drops the
  time story the prompt asked for. Why: time order is the first thing
  a human reads in a swarm.
- 2026-11-22, nodes are Runs. Choice: a node is a trace_id, shown
  as a short Run name. Rejected: inventing agent names, because the
  journal carries no agent identity. Why: the map must not claim what
  the journal does not say.
- 2026-11-22, latest 20 Runs. Choice: the same cap the run list
  already uses. Rejected: every Run ever, because the page grows
  without bound. Why: a map that never ends is not a map.

Resumed 2026-11-22, after the Verify button merged (PR #31) and its
audit fix merged (PR #32). Test file is back in tests/. One test
added at resume: the run list dest filter reads every row, so a Run
that called a destination on any row shows under that filter, not
only when its latest row is that destination. The edge color rule
also names retry as amber, though no attempt row carries a retry
outcome today.
