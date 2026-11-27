"""Replay two recorded sessions side by side from the ledger (read-only, no model call).

uv run python scripts/replay.py --scenario s11 --model gemini-3.1-flash-lite --step
uv run python scripts/replay.py --sessions <control_id> <challenger_id>
"""

from second_audience.demo.replay import main

if __name__ == "__main__":
    raise SystemExit(main())
