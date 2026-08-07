from __future__ import annotations

from functools import lru_cache

from services.http import get_json
from services.replay import season_start

TEAM_STATS_URL = "https://statsapi.mlb.com/api/v1/teams/{team_id}/stats"


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stat_block(data: dict) -> dict:
    for block in data.get("stats") or []:
        for split in (block or {}).get("splits") or []:
            stat = (split or {}).get("stat") or {}
            if stat:
                return stat
    return {}


def _pitching_card(stat: dict, source: str, *, cutoff: str | None = None) -> dict:
    ip = _number(stat.get("inningsPitched"))
    hr = _number(stat.get("homeRuns"))
    walks = _number(stat.get("baseOnBalls"))
    hits = _number(stat.get("hits"))
    return {
        "hr9": (hr * 9.0 / ip) if hr is not None and ip else None,
        "era": _number(stat.get("era")),
        "whip": ((walks or 0.0) + (hits or 0.0)) / ip if ip else _number(stat.get("whip")),
        "innings": ip,
        "source": source,
        "stats_cutoff": cutoff,
    }


@lru_cache(maxsize=128)
def _relief_pitching(team_id: int, season: int) -> dict:
    relief = get_json(
        TEAM_STATS_URL.format(team_id=team_id),
        params={
            "stats": "statSplits",
            "group": "pitching",
            "season": season,
            "sitCodes": "rp",
        },
    )
    relief_stat = _stat_block(relief)
    relief_card = _pitching_card(relief_stat, "MLB_RELIEF_SPLIT") if relief_stat else {}
    if relief_card.get("innings"):
        return relief_card

    fallback = get_json(
        TEAM_STATS_URL.format(team_id=team_id),
        params={"stats": "season", "group": "pitching", "season": season},
    )
    fallback_stat = _stat_block(fallback)
    return _pitching_card(fallback_stat, "TEAM_PITCHING_PROXY") if fallback_stat else {
        "hr9": None, "era": None, "whip": None, "innings": None,
        "source": "UNAVAILABLE", "stats_cutoff": None,
    }


@lru_cache(maxsize=1024)
def _pitching_as_of(team_id: int, season: int, end_date: str) -> dict:
    """Historical-safe team pitching proxy through end_date.

    MLB's team stats endpoint does not reliably combine the relief-only split
    with a historical by-date range.  In replay mode we therefore prefer a
    clearly labeled team-pitching date-range proxy over a relief split that
    contains future games.  The Blender audit can see that provenance.
    """
    data = get_json(
        TEAM_STATS_URL.format(team_id=team_id),
        params={
            "stats": "byDateRange",
            "group": "pitching",
            "startDate": season_start(season),
            "endDate": end_date,
        },
    )
    stat = _stat_block(data)
    if not stat:
        return {
            "hr9": None, "era": None, "whip": None, "innings": None,
            "source": "REPLAY_TEAM_PITCHING_UNAVAILABLE", "stats_cutoff": end_date,
        }
    return _pitching_card(stat, "MLB_TEAM_PITCHING_BY_DATE_RANGE", cutoff=end_date)


def build_bullpen_card(
    team_data: dict | None,
    *,
    team_id: int | None = None,
    season: int | None = None,
    team_name: str | None = None,
    as_of: str | None = None,
    replay_mode: bool = False,
) -> dict:
    team_data = dict(team_data or {})
    if team_id and season:
        if replay_mode:
            fetched = _pitching_as_of(int(team_id), int(season), str(as_of)) if as_of else {}
        else:
            fetched = _relief_pitching(int(team_id), int(season))
        for key, value in fetched.items():
            if team_data.get(key) is None and value is not None:
                team_data[key] = value
        team_data.setdefault("source", fetched.get("source"))
        team_data.setdefault("stats_cutoff", fetched.get("stats_cutoff"))

    hr9 = _number(team_data.get("hr9"))
    era = _number(team_data.get("era"))
    whip = _number(team_data.get("whip"))
    fatigue = _number(team_data.get("fatigue"))

    components: list[tuple[float, float]] = []
    if hr9 is not None:
        components.append((max(0.0, min(10.0, hr9 / 1.35 * 6.0)), 0.50))
    if era is not None:
        components.append((max(0.0, min(10.0, era / 4.50 * 6.0)), 0.25))
    if whip is not None:
        components.append((max(0.0, min(10.0, whip / 1.30 * 5.5)), 0.15))
    if fatigue is not None:
        components.append((max(0.0, min(10.0, fatigue)), 0.10))

    weight = sum(component_weight for _, component_weight in components)
    risk_score = (
        round(sum(value * component_weight for value, component_weight in components) / weight, 3)
        if weight else None
    )
    source = team_data.get("source") or ("SUPPLIED" if team_data else "UNAVAILABLE")
    return {
        "team": team_data.get("team") or team_name,
        "team_id": team_id,
        "hr9": hr9,
        "era": era,
        "whip": whip,
        "fatigue": fatigue,
        "recent_usage": team_data.get("recent_usage"),
        "risk_score": risk_score,
        "loaded": risk_score is not None,
        "verified_relief_split": source in {"MLB_RELIEF_SPLIT", "SUPPLIED"},
        "source": source,
        "stats_cutoff": team_data.get("stats_cutoff"),
    }
