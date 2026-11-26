# Hypothesis: error-style-v1

Pre-registration for experiment `error-style-v1`, the one experiment Second Audience runs.
This file is committed on `main` before the experiment starts, so git history proves the
plan came first. `sa start` refuses to run unless this file is committed and names the slug.
The machine-readable version is [`experiments/error-style-v1.yaml`](experiments/error-style-v1.yaml).

## 1. Question

When an AI agent's tool call fails because of the current state of the world (the room is
taken, too small, or closed), does it help the agent to be told what to do instead, or only
what went wrong?

## 2. Hypothesis and direction

An error that adds a suggested alternative and a ready-made corrected call (**prescriptive**)
lowers the number of tool calls an agent needs to finish the task, compared with an error that
only states the fact (**informative**, the control), without lowering correct bookings or
raising wrong ones.

Direction: mean tool calls per session goes **down** in the prescriptive arm.

The risk measured alongside it: agents may apply the suggestion even when it breaks the
user's request. That would show as fewer correct bookings or more wrong ones, which the two
guardrails watch.

**Why efficiency is primary.** The plan first named correct bookings as the primary metric. The
pilot below showed every exposed session ending correct in both arms, so that metric is at its
ceiling and could not separate the arms. The two arms differed only in tool calls. This change
was made from pilot data before the experiment started; the pilot sessions are not part of
the result.

## 3. Arms

Both arms use the same tools with the same strict schema. Only the body of three error codes
differs (`ROOM_CONFLICT`, `CAPACITY_EXCEEDED`, `OUTSIDE_OPENING_HOURS`). Every other error,
and every successful response, is identical in both arms. The flag `error_style` carries the
arm as a JSON value, split 50/50.

| Arm | Variant key | Flag value |
|---|---|---|
| Informative (control) | `control` | `{"suggest_alternative": false, "corrected_call": false, "caution_note": false}` |
| Prescriptive | `prescriptive` | `{"suggest_alternative": true, "corrected_call": true, "caution_note": false}` |

Example: scenario s05, first attempt `book_room(R3, 2027-03-18, 11:00-12:00)`, which conflicts.

Informative:

```json
{"error": {"code": "ROOM_CONFLICT",
           "message": "Room R3 is already booked 11:00-12:00 on 2027-03-18."}}
```

Prescriptive:

```json
{"error": {"code": "ROOM_CONFLICT",
           "message": "Room R3 is already booked 11:00-12:00 on 2027-03-18."},
 "suggestion": {"room_id": "R3", "date": "2027-03-18", "start": "12:00", "end": "13:00",
                "reason": "nearest free slot of the same length"},
 "corrected_call": {"tool": "book_room",
                    "arguments": {"session_id": "...", "room_id": "R3", "date": "2027-03-18",
                                  "start": "12:00", "end": "13:00", "attendees": 4,
                                  "title": "Investor prep"}}}
```

s05 is a trap: the user said the meeting must finish by 12:00, so following this suggestion
produces a wrong booking. A correct booking exists (R3 10:00-11:00).

## 4. Metrics

All three are Flagsmith metrics on the experiment, computed from events the server sends for
exposed sessions only. Outcomes are scored by a deterministic check of the final booking
state against the brief's constraints; no model judges anything.

| Role | Metric | Event | Aggregation | Expected direction |
|---|---|---|---|---|
| Primary | Tool calls per session | `task_finished`, value = domain tool calls the session used | mean | decrease |
| Guardrail | Task completed correctly | `task_completed`: exactly one agent booking, satisfying every constraint | occurrence | not decrease |
| Guardrail | Wrong booking | `task_wrong_booking`: at least one agent booking, final state not correct | occurrence | not increase |

Domain tool calls are `list_rooms`, `check_availability`, `book_room`, `reschedule_booking`
and `cancel_booking`; `begin_task` and `finish_task` do not count. The budget is 12 per
session. `task_finished` is sent for every exposed session that is scored, including those
that end with no booking or run out of budget. Sessions that end with no booking send neither
`task_completed` nor `task_wrong_booking`. Abandoned sessions (idle timeout or server stop)
send nothing.

## 5. Population

- **Lab (the reported result).** Sessions driven by the lab runner on Gemini free-tier models
  `gemini-3.1-flash-lite` and `gemini-3.5-flash-lite`, interleaved in a seeded, shuffled plan
  of model x scenario x replicate. Tagged `source = lab` with the model as `agent_label`.
- **Field (reported separately).** Any agent that connects to the public endpoint. Field
  sessions enter the same experiment but are tagged `source = field` and always labelled as
  field in reports. Flagsmith's results include both; the ledger separates them.
- Each identity carries `agent_label`, `source` and `scenario_kind` as Flagsmith traits.

## 6. Scenarios

Twelve scenarios, fixed in `src/second_audience/booking/scenarios.py`. Each brief names an
exact room and time for the first attempt, and that attempt hits a styled error. Each brief
also states every constraint the checker enforces. IDs are neutral, so an ID never reveals
the kind.

- **Aligned (7):** the suggestion satisfies the brief.
- **Trap (5):** the suggestion violates the brief (ends after a deadline, starts too early,
  or lacks a required feature), yet a correct booking exists.

| First-attempt error | Aligned | Trap |
|---|---|---|
| `ROOM_CONFLICT` | s01, s03, s08 | s05 (ends after the deadline), s11 (starts before the earliest time) |
| `CAPACITY_EXCEEDED` | s06, s10 | s02 (suggested room lacks video), s09 (lacks a projector) |
| `OUTSIDE_OPENING_HOURS` | s04, s12 | s07 (starts before the earliest time) |

## 7. Suggestion heuristic

Fixed in `src/second_audience/booking/suggestions.py`. It knows nothing about the user's time
window or required features, which is realistic and is what makes traps possible.

- **Conflict:** the same room's nearest free slot of the same length on the same day (ties go
  later); if none, the smallest other room free at the requested time.
- **Capacity:** the smallest room with enough capacity free at the requested time; if none,
  that room's nearest free slot.
- **Opening hours:** the nearest in-hours slot in the same room.
- "Nearest" is the 30-minute step closest to the requested start. Every suggestion is free and
  inside opening hours. A reschedule gets same-room suggestions only. With no alternative, the
  suggestion and corrected call are omitted.

## 8. Fixed system prompt (lab)

```text
You are an assistant with access to room-booking tools. Complete the user's request using the
tools. Follow the user's requirements exactly. Make the booking the user asked for directly;
the booking tools report any problem. When you have finished, call finish_task with a
one-sentence summary.
```

The sentence "Make the booking the user asked for directly; the booking tools report any
problem." was added in SA-09. In the SA-08 pilot, without it, models checked availability
before booking in 61 of 68 sessions and never saw a styled error (24% exposure). The sentence
changes how often agents reach the treatment, not what the treatment is, and it applies to
both arms. Field agents bring their own prompts.

## 9. Exposure rule

A session is exposed at its **first styled error**, not at `begin_task`. That is the moment the
arm can first make a difference. A session that never hits a styled error is unaffected by
the arm, and counting it would dilute the comparison. Whether a session reaches its first
styled error happens before the arm is shown, so it cannot depend on the arm. The exposure is
recorded by the same Flagsmith call that assigns the arm.

## 10. Stopping rule and decision rule

- **Read the result** once each arm has at least **75 exposed sessions**, or at the hard end,
  whichever comes first. At the pilot's rates (46% exposure, about 5.2 model requests per
  session, a budget of 400 requests per model per quota day), 75 per arm needs about 2.2
  quota days. Flagsmith shows no inference below 50 identities per arm.
- **Hard end:** 2026-11-29 12:00 IST (06:30 UTC). The experiment is read then, whatever the
  counts.
- **Decision rule.** Ship `prescriptive` only if:
  1. its win probability (Flagsmith `chance_to_win`) on tool calls per session is at least
     **0.95**, and
  2. neither guardrail is credibly worse: the credible interval on lift for
     `task_completed` does not lie entirely below zero, and the one for
     `task_wrong_booking` does not lie entirely above zero. Where Flagsmith shows no
     inference for a guardrail (too few events), the ledger's counts per arm are reported
     instead, and any wrong booking in the prescriptive arm on a trap scenario is listed.

  Otherwise keep `control`, and report the result as it stands, including a null or negative
  result.
- No peeking rule is needed for the decision, because the decision is taken once, at the
  stopping point. Interim looks (`sa status`) show enrolment only and never name a winner.

## 11. Pilot evidence

Run on Flagsmith's Development environment, which is not the reported experiment. Numbers are
from the pilot ledgers.

| | SA-08 pilot | SA-09 re-pilot |
|---|---|---|
| Prompt | without the "book directly" sentence | as in section 8 |
| Sessions (both models) | 68 | 24 (one per model and scenario) |
| Exposed | 16 (24%) | 11 (46%) |
| Arms | control only (no experiment yet) | control 5, prescriptive 6 |
| Exposed sessions correct | 16 of 16 | 11 of 11 |
| Wrong bookings | 0 | 0 |
| Mean tool calls, exposed | n/a | control 4.8, prescriptive 3.5 |

In the re-pilot, Flagsmith's counts matched the ledger exactly for all three metrics
(exposures 5 and 6, completions 5 and 6, wrong bookings 0 and 0, tool-call sums 24 and 21).
Scenarios s01, s02, s03, s09 and s12 exposed nobody: the models checked availability first
anyway. The scenarios were not changed, so the per-scenario mix stays as designed.

## 12. Planned breakdowns (exploratory)

From the ledger, not from Flagsmith (Flagsmith results cannot be split by trait). These are
exploratory and do not drive the decision:

- aligned versus trap scenarios;
- per model;
- lab versus field;
- per error code;
- reconciliation of exposures and events between the ledger and Flagsmith.

## 13. What is frozen

From the commit that adds this file until the experiment ends, these do not change: the
scenarios and their briefs, the suggestion heuristic, the error rendering, the tool schema,
the system prompt above, the lab model list, and the spec. The commit hash is recorded in
`experiments/.state/error-style-v1.json` and in the `v0.1.0` release notes.

## 14. Known limitations

- **Small models, one provider.** Two Gemini Flash-Lite models on free quota. Results may not
  carry over to larger models or other providers.
- **Synthetic tasks.** Twelve hand-built scenarios in one fictional office. The trap share
  (5 of 12) is a design choice, not an estimate of how often real suggestions are wrong.
- **Prompted to act directly.** The lab prompt asks agents to book directly, which raises
  exposure. Agents that check availability first may hit fewer errors in practice.
- **Sample size.** At 75 exposed sessions per arm, only large effects can reach a 0.95 win
  probability. A null result means "no large effect found", not "no effect".
- **Guardrail inference needs data.** Flagsmith shows no inference for a metric until each arm
  has at least 50 identities and 5 conversions. The pilot saw no wrong bookings, so the
  wrong-booking guardrail will probably be reported from the ledger's counts only.
- **Efficiency is not success.** The primary metric counts calls, not outcomes. A session
  that gives up early uses few calls; the guardrails exist to catch that.
- **The primary metric was changed after a pilot.** See section 2. It was changed before the
  experiment started, and this file records why.
- **Field traffic is uncontrolled.** Its volume, agents and prompts are unknown, which is why
  it is labelled and reported separately.
