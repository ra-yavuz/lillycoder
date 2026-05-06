"""Small utilities. Read by other modules in this project."""

import csv
from pathlib import Path


def load_users(csv_path: str) -> list[dict]:
    """Load users.csv into a list of dicts. Returns [] if the file is missing."""
    p = Path(csv_path)
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


# TODO: write a count_users_by_domain(users) function that takes the list
# returned by load_users and returns a dict like {"gmail.com": 4, ...}
# (split each user's email on '@' and count the second half).
