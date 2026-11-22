# CI fix-forward for PR #36

PR #36 merged on Charles's word. CI run #79 then failed 9/15.
This fixes the 6 failures. Test code only. No product code
changes. Suite stays 213 tests.

## Failure 1: Windows, all five jobs

PanelPolishRefusalTest tears down wrong. setUp registers
cleanups in this order: restore DB_PATH, close the DB
connection, close the server, delete the temp dir. addCleanup
runs last-in-first-out, so the temp dir delete runs FIRST,
while the DB connection is still open. Windows refuses to
delete a folder holding an open file (WinError 32, file in
use; WinError 267 on 3.10). Linux and macOS let an open file
be unlinked, so it passed there. The sibling class closes
its connection before the temp delete and passed on Windows,
which confirms the cause.

Fix: register the temp dir delete first, so it runs last.
Teardown becomes server close, connection close, temp delete,
then restore. The tests themselves passed; only teardown
failed.

## Failure 2: Ubuntu 3.11, unittest only

test_spend_cap_warns_80_blocks_100, final reconcile:
counts sum 20, rows 21. The test reads the counts, then
reads the rows, two separate reads. The proxy journals a
tunnel row when the tunnel closes, on its own thread, up to
half a second after the close. A row can land between the
two reads. It passed locally and under pytest in the same
job, so it is a race, not a wrong count.

Fix: read both from one connection inside one read
transaction, so both see the same snapshot. The assert
keeps its meaning. The transaction lasts two quick reads;
the writer waits on the busy timeout if it lands inside.

## Decisions

- 2026-11-22, test-only fix, no product change. Rejected:
  changing the proxy journal timing, which would move a
  real behavior to suit a test.
- 2026-11-22, one read transaction for the reconcile.
  Rejected: counting outcomes from the same row list, which
  makes the assert true by construction and drops the check.
- 2026-11-22, Windows fix is cleanup order only. Rejected:
  closing the connection inside each test, which spreads
  teardown across three tests instead of fixing setUp once.

## Non-goals

No panel change. No proxy change. No spend-cap change.
No demo change. No new tests. No count change.
