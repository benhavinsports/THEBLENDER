from __future__ import annotations

from functools import lru_cache
from services.http import get_json
from services.replay import season_start

PEOPLE_URL = "https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
PERSON_URL = "https://statsapi.mlb.com/api/v1/people/{player_id}"


def _f(value):
    try: return float(value)
    except (TypeError, ValueError): return None


@lru_cache(maxsize=256)
def _pitcher_hand(player_id: int) -> str | None:
    data = get_json(PERSON_URL.format(player_id=player_id))
    people = data.get("people") or []
    if not people:
        return None
    hand = (people[0].get("pitchHand") or {}).get("code")
    return str(hand).upper() if hand else None


def _parse_pitching(data: dict, *, source: str, cutoff: str | None = None) -> dict:
    blocks = data.get("stats") or []
    splits = (blocks[0].get("splits") or []) if blocks else []
    stat = (splits[0].get("stat") or {}) if splits else {}
    ip = _f(stat.get("inningsPitched"))
    hr = _f(stat.get("homeRuns"))
    so = _f(stat.get("strikeOuts"))
    bf = _f(stat.get("battersFaced"))
    return {
        "hr9": (hr * 9 / ip) if hr is not None and ip else None,
        "k_rate": (so / bf * 100) if so is not None and bf else None,
        "era": _f(stat.get("era")),
        "stats_source": source,
        "stats_cutoff": cutoff,
    }


@lru_cache(maxsize=256)
def _season_pitching(player_id: int, season: int) -> dict:
    data = get_json(PEOPLE_URL.format(player_id=player_id), params={"stats":"season", "group":"pitching", "season":season})
    return _parse_pitching(data, source="MLB_SEASON")


@lru_cache(maxsize=2048)
def _pitching_as_of(player_id: int, season: int, end_date: str) -> dict:
    data = get_json(
        PEOPLE_URL.format(player_id=player_id),
        params={
            "stats": "byDateRange",
            "group": "pitching",
            "startDate": season_start(season),
            "endDate": end_date,
        },
    )
    return _parse_pitching(data, source="MLB_BY_DATE_RANGE", cutoff=end_date)


def build_pitcher_card(pitcher, season: int, *, as_of: str | None = None, replay_mode: bool = False) -> dict:
    if isinstance(pitcher, str):
        pitcher = {"name": pitcher}
    pitcher = pitcher or {}
    card = {
        "id": pitcher.get("id"), "name": pitcher.get("name") or "UNKNOWN",
        "side": pitcher.get("side"), "team": pitcher.get("team"),
        "hr9": None, "barrel_allowed": None, "xwoba_allowed": None,
        "hard_hit_allowed": None, "k_rate": None,
        "pitch_predictability": None, "platoon_weakness": "NEUTRAL",
        "throws": None, "stats_source": None, "stats_cutoff": None,
    }
    if card["id"]:
        if replay_mode:
            fetched = _pitching_as_of(int(card["id"]), season, str(as_of)) if as_of else {}
        else:
            fetched = _season_pitching(int(card["id"]), season)
        card.update({k:v for k,v in fetched.items() if v is not None})
        card["throws"] = _pitcher_hand(int(card["id"]))
    card["leak_score"] = calculate_leak_score(card)
    return card


def calculate_leak_score(p: dict) -> float:
    hr9 = p.get("hr9") or 0
    barrel = (p.get("barrel_allowed") or 0) / 10
    xwoba = (p.get("xwoba_allowed") or 0) * 10
    hard = (p.get("hard_hit_allowed") or 0) / 10
    k = (p.get("k_rate") or 0) / 10
    predict = p.get("pitch_predictability") or 0
    return round(hr9 * 2 + barrel * 1.5 + xwoba * 1.2 + hard - k * .8 + predict, 3)
