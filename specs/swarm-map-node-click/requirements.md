# Swarm map node click + color

## Problem, one sentence
The map's Run nodes are hollow 8-pixel circles, so only their 1-pixel
outline is clickable, and they carry no color, so the map does not
read at a glance.

## Intention / done definition
- A Run node's whole disc is clickable: the circle has a fill, so a
  click anywhere inside it follows the Run link.
- A Run node's fill color is its worst outcome: green allowed, amber
  rate-limited or retry, red blocked or failed, the same precedence
  the edges already use (red over amber over green).
- A destination node's square keeps its shape and gets a white fill,
  so its interior clicks too, the same hollow-shape trap as the Run
  circle.
- The map still renders zero JavaScript, still caps at the latest 20
  Runs, still places nodes by time.

## Decisions
- 2026-11-22, fill + color the node, not a bigger invisible hit
  target: a visible filled disc is the honest target, and the color
  carries the outcome a viewer needs. Rejected: an invisible
  transparent circle on top, which hides the real target size.
- 2026-11-22, node color reuses the edge color rule (_row_color
  precedence): one rule for color everywhere on the map, not a
  second rule that can drift from the edges.
- 2026-11-22, no jitter and no fake spread: nodes keep their true
  time places. The spread comes from paced demo traffic, not from
  moving nodes.

## Non-goals
- No change to the index table, the Run page, or any Steer control.
- No change to the 20-Run cap or the time mapping.
- No seeded or fake traffic: the demo map is drawn from real runs.
