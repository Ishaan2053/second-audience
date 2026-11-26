# SA-15: Demo kit: side-by-side transcript replay

Read `CLAUDE.md` and `docs/02-architecture.md` before starting. This ticket is self-contained;
where it conflicts with an older plan document, the ticket wins and the document is updated.

| Field | Value |
|---|---|
| Priority | P1 |
| Type | feature |
| Estimate | 1.5h |
| Depends on | SA-08 (recorded sessions in the ledger) |
| Components | `scripts/replay.py`, `docs/demo-script.md` |
| Branch | `feat/SA-15-demo-kit` |
| Can run in parallel with | SA-13, SA-14 |

## 1. Objective

Build a terminal replay that shows two real recorded sessions on the same scenario side by side, one per arm, so the demo video can show the behavioural difference reliably without a live model call.

## 2. Context

The most persuasive moment in the video is watching two agents receive different errors and do different things. Live runs are slow, quota-bound and non-deterministic. Replaying real ledger transcripts is honest (it is real recorded behaviour) and repeatable. The video must say that it is a replay of recorded sessions.

## 3. Scope

**In scope**

- `scripts/replay.py --scenario <id> [--model <name>]` or `--sessions <id_a> <id_b>`.
- `scripts/find_pairs.py` to list candidate pairs.
- `docs/demo-script.md`: the shot list and narration.

**Out of scope**

- Any web UI.
- Editing or re-ordering transcripts.
- Video editing.

## 4. Implementation instructions

- Use `rich` for a two-column layout: left control, right prescriptive. Header shows scenario ID, kind, model and session IDs. Then the brief once, full width.
- Step through tool calls in sequence order with a short configurable delay; `--step` waits for a key press between steps. Show the tool name, the key arguments, and the response. Highlight the first styled error in each column and, on the right, the `suggestion` block.
- Footer: each session's outcome (`correct`, `incorrect`, and so on) and tool-call count.
- A banner line reads "Replay of recorded sessions" with the recording dates.
- `find_pairs.py` lists, per scenario and model, pairs of exposed sessions with different arms, and flags the pairs where outcomes differ. It must not hide pairs that contradict the headline; print counts of each pattern so the chosen example can be described honestly (for example "this happened in 14 of 22 trap sessions").
- Read-only access to the ledger. Works against SQLite or Postgres.
- `docs/demo-script.md`: the seven-part flow from `docs/01-master-plan.md` section 7 expanded into shots, with the exact commands to run, what to say, target seconds per shot, and the fallback for each shot.
- Choose two replays for the video: one trap scenario and one aligned scenario, so both sides of the trade-off are shown.

## 5. Dependencies

- Tickets: SA-08 (recorded sessions in the ledger)
- Check `docs/tickets/STATUS.md` to confirm they are merged before starting.

## 6. Acceptance criteria

- The replay renders two real sessions side by side from the ledger and ends with outcomes.
- `--step` mode works for controlled recording.
- `find_pairs.py` prints pattern counts, not only favourable pairs.
- The replay output states that it is a replay.
- `docs/demo-script.md` covers every shot with a fallback.

## 7. Testing requirements

- Unit test rendering against a seeded SQLite ledger (assert on captured console text).
- Manual: record a 30-second test clip and check legibility at the video's resolution.

## 8. Definition of done

- All acceptance criteria met and demonstrated, with real command output in the pull request.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` and `uv run pytest` pass locally; CI is green.
- New settings are in `Settings`, `.env.example` and the environment reference.
- Documentation touched by this change is updated to match the code.
- No secrets, dead code or unexplained workarounds.
- `docs/tickets/STATUS.md` updated and the handoff written in the format in `CLAUDE.md`.

## 9. GitHub workflow

1. Issue: `SA-15: Demo kit: side-by-side transcript replay`. Create it with `gh issue create` if it does not exist, using this file as the body, with labels `P1` and the type label.
2. Branch from an up-to-date `main`: `feat/SA-15-demo-kit`.
3. Commit in small, meaningful steps. Open a pull request titled `SA-15: Demo kit: side-by-side transcript replay` with `Closes #<issue>` and the template filled in.
4. Wait for CI. Squash-merge when green. Delete the branch.
5. Lean workflow: `/code-review` is not required for this ticket unless stated above; it is mandatory in SA-17.

## 10. Next ticket

SA-16.
