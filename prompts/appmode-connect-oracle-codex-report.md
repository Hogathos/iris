# Muse Code live-traffic oracle

Run on November 27, 2026, at `4d222c1`. The check used the Connect backend, a fresh headless Muse Code process, and the Iris journal.

| Phase | Result |
| --- | --- |
| 0. Setup | The settings file existed. The Iris backup was absent. The journal contained 0 rows. |
| 1. Interception | Passed. Connect created the backup. Iris recorded 21 new `CONNECT` rows. All returned HTTP 403 with outcome `blocked` and reason `not_allowlisted`. |
| 2. Forwarding | Skipped as requested. The stated quota reset was November 29, 2026, at 19:00 EST. |
| 3. Restore | Passed. Disconnect removed the backup. The settings hashes matched. Muse Code restarted in local echo mode and exited with code 0. The proxy stopped and port 8888 closed. |

The journal contained 21 rows after the run. The one distinct destination value was `api.meta.ai:443`.

Muse Code reported `transport error: error sending request for url` and retried. The run stopped the process at 55 seconds. Muse Code did not report a quota error.

| Settings SHA-256 | Value |
| --- | --- |
| Before Connect | `C82795424C3E2CDBC4F6B6D7525DA1ED9C8C86D951359A542CB9694059BE9B6E` |
| After Disconnect | `C82795424C3E2CDBC4F6B6D7525DA1ED9C8C86D951359A542CB9694059BE9B6E` |

Live forwarding and the app-mode Connect button remain Unverified.
