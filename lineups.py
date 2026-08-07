from __future__ import annotations

import datetime as dt
import re
from html.parser import HTMLParser
from typing import Any

from services.http import get_json, get_text

FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_id}/boxscore"
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
ROSTER_URL = "https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
ROTOWIRE_URL = "https://www.rotowire.com/baseball/daily-lineups.php"

TEAM_ABBREVIATIONS = {
    "Arizona Diamondbacks": "ARI",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Athletics": "ATH",
    "Oakland Athletics": "ATH",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}


def _slot(value: Any, fallback: int) -> int:
    try:
        number = int(str(value))
        if number >= 100:
            number //= 100
        return number if 1 <= number <= 9 else fallback
    except (TypeError, ValueError):
        return fallback


def _person_name(record: dict) -> str | None:
    person = record.get("person") or {}
    return person.get("fullName") or person.get("name") or record.get("fullName")


def _player_record(players_map: dict, player_id: Any) -> dict:
    if not player_id:
        return {}

    direct = (
        players_map.get(f"ID{player_id}")
        or players_map.get(str(player_id))
        or players_map.get(player_id)
    )
    if isinstance(direct, dict):
        return direct

    for record in players_map.values():
        if not isinstance(record, dict):
            continue
        person = record.get("person") or {}
        if str(person.get("id")) == str(player_id):
            return record

    return {}


def _ordered_player_ids(team_data: dict) -> list:
    """Return the nine starting hitters in batting-order order."""
    order = team_data.get("battingOrder") or []
    if order:
        return list(order)[:9]

    players_map = team_data.get("players") or {}
    ordered: list[tuple[int, Any]] = []

    for record in players_map.values():
        if not isinstance(record, dict):
            continue
        batting_order = record.get("battingOrder")
        person = record.get("person") or {}
        player_id = person.get("id")
        if batting_order and player_id:
            ordered.append((_slot(batting_order, 99), player_id))

    ordered.sort(key=lambda item: item[0])
    resolved = [player_id for slot, player_id in ordered if 1 <= slot <= 9]
    if resolved:
        return resolved[:9]

    # `batters` is only a last official-feed fallback because during live games
    # it can include substitutes who were not in the starting nine.
    batters = team_data.get("batters") or []
    return list(batters)[:9]



def _starter_player_ids(team_data: dict) -> list:
    """Recover the original starting nine from a historical MLB boxscore.

    MLB encodes battingOrder as 100, 200, ... 900 for starters. Replacement
    hitters occupy derivative values such as 201/202. Historical replay must
    never allow a post-start substitution to enter the pregame player pool.
    """
    players_map = team_data.get("players") or {}
    starters: list[tuple[int, int]] = []
    for record in players_map.values():
        if not isinstance(record, dict):
            continue
        raw_order = record.get("battingOrder")
        person = record.get("person") or {}
        player_id = person.get("id")
        try:
            order = int(str(raw_order))
        except (TypeError, ValueError):
            continue
        if not player_id or order < 100 or order > 900 or order % 100 != 0:
            continue
        starters.append((order // 100, int(player_id)))
    starters.sort(key=lambda item: item[0])
    ids = [player_id for slot, player_id in starters if 1 <= slot <= 9]
    return ids[:9]

def _extract_team(
    team_data: dict,
    team_name: str,
    side: str,
    lineup_status: str = "OFFICIAL",
    *,
    starter_only: bool = False,
) -> list[dict]:
    players_map = team_data.get("players") or {}
    player_ids = _starter_player_ids(team_data) if starter_only else _ordered_player_ids(team_data)
    hitters: list[dict] = []
    seen: set[str] = set()

    for fallback, player_id in enumerate(player_ids, 1):
        record = _player_record(players_map, player_id)
        person = record.get("person") or {}
        resolved_id = person.get("id") or player_id
        name = _person_name(record)
        position = ((record.get("position") or {}).get("abbreviation") or "").upper()

        if not resolved_id or not name:
            continue
        if position in {"P", "SP", "RP"}:
            continue

        key = str(resolved_id)
        if key in seen:
            continue
        seen.add(key)

        hitters.append({
            "id": resolved_id,
            "name": name,
            "player": name,
            "team": team_name,
            "side": side,
            "slot": _slot(record.get("battingOrder"), fallback),
            "position": position or "UNKNOWN",
            "handedness": None,
            "lineup_status": lineup_status,
        })

    return hitters[:9]


def _extract_boxscore(feed: dict, game_id: Any) -> dict:
    boxscore = ((feed.get("liveData") or {}).get("boxscore") or {})
    if boxscore:
        return boxscore
    return get_json(BOXSCORE_URL.format(game_id=game_id))


def _normalize_name(value: str | None) -> str:
    text = (value or "").lower().strip()
    text = text.replace("’", "'")
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b", "", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
    attr_map = dict(attrs)
    return set((attr_map.get("class") or "").split())


class _RotoWireParser(HTMLParser):
    """
    Parse RotoWire's public daily-lineup cards without adding dependencies.

    The parser intentionally accepts several class-name variations because
    RotoWire has changed small pieces of this markup over time.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.card_depth: int | None = None
        self.current: dict[str, Any] | None = None
        self.capture_team = False
        self.capture_player = False
        self.team_buffer: list[str] = []
        self.player_buffer: list[str] = []
        self.lineups: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = _classes(attrs)
        attr_map = dict(attrs)

        if self.current is None and tag == "div" and "lineup" in classes:
            self.current = {"teams": [], "players": [[], []], "status": []}
            self.card_depth = self.depth

            # Some page versions expose abbreviations directly as data attrs.
            for key in ("data-away", "data-home", "data-visit"):
                value = attr_map.get(key)
                if value and value.upper() not in self.current["teams"]:
                    self.current["teams"].append(value.upper())

        if self.current is not None:
            if (
                "lineup__abbr" in classes
                or "lineup__team-abbr" in classes
                or "lineup__team" in classes
            ):
                self.capture_team = True
                self.team_buffer = []

            if (
                "lineup__player" in classes
                or "lineup__player-name" in classes
                or "lineup__name" in classes
            ):
                self.capture_player = True
                self.player_buffer = []

            # Player names are commonly stored in the anchor title attribute.
            if tag == "a" and self.capture_player:
                title = attr_map.get("title")
                if title:
                    self.player_buffer.append(title)

        self.depth += 1

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if self.capture_team:
            self.team_buffer.append(data)
        if self.capture_player:
            self.player_buffer.append(data)

        clean = " ".join(data.split()).strip()
        if clean in {"Expected Lineup", "Confirmed Lineup"}:
            self.current["status"].append(clean)

    def handle_endtag(self, tag: str) -> None:
        self.depth -= 1

        if self.current is not None and self.capture_team and tag in {"div", "span", "a"}:
            text = " ".join(" ".join(self.team_buffer).split()).strip().upper()
            if re.fullmatch(r"[A-Z]{2,4}", text) and text not in self.current["teams"]:
                self.current["teams"].append(text)
            self.capture_team = False
            self.team_buffer = []

        if self.current is not None and self.capture_player and tag in {"li", "div", "span", "a"}:
            text = " ".join(" ".join(self.player_buffer).split()).strip()
            # Remove a trailing handedness marker from visible text.
            text = re.sub(r"\s+[RLS]$", "", text).strip()
            if text and not re.fullmatch(r"[A-Z]{1,3}", text):
                team_index = 0 if len(self.current["teams"]) < 2 else 1
                # In normal markup, each lineup list follows its team header.
                if len(self.current["players"][0]) >= 9:
                    team_index = 1
                if text not in self.current["players"][team_index]:
                    self.current["players"][team_index].append(text)
            self.capture_player = False
            self.player_buffer = []

        if (
            self.current is not None
            and self.card_depth is not None
            and self.depth == self.card_depth
            and tag == "div"
        ):
            self.current["teams"] = self.current["teams"][:2]
            self.current["players"] = [
                self.current["players"][0][:9],
                self.current["players"][1][:9],
            ]
            if len(self.current["teams"]) == 2:
                self.lineups.append(self.current)
            self.current = None
            self.card_depth = None


def _parse_rotowire(html: str) -> list[dict[str, Any]]:
    if not html:
        return []
    parser = _RotoWireParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        print(f"ROTOWIRE PARSE ERROR: {exc}")
        return []
    return parser.lineups


def _active_roster(team_id: Any) -> list[dict]:
    if not team_id:
        return []
    data = get_json(
        ROSTER_URL.format(team_id=team_id),
        params={"rosterType": "active"},
    )
    return data.get("roster") or []


def _resolve_projected_names(
    names: list[str],
    team_id: Any,
    team_name: str,
    side: str,
) -> list[dict]:
    roster = _active_roster(team_id)
    by_name: dict[str, dict] = {}
    by_last: dict[str, list[dict]] = {}

    for entry in roster:
        person = entry.get("person") or {}
        full_name = person.get("fullName") or ""
        normalized = _normalize_name(full_name)
        if normalized:
            by_name[normalized] = entry
        last = _normalize_name(full_name.split()[-1] if full_name else "")
        if last:
            by_last.setdefault(last, []).append(entry)

    hitters: list[dict] = []
    seen: set[str] = set()

    for slot, projected_name in enumerate(names[:9], 1):
        normalized = _normalize_name(projected_name)
        entry = by_name.get(normalized)

        if entry is None:
            # RotoWire sometimes displays first initial + surname.
            last_key = _normalize_name(projected_name.split()[-1] if projected_name else "")
            candidates = by_last.get(last_key, [])
            if len(candidates) == 1:
                entry = candidates[0]
            elif candidates:
                first_initial = normalized[:1]
                matching = [
                    candidate
                    for candidate in candidates
                    if _normalize_name(
                        ((candidate.get("person") or {}).get("fullName") or "")
                    ).startswith(first_initial)
                ]
                if len(matching) == 1:
                    entry = matching[0]

        if not entry:
            continue

        person = entry.get("person") or {}
        player_id = person.get("id")
        full_name = person.get("fullName") or projected_name
        position = ((entry.get("position") or {}).get("abbreviation") or "").upper()

        if not player_id or position in {"P", "SP", "RP"}:
            continue
        if str(player_id) in seen:
            continue
        seen.add(str(player_id))

        hitters.append({
            "id": player_id,
            "name": full_name,
            "player": full_name,
            "team": team_name,
            "side": side,
            "slot": slot,
            "position": position or "UNKNOWN",
            "handedness": None,
            "lineup_status": "PROJECTED",
            "lineup_source": "ROTOWIRE",
        })

    return hitters


def _rotowire_projected_lineups(game: dict) -> tuple[list[dict], list[dict]]:
    date_value = game.get("date") or dt.date.today().isoformat()
    html = get_text(ROTOWIRE_URL, params={"date": date_value})
    cards = _parse_rotowire(html)

    away_abbr = TEAM_ABBREVIATIONS.get(game.get("away", ""), "")
    home_abbr = TEAM_ABBREVIATIONS.get(game.get("home", ""), "")

    for card in cards:
        teams = [str(value).upper() for value in card.get("teams", [])]
        if away_abbr not in teams or home_abbr not in teams:
            continue

        away_index = teams.index(away_abbr)
        home_index = teams.index(home_abbr)
        player_lists = card.get("players") or [[], []]

        away_names = player_lists[away_index] if away_index < len(player_lists) else []
        home_names = player_lists[home_index] if home_index < len(player_lists) else []

        away = _resolve_projected_names(
            away_names,
            game.get("away_id"),
            game.get("away", "AWAY"),
            "away",
        )
        home = _resolve_projected_names(
            home_names,
            game.get("home_id"),
            game.get("home", "HOME"),
            "home",
        )

        if len(away) == 9 and len(home) == 9:
            return away, home

    return [], []


def _previous_official_lineup(
    team_id: Any,
    team_name: str,
    side: str,
    before_date: str,
) -> list[dict]:
    """
    Emergency projection only when the projected-lineup page is unavailable.

    It uses the team's latest completed official starting nine and labels that
    data as PROJECTED_PREVIOUS_GAME so it can never be mistaken for confirmed.
    """
    if not team_id:
        return []

    try:
        end_date = dt.date.fromisoformat(before_date) - dt.timedelta(days=1)
    except (TypeError, ValueError):
        end_date = dt.date.today() - dt.timedelta(days=1)
    start_date = end_date - dt.timedelta(days=10)

    schedule = get_json(
        SCHEDULE_URL,
        params={
            "sportId": 1,
            "teamId": team_id,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "hydrate": "linescore",
        },
    )

    candidates: list[dict] = []
    for day in schedule.get("dates", []):
        candidates.extend(day.get("games", []))

    candidates.sort(key=lambda raw: raw.get("gameDate") or "", reverse=True)

    for raw in candidates:
        if (raw.get("status") or {}).get("abstractGameState") != "Final":
            continue
        game_id = raw.get("gamePk")
        boxscore = get_json(BOXSCORE_URL.format(game_id=game_id))
        teams = boxscore.get("teams") or {}

        raw_away_id = (((raw.get("teams") or {}).get("away") or {}).get("team") or {}).get("id")
        box_side = "away" if str(raw_away_id) == str(team_id) else "home"
        lineup = _extract_team(
            teams.get(box_side) or {},
            team_name,
            side,
            lineup_status="PROJECTED",
        )
        if len(lineup) == 9:
            for hitter in lineup:
                hitter["lineup_source"] = "PREVIOUS_OFFICIAL_LINEUP"
            return lineup

    return []


def build_game_pool(game: dict, *, replay_mode: bool = False) -> list[dict]:
    """
    Build a complete two-team hitter pool for pregame Blender runs.

    Source priority:
      1. Official MLB starting lineups from the live feed/boxscore.
      2. RotoWire Expected/Confirmed daily lineups before MLB publishes them.
      3. Latest completed official lineup as an emergency projection fallback.

    The function never mixes one official side with one stale side silently.
    Both sides must contain nine non-pitchers before a full pool is returned.
    """
    game_id = game.get("game_id") or game.get("gamePk")
    if not game_id:
        return []

    feed = get_json(FEED_URL.format(game_id=game_id))
    boxscore = _extract_boxscore(feed, game_id)
    teams = boxscore.get("teams") or {}

    away = _extract_team(
        teams.get("away") or {},
        game.get("away", "AWAY"),
        "away",
        starter_only=replay_mode,
    )
    home = _extract_team(
        teams.get("home") or {},
        game.get("home", "HOME"),
        "home",
        starter_only=replay_mode,
    )

    # Retry the dedicated boxscore endpoint because it can update before the
    # nested live-feed boxscore does.
    if len(away) < 9 or len(home) < 9:
        retry = get_json(BOXSCORE_URL.format(game_id=game_id))
        retry_teams = retry.get("teams") or {}
        retry_away = _extract_team(
            retry_teams.get("away") or {},
            game.get("away", "AWAY"),
            "away",
            starter_only=replay_mode,
        )
        retry_home = _extract_team(
            retry_teams.get("home") or {},
            game.get("home", "HOME"),
            "home",
            starter_only=replay_mode,
        )
        if len(retry_away) == 9:
            away = retry_away
        if len(retry_home) == 9:
            home = retry_home

    if len(away) == 9 and len(home) == 9:
        game["lineup_status"] = "OFFICIAL"
        game["lineup_source"] = "MLB"
        return away + home

    # Historical replay is allowed to use only the archived MLB starting nine.
    # Current RotoWire and current active-roster fallbacks would leak future roster state.
    if replay_mode:
        game["lineup_status"] = "UNAVAILABLE"
        game["lineup_source"] = "HISTORICAL_MLB_STARTERS_UNAVAILABLE"
        return []

    # Before official lineups post, restore the intended projected-lineup path.
    projected_away, projected_home = _rotowire_projected_lineups(game)
    if len(projected_away) == 9 and len(projected_home) == 9:
        game["lineup_status"] = "PROJECTED"
        game["lineup_source"] = "ROTOWIRE"
        return projected_away + projected_home

    # Network/page-layout safety net. This is still explicitly tagged projected.
    date_value = game.get("date") or dt.date.today().isoformat()
    fallback_away = _previous_official_lineup(
        game.get("away_id"),
        game.get("away", "AWAY"),
        "away",
        date_value,
    )
    fallback_home = _previous_official_lineup(
        game.get("home_id"),
        game.get("home", "HOME"),
        "home",
        date_value,
    )

    if len(fallback_away) == 9 and len(fallback_home) == 9:
        game["lineup_status"] = "PROJECTED"
        game["lineup_source"] = "PREVIOUS_OFFICIAL_LINEUP"
        return fallback_away + fallback_home

    game["lineup_status"] = "UNAVAILABLE"
    game["lineup_source"] = "NONE"
    return []
