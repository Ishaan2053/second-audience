"""List replay candidates per scenario and model, with counts of every outcome pattern.

uv run python scripts/find_pairs.py
uv run python scripts/find_pairs.py --scenario s11
"""

from second_audience.demo.pairs import main

if __name__ == "__main__":
    raise SystemExit(main())
