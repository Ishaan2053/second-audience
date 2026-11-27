# Demo script

The shot list for the submission video: the seven-part flow from `docs/01-master-plan.md`
section 7, expanded into shots with the exact command, what to say, a target length and a
fallback. Target total: about 3 minutes 15 seconds. Commands are for Windows PowerShell from
the repository root, with `.env` holding the production settings.

## Before recording

1. Terminal: a dark theme, a monospace font sized so that **140 columns fill the frame
   width** at the video's resolution (1920x1080). Every replay command below passes
   `--width 140`, so the layout is the same whatever the window size.
2. Re-run the pair finder and note the counts you will quote. Numbers in this script are a
   snapshot (2026-11-27, about 48 exposed sessions); the experiment is still running, so the
   counts on the day of recording replace them.

   ```powershell
   uv run python scripts/find_pairs.py --width 200
   ```

3. Open in a browser: the Flagsmith results page for experiment `error-style-v1`, and
   `https://second-audience.onrender.com/healthz` (loading it also wakes the free host).
4. Run `uv run sa report error-style-v1` once, so the newest results JSON is saved in
   `reports/error-style-v1/` before the recording starts.

## The two replays

Both replays are real recorded lab sessions from the ledger, read-only. The video says so,
and so does the replay itself (banner at the top, line at the bottom). The pairs were chosen
with `scripts/find_pairs.py`; the counts below are what the narration may claim.

| Replay | Scenario | Model | Control session | Prescriptive session | Why this pair |
|---|---|---|---|---|---|
| Trap | `s05` (trap) | gemini-3.1-flash-lite | `45e0cf4504cb4ae08fcccff1fd54f173` (correct, 5 calls) | `12ca743d6fe942e289986d2ee77b6e04` (correct, 4 calls) | The suggestion (12:00-13:00) breaks the brief's "finish by 12:00"; the agent ignores it and books 10:00-11:00 |
| Aligned | `s08` (aligned) | gemini-3.1-flash-lite | `6fb4e412a6224a85b3b7438c61a5ed7b` (correct, 4 calls) | `3d904655e0ec40fd86907ba80cb54fca` (correct, 2 calls) | The corrected call is right; the agent books with it directly and skips two look-ups |

What the snapshot supports (quote the recording-day numbers instead):

- **Trap:** every exposed trap session ended correct in both arms (control 9 of 9,
  prescriptive 13 of 13). No agent took a suggestion that violated its brief. Mean tool calls
  4.67 (control) versus 4.46 (prescriptive). Of the 33 trap pairs, 9 had the prescriptive
  session use fewer calls, 18 equal and 6 more; the replay shows a "fewer" pair, so say
  "here the agent rejected the bad suggestion", not "the suggestion saves calls on traps".
- **Aligned:** control 14 of 14 correct, mean 4.29 calls; prescriptive 10 of 12 correct, mean
  3.25 calls. The two prescriptive failures are both `no_booking` on `s08` with
  gemini-3.5-flash-lite. In the `s08` gemini-3.1-flash-lite cell, 2 of 4 prescriptive
  sessions used 2 calls and 2 used 4; all 3 control sessions used 4. Say "in some sessions",
  not "always", and mention that prescriptive errors also produced the only two aligned
  failures.

## Shots

| # | Part | Seconds | Shot |
|---|---|---|---|
| 1 | Problem | 20 | Title card, then the public endpoint's landing page |
| 2a | Side by side: trap | 25 | Trap replay |
| 2b | Side by side: aligned | 20 | Aligned replay |
| 3 | Operator | 45 | `sa propose`, then `sa start --dry-run` |
| 4 | Results | 45 | Flagsmith results page, then `sa report` |
| 5 | Field | 20 | An MCP client on the public URL |
| 6 | Ship | 15 | `sa ship --dry-run` |
| 7 | Close | 10 | Closing card |

### 1. Problem (20 s)

- **Show:** a title card "Second Audience", then the landing page at
  `https://second-audience.onrender.com/`, which explains the experiment to visiting agents'
  owners.
- **Say:** "You A/B test the interface humans use. The one agents use, your MCP server's
  tools and error messages, is worded by taste and never measured. Second Audience measures
  it, with agents as the subjects and Flagsmith Experimentation as the engine."
- **Fallback:** if the host is asleep, show `src/second_audience/booking/server.py` in the
  editor; the title card carries the sentence on its own.

### 2a. Side by side, trap scenario (25 s)

- **Run:**

  ```powershell
  uv run python scripts/replay.py --sessions 45e0cf4504cb4ae08fcccff1fd54f173 12ca743d6fe942e289986d2ee77b6e04 --width 140 --step
  ```

  Press Enter once per step; pause on the step with the red "first styled error" panels.
- **Say:** "This is a replay of two recorded sessions, same task, same model. Left gets the
  informative error: the room is taken. Right also gets a suggestion and a ready-made
  corrected call, but this is a trap: the suggested slot ends after the deadline in the
  brief. This agent checks availability and books a slot that fits. In this snapshot, no
  agent in either arm took a bad suggestion: <N> of <N> prescriptive trap sessions ended
  correct."
- **Fallback:** if the ledger cannot be reached, replay from a local copy of the two
  sessions: set `SA_DATABASE_URL` to an SQLite file holding them, or show a screenshot of
  this replay taken while preparing.

### 2b. Side by side, aligned scenario (20 s)

- **Run:**

  ```powershell
  uv run python scripts/replay.py --sessions 6fb4e412a6224a85b3b7438c61a5ed7b 3d904655e0ec40fd86907ba80cb54fca --width 140 --delay 1.5
  ```

- **Say:** "Where the suggestion is right, the prescriptive agent uses the corrected call
  directly: two tool calls instead of four. That is the trade-off the experiment measures:
  fewer calls when the suggestion fits, and a guardrail on wrong bookings for when it does
  not. It does not always go this way: <quote the counts from find_pairs>."
- **Fallback:** as in 2a.

### 3. Operator (45 s)

- **Run:**

  ```powershell
  uv run sa propose --from-slug error-style-v1
  uv run sa start experiments/error-style-v1.yaml --dry-run
  ```

- **Say:** "The operator agent reads failing transcripts from the ledger and drafts the next
  experiment as a spec file, which a human edits and commits. `sa start` shows the plan, the
  flag, variants, metrics and stopping rule, and changes nothing in Flagsmith until a human
  types the confirmation. This experiment was started that way on the 26th."
- **Fallback:** if Gemini is out of quota, `sa propose` falls back to its template; if it
  fails outright, show a saved draft from `experiments/proposals/` and the `v0.1.0` release
  notes for the real start. The dry run reads Flagsmith but writes nothing; for the running
  experiment it reports that it is already running.

### 4. Results (45 s)

- **Show:** the Flagsmith results page for `error-style-v1`, then run:

  ```powershell
  uv run sa report error-style-v1
  ```

  and scroll `reports/error-style-v1/report.md`: overall result, aligned versus trap, the
  per-model table, reconciliation.
- **Say:** "Flagsmith computes the result: tool calls per session, with task completion and
  wrong bookings as guardrails. `sa report` refreshes it, saves the raw JSON, and writes the
  report. Every number comes from Flagsmith or the ledger; the model only writes the prose
  around them, and the report says which. Lab and field sessions are labelled separately."
- **Fallback:** if Flagsmith is slow to refresh, `sa report` reuses the newest saved results
  JSON in `reports/error-style-v1/`; show `report.md` from the preparation run.

### 5. Field (20 s)

- **Show:** an MCP client (for example Claude Code) connected to
  `https://second-audience.onrender.com/mcp`, running one task to `finish_task`.
- **Say:** "The same server is public. Anyone can point their own agent at it; those
  sessions are tagged as field traffic and reported apart from the lab."
- **Fallback:** if the host is down, run the server locally
  (`uv run python -m second_audience.booking.server --transport http --port 8000`), state the
  public URL on screen, and show field rows from the ledger
  (`uv run python scripts/find_pairs.py` lists the field cells).

### 6. Ship (15 s)

- **Run:**

  ```powershell
  uv run sa ship error-style-v1 --dry-run
  ```

- **Say:** "Shipping is one command with typed approval: it ends the experiment and rolls the
  winner out on the flag, or records the decision not to ship and why. Here is the decision
  the data supports today."
- **Fallback:** if the stopping rule is not met at recording time, the dry run refuses and
  says why; show that refusal and say the decision is pending.

### 7. Close (10 s)

- **Show:** a closing card with the repository URL.
- **Say:** "If you own an MCP server, pick one wording question, put it behind a Flagsmith
  flag, and let agents answer it."
- **Fallback:** none needed; the card is pre-made.

## Test clip

Before the full recording, record a 30-second clip of shot 2a at 1920x1080 and check that the
tool arguments, the red error panels and the yellow suggestion block are readable at 100%
and on a phone screen. If not, raise the font size and lower `--width` (for example 120).
