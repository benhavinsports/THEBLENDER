from __future__ import annotations

import datetime as dt
from services.http import get_json

SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"


def _pitcher(team_entry: dict) -> dict:
    probable = team_entry.get("probablePitcher") or {}
    return {
        "id": probable.get("id"),
        "name": probable.get("fullName"),
    }


def get_mlb_slate(date: str | None = None) -> list[dict]:
    date = date or dt.date.today().isoformat()
    data = get_json(
        SCHEDULE_URL,
        params={
            "sportId": 1,
            "date": date,
            "hydrate": "team,probablePitcher,venue,linescore",
        },
    )
    games: list[dict] = []
    for day in data.get("dates", []):
        for raw in day.get("games", []):
            teams = raw.get("teams", {})
            away_entry = teams.get("away", {})
            home_entry = teams.get("home", {})
            away_team = away_entry.get("team", {})
            home_team = home_entry.get("team", {})
            games.append({
                "game_id": raw.get("gamePk"),
                "game_date": raw.get("gameDate"),
                "date": date,
                "away": away_team.get("name", "UNKNOWN AWAY"),
                "home": home_team.get("name", "UNKNOWN HOME"),
                "away_id": away_team.get("id"),
                "home_id": home_team.get("id"),
                "away_pitcher": _pitcher(away_entry),
                "home_pitcher": _pitcher(home_entry),
                "venue": raw.get("venue", {}),
                "status": (raw.get("status") or {}).get("abstractGameState"),
            })
    return games
