# Devtool panel polish — requirements

Problem in one sentence: the panel hides words a human already typed,
refuses a cap or a block without saying what form the destination takes,
and a filter can miss a Run that exists past the latest 100 traces.

## Criteria

1. A VERIFY gate row shows the words the human typed. The What column
   for the row shows the journal reason, `verify ok: <words>`, instead
   of `unverified: nothing checked this yet.`
2. A refused cap or block names the form. The refusal page says the
   destination is a label Iris already allows, like `127.0.0.1:18080`,
   not a web address. The Spend cap and Block forms hint the same form
   beside the destination input.
3. A filter searches past the latest 100. With any filter set, the run
   list reads the latest 1000 traces and still shows at most 100
   matches, so blocked-only finds a blocked Run older than the latest
   100. Unfiltered, the list still reads 100 and shows at most 100.

## Decisions

- 2026-11-22, VERIFY words: show the journal reason verbatim
  (`verify ok: <words>`). The page shows what the journal says, one
  source of truth. Rejected: showing only the words after the prefix,
  which drops the `verify ok` marker that says the gate passed.
  The check sits after the `verified` outcome branch, so a read-back
  verdict row keeps its own words.
- 2026-11-22, refusal wording: name the form with an example. The
  setter still takes a destination label and still refuses a
  non-allowlisted host; nothing about what it accepts changes.
  Rejected: making the setter parse URLs. A URL for a non-allowlisted
  host still refuses, so parsing would only move the confusion.
- 2026-11-22, filter reach: raise the `list_traces` ceiling from 100
  to 1000, and read 1000 when a filter is set. The shown list stays
  capped at 100. Rejected: panel-side direct SQL for filtered views,
  which would duplicate the summary query; rejected: an unbounded
  fetch, which a long session's database could make slow. A filter
  that still misses past 1000 is a known limit, not a silent one.
