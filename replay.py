from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any


def parse_date(value: str | None, *, fallback_year: int | None = None) -> dt.date:
    if value:
        try:
            return dt.date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    if fallback_year:
        return dt.date(fallback_year, 10, 1)
    return dt.date.today()


def pregame_cutoff(game_date: str | None, *, fallback_year: int | None = None) -> str:
    """Return the last date whose game results are allowed in a historical replay.

    A retrospective run for 2026-04-27 must not see anything that occurred on
    2026-04-27.  All form/stat windows therefore end on 2026-04-26.
    """
    return (parse_date(game_date, fallback_year=fallback_year) - dt.timedelta(days=1)).isoformat()


def season_start(season: int) -> str:
    # March 1 safely precedes the regular season while excluding prior-year data.
    return dt.date(int(season), 3, 1).isoformat()


def stable_signature(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
