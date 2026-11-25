# Iris app mode — Prescriptive spec

Built from the deslopified scratch paper: Design 1 (consumer app shell) and Design 2 (devpanel look). Verified against the research and the real code (main `ceafc73`). Every word below is the exact word on screen, unless it is in {braces} (filled in by Iris) or marked as a code value. No design choice is left open. If a line here conflicts with the designs, this spec wins.

## Decisions (locked)

These are the decisions that shape the spec. They are not open.

1. **Verify is the gate.** The human types what was checked, in plain words, and stamps approval. That lets a paused or redirected Run go again. The claim-checker (Iris looks at the result itself and returns a verdict) is the Verify check library, Part 2, queued. It is not in this spec.
2. **The UI word for the third verdict is "Unverified".** "Unknown" is the code verdict only. It never appears on screen.
3. **A Run is labelled "Run " plus the first 8 characters of its trace id.** There is no sequential Run number in the code. The panel already does this.
4. **The word is "journal", not "record".** The panel code says "journal". The menu, the dialogs, and the file name all say "journal".
5. **Loopback security (F21).** Every action the panel posts carries a per-launch token. The server checks the token and the Host header. A post without the right token is refused. This is a requirement, not a design choice.
6. **Spending limit.** When a daily limit is set, Iris blocks any call whose cost it cannot work out, unless the human has allowed that destination. No limit is set by default, so this block only applies when the person has set a limit. **Allowing a place is one click, in context.** When a call is blocked, a notice strip shows `Iris blocked a call to {place}. It could not work out what the call costs, and you have a daily limit set.` with **Allow {place}** and **Dismiss**. One click adds the destination, no typing, no confirmation dialog, then the strip reads `{place} is allowed. Calls to it will not count toward your limit.` with **Undo**. The blocked call does not retry by itself. The Run detail shows the same button. The Limits dialog shows a **Recently blocked** list with an **Allow** button per destination. The Add dialog (typing an address by hand) is the fallback, not the main path. (Charles, Nov 25: the human chooses, but the process must be as friendly as possible.)

## 0. Shape

- A native PySide6 shell. The centre is the existing HTML panel in a `QWebEngineView`. The panel loads from the loopback panel server in the same process, at `http://127.0.0.1:{spot}/?tier={tier}`. Never `setHtml()`. No `QWebChannel` in v1.
- The proxy and the panel server run in-process on a worker `QThread`. They stop explicitly in `closeEvent`. The teardown order is the PR #52 order: stop the controller, remove the view container from the layout and delete it synchronously, then delete the view. Container first, never reverse.
- Loopback only. Never `0.0.0.0`. The consumer build does not ship the Developer pages or routes at all. Hiding them with CSS is not enough.
- Verify, Pause, and Emergency stop are native Qt buttons outside the web view. The panel has no copies of them. The panel may point at them in words. It is never the only way to use them.
- The product name on screen is "Iris".
- The window title is `Iris`. Default size 1100 by 720. Smallest size 860 by 560.

## 1. Window, top to bottom

1. **Menu bar** (native). Two menus: Iris and Help.
2. **Toolbar** (native). Watching switch, Verify, Pause, More detail switch, Emergency stop.
3. **Notice strip** (native). Hidden unless something needs the person. One strip at a time.
4. **Web view** (the panel). Run table on top. Run detail below it. Empty state when there are no Runs.
5. **Status bar** (native). One sentence on the left. Cost on the right.

## 2. First run

Shown once, on the first launch only. One screen. No account. No tour. No steps. The loading state (section 3) comes first, then this screen fills the window.

**Layout, top to bottom.** One column, left aligned, about 560 px wide, in the upper third of the window. Normal text size. Line height 1.6. Gap between paragraphs 24 px. No headline above it. No image. No cards.

**Words, exactly:**

> Iris watches what your AI agent does on this computer.
>
> Everything stays on this computer. Iris needs no account.
>
> It sees where each tool call goes, how big it is, and how long it takes.
>
> When your agent says it did something, Iris looks for itself and tells you: Correct, Incorrect, or Unverified. Unverified means Iris could not tell. Iris will tell you if an action is unverifiable, and ask you what to do.

Then one ordinary button, left aligned, standard size, with this line beside it:

- Button: **Run the demo**
- Line beside it: `The demo runs on this computer only. It costs $0.00.`

Then one plain text link under it: **Skip the demo and open the dashboard**.

**Behavior.**

- Click **Run the demo**: this screen is replaced by the dashboard at once. The demo starts running on the dashboard. The person sees the demo happen (section 4, demo). The demo does not start by itself before the click.
- Click **Skip the demo and open the dashboard**: the dashboard opens with its empty state (section 4).
- Closing the window on this screen: quits Iris. The welcome screen shows again next launch, because the person never got past it.
- Neither path asks for a name, an email, a permission or a choice.
- The SmartScreen words ("More info, then Run anyway") and the file hash belong on the download page and the installer, not in this app.

## 3. Loading state

Shown while the loopback panel server starts and the web view loads.

- Native label, centered in the window: `Starting Iris…`
- Under it, a thin indeterminate bar.
- The toolbar and status bar are already on screen but disabled.
- After 10 seconds still loading, the label becomes: `Iris is taking longer than usual.` Under it: button **Try again**.
- If the panel server cannot start, see "Errors" in section 7.

## 4. The dashboard, Simple tier

### 4.1 Menu bar

**Iris menu**

- `Run the demo`
- `Copy the address for your agent`
- `Save a copy of the journal…`
- (line)
- `Limits…`
- `Settings…`
- `Developer page` (only with More detail on)
- (line)
- `Close Iris`

**Help menu**

- `What do the verdicts mean?` (opens a small window with the verdict words below)
- `Check for updates` (opens the Iris downloads page in the browser)
- `About Iris`

**Verdict words window** (title: `What the verdicts mean`):

> **Correct.** Iris looked, and what it found matches what the agent said.
>
> **Incorrect.** Iris looked, and what it found does not match what the agent said.
>
> **Unverified.** Iris could not look, or could not tell. Iris never guesses.

Button: **Close**.

### 4.2 Toolbar, left to right

| Control | Label | State words | Tooltip |
| --- | --- | --- | --- |
| Switch | `Watching` | `On` / `Off` | On: `Iris is watching your agents. Click to turn watching off.` Off: `Watching is off. Click to turn it on.` |
| Button | `Verify` | none | `Check the selected Run. You type what was checked, in plain words, and stamp approval.` |
| Button | `Pause` (becomes `Resume` when the Run is paused) | none | Pause: `Hold the selected Run. Its next call waits until you click Resume.` Resume: `Let the selected Run go on.` |
| (space) |  |  |  |
| Switch | `More detail` | `On` / `Off` | `Shows how Iris reached each verdict, and a few extra controls.` |
| Button | `Emergency stop` | none | `Block every call from every agent that uses Iris. Nothing gets through until you turn it back on.` |

- Emergency stop is the rightmost control. It is the only solid red button in the window. It is always visible and always enabled, except when Emergency stop is already on.
- Verify and Pause need a selected Run. With none selected they are disabled, with the tooltip: `Pick a Run in the table first.` If exactly one Run is running and none is selected, that Run is selected for the person.
- In the Advanced tier one more button appears after Pause: `Send somewhere else` (section 6).

### 4.3 Run table (in the web view)

Newest Run first. One row per Run. Click a row to select it. Arrow keys move the selection.

| Column | Header | Header tooltip | Cell words |
| --- | --- | --- | --- |
| 1 | `Run` | `One job your agent does, from start to end.` | `Run {trace prefix}`. The demo shows `Run {trace prefix} · Demo`. |
| 2 | `Started` | `When the Run's first call reached Iris.` | `{time}` today, or `{date} {time}` |
| 3 | `Status` | `Where the Run is now.` | `Running`, `Paused`, `Stopped`, `Finished` |
| 4 | `Verdict` | `Whether the agent's claims check out. Correct, Incorrect, or Unverified. Unverified means Iris could not tell. Iris never guesses.` | `Correct`, `Incorrect`, `Unverified`, or `Not checked yet` |
| 5 | `Cost` | `What this Run cost, when Iris can work it out. Otherwise it says Unverified.` | `$0.00` or `Unverified` |

**Status cell tooltips:**

- Running: `The agent is working.`
- Paused: `You paused this Run. Its next call is waiting.`
- Stopped: `This Run can't make more calls.` The reason shows in the Run detail.
- Finished: `The agent is done.`

**Verdict cells** show a mark and the word. ✓ Correct. ✕ Incorrect. ? Unverified. Never color alone.

**Verdict for a whole Run** is the roll-up of its claims.

- Any claim Incorrect: `Incorrect`.
- None Incorrect, any Unverified: `Unverified`.
- All claims Correct: `Correct`.
- No claim checked yet: `Not checked yet`, in plain gray text, with no mark.

**Empty state** (no Runs yet), shown in place of the table:

> No Runs yet. A Run shows up here when your agent makes its first call through Iris.
>
> To try Iris now: **Run the demo**
>
> To watch your own agent, give it this address: `http://127.0.0.1:{spot}`  **Copy**
>
> Paste it where your agent asks for the address of its AI service.

- **Run the demo** here is a plain button in the panel. It does the same as the menu item. It is not a safety control, so it is allowed in the web view.
- **Copy** puts the address on the clipboard. The button then reads `Copied` for 2 seconds.
- `{spot}` is the number Iris listens on. It is never written in Iris words. The person only sees it inside the address.

### 4.4 Run detail (in the web view, under the table)

Shown for the selected Run. If nothing is selected: `Pick a Run to see what happened.`

**Top line:** `Run {trace prefix}`, then the Status word.

**Summary sentence** (one of these):

- Running, nothing checked: `The agent is working. Iris has not checked any claims yet.`
- Running, all so far Correct: `The agent is working. Every claim so far is Correct.`
- Paused: `You paused this Run. Its next call is waiting.`
- Stopped by Iris: `Iris stopped this Run. A claim was Incorrect.`
- Stopped by Emergency stop: `Emergency stop ended this Run.`
- Finished, all Correct: `The agent finished. Every claim is Correct.`
- Finished with an Unverified: `The agent finished. Iris could not check every claim.`

**Section: What the agent said.** A table with three columns:

- `The agent said`
- `Verdict`
- `What Iris found`

One row per claim. The "What Iris found" cell is one plain sentence. Examples are in the demo below. For an Unverified the sentence says why Iris could not tell.

**Section: Where the calls went.**

- Simple tier: one sentence, such as `3 calls. All of them stayed on this computer.` or `{n} calls to {m} places.`
- A tooltip on `calls`: `Each time the agent reached out to something.`
- If a call was blocked because Iris could not work out its cost and a limit is set, the detail says so in plain words: `Iris blocked a call to {place}. It could not work out what the call costs, and you have a daily limit set.` Beside it, a button: **Allow {place}**. It does the same as the notice strip button: adds the destination to the allowed list at once, no confirmation, no typing.

**Section: Cost.**

- Demo or all-local Runs: `$0.00`, then `This Run stayed on this computer, so it cost nothing.`
- When Iris can work it out: `{amount}`, then `Iris worked this out from what it saw.`
- When it cannot: `Unverified`, then `Iris could not work out a cost. It does not guess.`

The detail has no buttons except the **Allow {place}** button for a blocked call. Under the summary, one gray line: `To check or pause this Run, use the buttons at the top.`

### 4.5 Notice strip

One strip at a time, between the toolbar and the web view. Each strip has plain words and at most two buttons. The strips are:

- **Emergency stop is on.** Red. See section 5.
- **Waiting for you.** Amber. Only if the existing human gate holds a Run. Words: `Run {trace prefix} is waiting for you. The agent says it finished, and Iris could not confirm it.` Buttons: **Let it go on** and **Keep it stopped**.
- **Blocked a call.** Amber. Only when a daily limit is set and Iris blocked a call because it could not work out the cost. Words: `Iris blocked a call to {place}. It could not work out what the call costs, and you have a daily limit set.` Buttons: **Allow {place}** and **Dismiss**.
  - Clicking **Allow {place}** adds the destination to the allowed list at once. No confirmation dialog, no typing, the button is the choice. The strip then reads: `{place} is allowed. Calls to it will not count toward your limit.` with an **Undo** button. The blocked call does not retry by itself. The agent can try again, or the person can resume the Run.
  - Clicking **Dismiss** hides the strip. The destination stays blocked. The person can allow it later from the Limits dialog.
- **Port in use.** Amber. See section 7.

### 4.6 Status bar

Left, one of:

- `Watching. {n} Run is running.` / `Watching. {n} Runs are running.` / `Watching. No Runs are running.`
- `Watching is off. Agents that use Iris can't reach anything.`
- `Emergency stop is on.`
- `Checking Run {trace prefix}…` (only while Verify works)
- The last result for 10 seconds, then back to the line above. Example: `Run {trace prefix}: Incorrect. See what Iris found below.`

Right: `Cost: {amount}`. Tooltip: `What your agents cost, when Iris can work it out.` The Run table already shows `Unverified` in the Cost column for any Run Iris could not price, so the status bar does not repeat it.

### 4.7 The demo, step by step

The demo is one Run, named Demo. It runs on this computer only.

1. The person clicks **Run the demo**. The dashboard shows. A row appears: `Run {trace prefix} · Demo`, started now, `Running`, `Not checked yet`, `$0.00`. The row is selected. The Run detail is open.
2. About 2 seconds apart, three claims appear under "What the agent said":

| The agent said | Verdict | What Iris found |
| --- | --- | --- |
| `I saved a note called "groceries".` | ✓ Correct | `Iris looked in the demo notebook. The note "groceries" is there.` |
| `I checked the weather service.` | ? Unverified | `That service is on another computer. Iris only looks at this computer, so it could not check.` |
| `I saved 3 notes in total.` | ✕ Incorrect | `Iris looked in the demo notebook. It holds 2 notes, not 3.` |

3. On the third claim Iris stops the Run. Status becomes `Stopped`. The summary reads: `Iris stopped this Run. A claim was Incorrect.` The status bar reads: `Run {trace prefix} stopped. The demo agent said it saved 3 notes. Iris found 2.`
4. Under the detail, a gray teaching block shows (only while no real Run exists): `That was the demo. It never left this computer. To watch your own agent, give it this address:` then `http://127.0.0.1:{spot}` and **Copy**, then `Paste it where your agent asks for the address of its AI service.`
5. Nothing interrupts the person during the demo. No popup. No sound.

The demo only works as a demo if the demo agent really saves notes and Iris really reads them back. A staged verdict would teach the wrong thing.

## 5. What each control does

### Watching switch

- Turn off: one click, no confirmation. Iris stops the watcher. Agents that use Iris can't reach anything. Status bar: `Watching is off. Agents that use Iris can't reach anything.` An agent that does not use Iris is not affected, and Iris can't see it.
- Turn on: one click. Status bar returns to `Watching. …`.
- Turning it off does not end the journal. Existing Runs stay in the table.

### Verify (the gate)

- Acts on the selected Run.
- Click: a native dialog opens. Title: `Verify`. Body: `What was checked, in plain words:` with a text field. Buttons: **Cancel** and **Verify**.
- The person types what they checked. Click **Verify**: Iris stamps the gate. The Run is marked verified. If the Run was paused, it can go again. If the Run was redirected, the redirect takes effect.
- Verify never asks for confirmation beyond the dialog itself. It only records the human's word.
- The claim-checker (Iris looks at the result itself and returns a verdict) is the Verify check library, Part 2, queued. It is not in this spec.

### Pause and Resume

- Pause holds the selected Run. Its next call waits. Status becomes `Paused`. The button becomes `Resume`.
- Resume: one click, no confirmation. Status goes back to `Running`.
- Pause on a Run that has ended: disabled, with the tooltip `This Run is over.`

### Emergency stop

- One click. No confirmation. No second click, ever.
- Result, all at once: every call from every agent that uses Iris is blocked. Every running or paused Run becomes `Stopped`. A red notice strip shows. The status bar shows `Emergency stop is on.`
- Strip words: `Emergency stop is on. Iris is blocking every call. Your agent may still be running. Close it yourself if you want it fully off.`
- Strip button: **Turn off Emergency stop…**
- Turn-off dialog. Title: `Let agents work again?` Body: `Agents that use Iris will be able to make calls again. Runs that were stopped stay stopped.` Buttons: **Keep it on** (the default) and **Let agents work again**.
- Emergency stop stays on if the person closes and reopens Iris. The strip shows at launch.
- Shortcut: Ctrl+Shift+E, when Iris is the active window.
- If the web view has crashed, Emergency stop still works. The status bar still updates.

### More detail switch

- Switching it on or off reloads the web view at the same selected Run. The panel server decides which parts to draw.
- The setting is remembered across launches.

### Save a copy of the journal

- Opens the native save dialog. Default file name `iris-journal-{date}`.
- Before saving, a small note in the dialog: `The file lists what each Run did and what Iris found. It holds no message text and no keys.`
- After saving, the status bar says `Saved.`

### Check for updates

- Opens the Iris downloads page in the browser. Iris does not update itself.

### Close Iris

- If no Run is running: closes at once.
- If a Run is running: a dialog. Title `A Run is still going.` Body: `If you close Iris, your agent loses its connection and the Run stops.` Buttons: **Keep Iris open** (the default) and **Close Iris**.
- On close, Iris asks the watcher to stop and waits up to 3 seconds. If it is still busy, Iris exits anyway. It never leaves a watcher running behind it.

## 6. Advanced tier (More detail on)

Same window. More detail is off by default. The demo starts with More detail off. The switch sits in the toolbar during the demo, so the person can flip it on and see the tabs.

These parts change when More detail is on:

- **Run table** gains two columns:
  - `Calls`. Tooltip: `How many times the agent reached out during this Run.`
  - `Went to`. Tooltip: `The places the calls went.` Cell: `this computer`, or the first place and `+{n} more`.
- **Run detail** gains three tabs beside `Claims`: `Calls`, `Why`, `Cost`. The `Claims` tab stays and shows the plain-language claim list (the "What the agent said" table, section 4.4). With More detail off, the detail shows the claim list only.
  - **Calls.** Same columns and words as the devpanel Calls tab (section 10.3). One row per call, oldest first. This is the metadata Iris keeps and nothing else. No body, no header, no payload.
  - **Why.** One numbered list per claim, `How Iris decided`. Example for the third demo claim:
    1. `The agent made a claim.`
    2. `Iris wrote the claim down before doing anything else.`
    3. `Iris looked in the demo notebook.`
    4. `It holds 2 notes. The agent said 3.`
    5. `Verdict: Incorrect.`
    6. `Iris stopped the Run.`
  - **Cost.** Same as the devpanel Cost tab (section 10.3). Top: `Cost {amount}` and the meaning line. Table, one row per call: `Call`, `Cost`, `How Iris got it`.
- **Toolbar** gains **Send somewhere else**. It needs a selected Run that is not over.
  - Click opens a native dialog. Title: `Send this Run somewhere else`. Body: `Pick where this Run's next calls should go.` Field label: `Send them to:`. Buttons: **Cancel**, **Next**.
  - Next opens the confirmation: `From now on, Run {trace prefix}'s calls go to {place} instead. Do this?` Buttons: **Cancel** (the default), **Send them there**.
- **Iris menu** gains `Developer page`. It opens the devpanel in the browser. It shows only with More detail on.

## 7. Settings, Limits, errors

### Settings (native dialog)

Title: `Settings`. Button at the bottom: **Close**.

- `Where Iris keeps its journal:` `{folder path}` and a button **Open folder**. Under it: `Everything Iris saves is in this folder. Nothing is sent anywhere.`
- `Updates:` `Iris never updates itself. Use Help, then Check for updates, to see if there is a newer version.`

Advanced block, shown only with More detail on:

- `The spot Iris listens on:` a number field and **Change**. Under it: `Your agent needs the new address after you change this.`
- `How long to keep the journal:` a list: `30 days`, `1 year`, `Until I delete them` (the default).
- **Delete the journal…** Confirmation. Title `Delete the whole journal?` Body: `This removes what every Run did and what Iris found. You can't get it back. Iris can't check past Runs after this.` Buttons: **Keep the journal** (the default), **Delete the journal**.

### Limits (native dialog)

Title: `Limits`.

- **Spending.**
  - Words: `Iris warns you at 80% of your daily limit and blocks calls at 100%.`
  - Field: `Daily limit:` a dollar amount field and a box `No limit` (checked by default).
  - Words under it: `Iris counts a cost only when it can work it out. A call with an Unverified cost is not counted, unless you allow that place below. When a limit is set, Iris blocks calls it cannot price, unless you have allowed that place.`
  - **Allowed places.** A list of places the person has allowed even though Iris cannot work out what they cost. Buttons **Add…** and **Remove**.
    - Under the list, a second list: **Recently blocked.** Every destination Iris blocked recently because it could not work out the cost, that is not yet allowed. Each row shows the place and an **Allow** button. One click adds it to the allowed list. No typing, no confirmation. This is the easy way to allow a place. The list is empty when nothing was blocked recently.
    - **Add…** is the fallback for typing an address by hand. It opens a small dialog. Label `Address of the place:`. Buttons **Cancel**, **Next**. Next shows: `Iris will let calls to {place} through even though it can't work out what they cost. They will not count toward your limit. Allow it?` Buttons **Cancel** (the default), **Allow it**.
    - **Remove** shows: `Stop allowing {place}? Calls to it will be blocked when a limit is set and Iris can't work out the cost.` Buttons **Cancel** (the default), **Stop allowing it**.
- **Blocked places.**
  - A list. Buttons **Add…** and **Remove**.
  - **Add…** opens a small dialog. Label `Address of the place:`. Buttons **Cancel**, **Next**. Next shows: `This will stop your agents reaching {place}. Block it?` Buttons **Cancel** (default), **Block it**.
  - **Remove** shows: `Let agents reach {place} again?` Buttons **Cancel** (default), **Let them**.
- Bottom buttons: **Cancel**, **Save limits**. Save shows: `Save these limits? Your agents are held to them right away.` Buttons **Cancel** (default), **Save limits**.

### Errors

- **The spot is taken.** Notice strip, amber. `Iris couldn't start watching. Another program on this computer is using the spot Iris needs.` Buttons **Try another spot** and **Close Iris**. After Try another spot: strip says `Iris is watching on a new address. Copy it and give it to your agent.` with **Copy address**.
- **The dashboard won't load.** Shown in the web view area: `The dashboard didn't load. Verify, Pause, and Emergency stop still work.` Button **Try again**. The native buttons stay live.
- **Iris is opened twice.** The second launch brings the first window to the front and exits. No message.
- **The watcher stops on its own.** Notice strip, red: `Iris stopped watching because of a problem. Agents that use Iris can't reach anything.` Buttons **Start watching again** and **Close Iris**.

## 8. What the consumer build must not contain

Developer is removed, not hidden. The build has none of these:

- the dev panel pages and their routes;
- any raw traffic, header or payload view;
- replay or resend of a call;
- test places, seed data, sample Runs;
- trace or debug log levels;
- any setting that opens the server beyond this computer, or turns off the token check;
- settings-file or environment overrides, and hot reload;
- API keys or remote management.

There is no link to the dev panel anywhere in the app.

## 9. Loopback security (F21, requirement)

- Every action the panel posts carries a per-launch token. The token is made fresh each time Iris starts. It lives in memory only. It is never written to disk, never logged, never shown on screen.
- The panel server checks the token on every POST. A post without the right token is refused with `This page is out of date. Reload it.` Nothing changes.
- The panel server also checks the Host header on every request. It only accepts `127.0.0.1` and `localhost` with the panel's own port. Any other Host is refused.
- This applies to the app panel and the dev panel. Both post to the same server.
- The token check and the Host check are on by default. There is no setting to turn them off, in any tier.

## 10. Devpanel look

The developer panel (`devtool.exe`). A page in a browser, served from this computer only (`127.0.0.1`, never `0.0.0.0`). It is the Developer tier. It is not part of the consumer build and is not linked from the app.

- Same content as the app, plus the developer-only parts: test places, sample Runs, log detail, settings in use.
- Same panel server. Same strings as the app wherever the same thing appears. Those strings must stay identical in both.
- Plain language still. No `trace_id` without "Run". No stat without its meaning. Verdicts are Correct, Incorrect, Unverified.
- The three safety controls are HTML buttons here, because there is no native window around the page. The app's native buttons exist so the controls survive a dead web view. This page has no such cover. The page says so if it loses contact with Iris (section 10.8).

### 10.1 How it looks

One stylesheet, shared with the panel inside the app. The two differ by density and by the extra pages, not by style.

Rules for the look:

- **No hero.** No headline larger than 18 px. No banner, no tagline, no welcome copy, no illustration.
- **No card grid.** No tiles, no boxed stat blocks, no three-across rows.
- **No call to action.** Buttons are ordinary buttons, sized like the text around them.
- **Flat.** Panes are separated by 1 px lines. No shadows. No gradients. No rounded corners over 4 px. No icons in circles. No emoji.
- **Type.** System sans, 14 px. Monospace only for addresses, file paths and Run IDs.
- **Color.** Light and dark follow the system setting. One accent color (blue) for selection and links. Verdict colors: Correct green, Incorrect red, Unverified amber. Each verdict also has a mark and the word: ✓ Correct, ✕ Incorrect, ? Unverified. Never color alone. Emergency stop is the only solid red button on the page.
- **Tables.** 32 px rows. Sticky header row. Numbers right-aligned in a figure font that lines up. Long values cut with an ellipsis and a tooltip.
- **Motion.** None. Rows update in place. A running check shows `Checking…` on the button.
- **Width.** Built for 1100 px and wider. Under 900 px the Run list stacks above the Run detail.
- **Keyboard.** Everything reachable by keyboard. Up and down move the selected Run. `v` runs Verify. `p` Pause or Resume. Ctrl+Shift+E is Emergency stop. Single-letter keys do nothing while focus is in a text field.

### 10.2 Page frame, top to bottom

1. **Top bar.** Name, status sentence, the three safety buttons.
2. **Tabs row.** `Runs`, `Limits`, `Tools`.
3. **Developer line.** One gray line.
4. **Alert strip.** Hidden unless something needs the person. One at a time.
5. **Page body.** The selected tab.
6. **Footer.** One gray line.

Browser tab title: `Iris · Developer`.

#### Top bar

- Left: `Iris`, then a small label `Developer`.
- Middle, one sentence, kept current: `Watching · {n} Runs running · Cost {amount}`. Variants:
  - `Watching · 1 Run running · Cost {amount}`
  - `Watching · No Runs running · Cost {amount}`
  - `Watching is off`
  - `Emergency stop is on`
  - Tooltip on the cost: `What your agents cost, when Iris can work it out.`
- Right, three buttons in this order: **Verify**, **Pause** (becomes **Resume**), **Emergency stop**.
  - Tooltips: same words as the app.
  - Verify and Pause act on the selected Run. Disabled tooltip: `Pick a Run in the list first.`
- There is no Watching switch in the top bar. It is on the Tools tab.

#### Developer line

`Developer tools. This page does extra things the Iris app does not. It only works on this computer.`

#### Footer

`Iris Developer {version}. This page is only on this computer.`

### 10.3 Runs tab

Two panes side by side. List on the left, about 40% of the width. Detail on the right.

#### Page title row

- Left: `Runs` (18 px).
- Right: a link, **Save a copy of the journal**. It downloads a file named `iris-journal-{date}`. Under it, gray: `The file lists what each Run did and what Iris found. It holds no message text and no keys.`
- Under the title, a filter: `Show:` then three choices: `All`, `Running`, `Problems`. `Problems` means Incorrect or Unverified. Tooltip on `Problems`: `Runs where a claim is Incorrect or Unverified.`

#### Run list

Newest first. Click a row, or use the arrow keys.

| Header | Header tooltip | Cell words |
| --- | --- | --- |
| `Run` | `One job your agent does, from start to end.` | `Run {trace prefix}`. Demo: `Run {trace prefix} · Demo`. Sample: `Run {trace prefix} · Sample`. |
| `Status` | `Where the Run is now.` | `Running`, `Paused`, `Stopped`, `Finished` |
| `Verdict` | Same words as the app | `Correct`, `Incorrect`, `Unverified`, `Not checked yet`. Same roll-up as the app. |
| `Calls` | `How many times the agent reached out during this Run.` | A number |
| `Cost` | Same words as the app | `$0.00`, an amount, or `Unverified` |

Started time, places and length go in the detail, to keep the list narrow.

**Empty state** (no Runs):

> No Runs yet.
>
> Start the demo agent on the Tools tab, or give your agent this address: `http://127.0.0.1:{spot}`  **Copy**
>
> It works with any agent that lets you set the address of its AI service.

#### Run detail

If nothing is selected: `Pick a Run to see what happened.`

**Header.** `Run {trace prefix}` and its Status word. Under it, one line of facts: `Started {time} · Lasted {length} · {n} calls · Cost {amount}`. Then the summary sentence, same words as the app (section 4.4).

Four tabs inside the detail: `Claims`, `Calls`, `Why`, `Cost`.

**Claims.** The same three-column table as the app (`The agent said`, `Verdict`, `What Iris found`). Each row opens to show three more lines in gray:

- `Looked at:` where Iris looked. Example: `the demo notebook`
- `Found:` what it found. Example: `2 notes`
- `Took:` how long the look took. Example: `12 ms`

There is no Replay and no button to send a call again. A second look is Verify.

**Calls.** One row per call, oldest first. This is the metadata Iris keeps and nothing else.

| Column | Header | Tooltip |
| --- | --- | --- |
| 1 | `#` | `The order of the call in this Run.` |
| 2 | `Time` | `When the call started.` |
| 3 | `Kind` | `What sort of request it was, such as GET or POST. Encrypted calls say Tunnel.` |
| 4 | `Place` | `Where the call went.` |
| 5 | `Path` | `Which page or action at that place. Empty for Tunnel calls.` |
| 6 | `Sent` | `How much the agent sent.` |
| 7 | `Received` | `How much came back.` |
| 8 | `Took` | `How long the call took.` |
| 9 | `Outcome` | `How the call ended.` |

`Outcome` words and tooltips:

- `Answered`: `The place answered.`
- `Failed`: `The place answered with an error, or could not be reached.`
- `Blocked`: `Iris stopped the call before it went out.`
- `Cut off`: `You or Iris cut the call while it was in progress.`
- `Unverified`: `The call went out, but no answer came back. Iris can't say if it worked.`

Tunnel rows: the Path cell says `—`. Under the table, one gray line: `Tunnel rows are encrypted calls. Iris passes them through without opening them. It sees where they go and how big they are, nothing more.`

There is no body view, no header view and no payload view.

**Why.**

- Top line: `Run ID: {id}` with **Copy**. Tooltip on `Run ID`: `The number Iris uses inside its journal to find this Run.`
- Then one numbered list per claim, `How Iris decided`, with the same steps as the app.
- Under the calls, a second block, `Why each call is on record`: `Iris writes each call down before it goes out. If Iris stops halfway, the journal still shows the call.`
- If a call ended Unverified: `This call went out and no answer came back. Check the result before trying it again.`

**Cost.**

- Top: `Cost {amount}` and the meaning line (same words as the app, section 4.4).
- Table, one row per call: `Call`, `Cost`, `How Iris got it`.
- `How Iris got it` values:
  - `Worked out from the known price for this place`
  - `Stayed on this computer, so nothing to pay`
  - `Unverified. Iris could not work it out and does not guess.`
- Iris never shows a made-up amount. A cost appears only when a real source gave it.

### 10.4 Limits tab

Same words as the app's Limits dialog (section 7), laid out as a page with three sections: `Spending`, `Blocked places`, `Send a Run somewhere else`.

- All confirmations use the page's own dialog, with the same words and the same default button as the app.
- `Send a Run somewhere else` is always shown here, because this is the Developer tier. It needs a selected Run that is not over.
- Saving shows `Save these limits? Your agents are held to them right away.` Buttons **Cancel** (the default) and **Save limits**.
- Nothing on this tab saves until the person confirms.

### 10.5 Tools tab

All developer-only. Each section has a plain one-line purpose. Order, top to bottom: Demo agent, Test places, Sample Runs, Log detail, Settings in use, Watching.

#### Demo agent

- Words: `Starts the demo agent. It runs on this computer. It costs $0.00.`
- Button: **Run the demo**
- Same demo as the app (section 4.7). It does not start on its own.
- While it runs, the button says `Running…`. When it ends, the button goes back to **Run the demo** and the new Run is selected on the Runs tab.

#### Test places

- Words: `Pretend places your agent can call. They run on this computer. Nothing leaves it.`
- A table: `Name`, `What it does`, `Address`, and an on/off switch.

| Name | What it does |
| --- | --- |
| `Always answers` | `Answers every call right away.` |
| `Slow` | `Waits 5 seconds, then answers.` |
| `Fails` | `Refuses every call.` |
| `Goes quiet after accepting` | `Takes the call, then never answers. Use it to see Unverified.` |

- Each row has its own address, `http://127.0.0.1:{spot}/{name}`, with **Copy**. Address and switch are shown only when the switch is on.
- Calls to a test place show up as normal Runs and cost `$0.00`.

#### Sample Runs

- Words: `Add made-up Runs so you can see how the screens look. They are marked Sample. They are never real.`
- Buttons: **Add sample Runs** and **Remove sample Runs**.
- Add: three Runs appear, one Correct, one Incorrect, one Unverified, each marked `Sample`, each costing `$0.00`. The status line under the button: `Added 3 sample Runs.`
- Remove: `Remove all sample Runs?` Buttons **Keep them** (the default), **Remove them**. Real Runs are never touched.
- Sample Runs are left out of `Cost`, and out of the saved journal unless the box `Include sample Runs` is ticked in the save dialog.

#### Log detail

- Label: `Log detail`. Three choices:
  - `Warnings and errors only` (the default)
  - `Normal`
  - `Everything`
- Under it: `More detail never adds message text or keys.`
- Under that, gray: `The log is a file in the journal folder.` with the folder path and **Copy**.

#### Settings in use

- Words: `Where each setting comes from. This table is read-only.`
- Table: `Setting`, `Value`, `Where it came from`. The last column says `Default`, `Settings file`, or `Environment`. Rows: `The spot Iris listens on`, `Where Iris keeps its journal`, `Daily limit`, `Log detail`, `How long to keep the journal`.
- Under it: `To change a setting, edit the settings file. The path is below.` The path, with **Copy**.
- Button: **Reload settings file**. After it: `Reloaded. A new spot for Iris needs a restart.`

#### Watching

- A switch. Label `Watching`. Words: `Off means agents that use Iris can't reach anything. An agent that doesn't use Iris is not affected, and Iris can't see it.`
- One click, no confirmation.

### 10.6 What each control does (devpanel)

All three safety buttons are plain page buttons that post to the local server. Every post carries a per-launch token. The server refuses a post without it (section 9).

- **Verify.** Same behavior as the app (section 5).
- **Pause and Resume.** Same as the app.
- **Emergency stop.** One click. No confirmation. The result is the same as in the app. The page shows a red strip under the Developer line: `Emergency stop is on. Iris is blocking every call. Your agent may still be running. Close it yourself if you want it fully off.` Strip button: **Turn off Emergency stop…**. Same confirmation words as the app (`Let agents work again?`). The strip stays after a reload, and after Iris restarts.
- **Save a copy of the journal.** A normal file download.
- **Copy buttons.** After a click the button reads `Copied` for 2 seconds.
- **Confirmation dialogs** use the page's own dialog. The default button is always the safer one.
- The panel is read live. Tables update every second without losing the selected Run or the scroll position.

### 10.7 Same content as the app, where it sits here

| In the app | Here |
| --- | --- |
| Menu bar | not used |
| Toolbar buttons | the top bar |
| Notice strip | the alert strip |
| Status bar | the status sentence in the top bar |
| Run table with More detail on | the Run list plus the `Calls` tab |
| Settings and Limits dialogs | `Limits` and `Tools` tabs |
| First-run screen | none. The first load is the Runs tab with its empty state. |
| "Run the demo" on the first-run screen | the Tools tab, and the Runs empty state |

There is no first-run screen here. A developer who opens this page already knows what Iris is.

### 10.8 Errors (devpanel)

- **The spot is taken.** Alert strip, amber. Same words as the app, with **Try another spot** and **Close Iris**. The page cannot close Iris if the server did not start, so in that case the strip is not shown and the program's own window carries the words.
- **The page lost contact with Iris.** Shown after 3 failed reads in a row. Red strip: `This page lost contact with Iris. Verify, Pause, and Emergency stop may not work. Start Iris again.` The buttons stay clickable. A click that fails says `That did not reach Iris.` under the button.
- **Another browser tab has the page open.** No message. Both tabs show the same state.
- **A post without the right token.** `This page is out of date. Reload it.` Nothing changes.

### 10.9 Left out on purpose (devpanel)

- **Raw payload, header and body viewer.** The research table lists it as dev-only. It contradicts the rule that Iris keeps no bodies, keys or headers, and Iris never opens encrypted calls. The `Calls` tab shows exactly what Iris keeps.
- **Replay or resend a call.** There is nothing to replay: Iris keeps no bodies. A second look at a Run is Verify.
- **API keys, remote access, listening beyond this computer, turning off the token check.** Not offered. Not even here. The research lists them as dev-only. The page has no safe use for them, and the rules say loopback only.
- **Editing settings in the page.** The page shows them and reloads the file. It does not edit. This keeps text editing out of the web view and keeps one source of truth.

## 11. Words used

| Instead of | This spec writes |
| --- | --- |
| journal | journal (kept) |
| read back, read-back | `looks for itself`, `looked in…` |
| destination | place |
| port | spot (and inside the address only) |
| loopback | `this computer only` |
| redirect | `send somewhere else` |
| trace id | `Run {trace prefix}` |
| proxy | not used on screen |
| priced cost | `Cost` (the label), dollar amounts show when known |
| fail closed | never used |
| ground truth | never used |

`Run` is kept, with its meaning in the column tooltip. A Run is one job your agent does, from start to end.

## 12. Tests the implementer writes first

Charles reviews the tests before the implementation. New behavior only; refactors skip this.

1. **First run.** The first-run screen shows the exact words in section 2. Clicking **Run the demo** opens the dashboard and starts the demo Run. Clicking **Skip the demo and open the dashboard** opens the dashboard with the empty state.
2. **Demo.** The demo Run has three claims: Correct, Unverified, Incorrect. The Run stops at the Incorrect claim. The demo agent really saves notes and Iris really reads them back.
3. **Verdict words.** Every verdict on screen reads Correct, Incorrect, or Unverified. The word "Unknown" does not appear on screen anywhere.
4. **Run label.** Every Run on screen reads `Run ` plus the first 8 characters of its trace id. No sequential number appears.
5. **Verify gate.** Clicking Verify opens the dialog with `What was checked, in plain words:`. Typing words and clicking **Verify** stamps the gate. A paused Run can go again after Verify.
6. **Emergency stop.** One click blocks every call. Every running or paused Run becomes `Stopped`. The red strip shows. Turning it off needs the confirmation dialog. It stays on after a restart.
7. **Loopback security.** A POST without the per-launch token is refused. A request with a Host header other than `127.0.0.1` or `localhost` is refused. The token is fresh each launch and is never written to disk or logged.
8. **Spending limit.** With a daily limit set, a call Iris cannot price is blocked, unless its destination is on the allowed list. With no limit set, the same call goes through.
9. **Allowing a place is one click.** When a call is blocked for an Unverified cost, the notice strip shows **Allow {place}**. One click adds the destination to the allowed list, with no typing and no confirmation dialog. The strip then offers **Undo**. The Limits dialog shows recently blocked destinations with an **Allow** button each.
10. **Journal words.** Every place the design said "record", the screen says "journal". The save file is named `iris-journal-{date}`.
11. **Devpanel.** The devpanel shows the same strings as the app where the same thing appears. The Calls tab shows metadata only, no body, no header, no payload. There is no Replay button. There is no raw viewer.
12. **Tiers.** The consumer build has no Developer pages or routes. The `?tier=developer` URL returns nothing in the consumer build.
13. **Teardown.** Closing the window stops the controller, removes the view container from the layout and deletes it synchronously, then deletes the view. No watcher is left running behind Iris.
14. **More detail tabs.** With More detail off, the Run detail shows the claim list only. Flipping More detail on adds the three tabs beside Claims: Calls, Why, Cost. The Calls tab shows the same columns and words as the devpanel Calls tab, metadata only. The Why tab shows one numbered list per claim, `How Iris decided`. The Cost tab shows the same words as the devpanel Cost tab. The Iris menu gains `Developer page` only with More detail on.

## 13. What stays Unknown

- Whether the demo agent (the one that really saves notes) exists yet or must be built as part of this work. The spec assumes it is built here.
- Whether the `?tier=` mechanism in the panel server already hides Developer routes in the consumer build, or whether that removal must be built here. The panel code has tier logic ("Simple hides Redirect"), but full Developer removal is not confirmed.
- The exact loopback spot number. Shown as `{spot}` throughout. The implementer picks the number, and the spec is updated with it.
- The connection mechanism (paste one address into the agent's AI-service setting) has not been tested with a non-developer. That test is outside this spec.
