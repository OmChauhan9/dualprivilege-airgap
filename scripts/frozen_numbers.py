"""Recompute the headline numbers the README cites from the frozen files in results/published/.

    uv run python scripts/frozen_numbers.py

Per run, per case (any of 3 trials) and paired by case, so a reader can check every claim.
`uv run airgap reproduce` checks these and every other published number against the docs.
"""

from airgap.eval.reproduce import frozen_report

if __name__ == "__main__":
    print(frozen_report())
