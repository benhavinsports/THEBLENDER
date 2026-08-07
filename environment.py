from __future__ import annotations

from services.http import get_json

FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"

PARK_FACTORS = {
    "Coors Field": 1.20, "Great American Ball Park": 1.10,
    "Yankee Stadium": 1.07, "Citizens Bank Park": 1.06,
    "Fenway Park": 1.05, "Dodger Stadium": 1.00,
    "Oracle Park": .92, "T-Mobile Park": .92,
}


def build_environment_card(game: dict, *, replay_mode: bool = False) -> dict:
    venue_name = (game.get("venue") or {}).get("name")
    weather = {}
    game_id = game.get("game_id")
    if game_id:
        feed = get_json(FEED_URL.format(game_id=game_id))
        weather = ((feed.get("gameData") or {}).get("weather") or {})
        venue_name = (((feed.get("gameData") or {}).get("venue") or {}).get("name") or venue_name)
    temp = weather.get("temp")
    wind = weather.get("wind") or ""
    condition = weather.get("condition")
    return {
        "venue": venue_name,
        "park_factor": PARK_FACTORS.get(venue_name, 1.0),
        "temperature": temp,
        "wind": wind,
        "condition": condition,
        "environment_score": score_environment(PARK_FACTORS.get(venue_name, 1.0), temp, wind),
        "source": "MLB_GAME_FEED_ARCHIVE" if replay_mode else "MLB_GAME_FEED",
    }


def score_environment(park: float, temp, wind: str) -> float:
    score = (park - 1.0) * 20
    try:
        t = float(temp)
        if t >= 80: score += 2
        elif t <= 55: score -= 2
    except (TypeError, ValueError): pass
    lower = str(wind).lower()
    if "out" in lower: score += 2
    if "in" in lower:
        digits = "".join(ch for ch in lower if ch.isdigit())
        if digits and int(digits) >= 10: score -= 3
    return round(score, 3)
