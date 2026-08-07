from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.http import get_json
from services.replay import stable_signature

FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"


def _norm(value: str | None) -> str:
    text = (value or "").lower().replace("’", "'")
    text = re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _box_player_stats(feed: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    teams = (((feed.get("liveData") or {}).get("boxscore") or {}).get("teams") or {})
    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        for record in players.values():
            if not isinstance(record, dict):
                continue
            person = record.get("person") or {}
            pid = person.get("id")
            if not pid:
                continue
            batting = (((record.get("stats") or {}).get("batting")) or {})
            out[int(pid)] = {
                "id": int(pid),
                "name": person.get("fullName"),
                "hits": int(batting.get("hits") or 0),
                "doubles": int(batting.get("doubles") or 0),
                "triples": int(batting.get("triples") or 0),
                "home_runs": int(batting.get("homeRuns") or 0),
                "total_bases": int(batting.get("totalBases") or 0),
                "at_bats": int(batting.get("atBats") or 0),
                "plate_appearances": int(batting.get("plateAppearances") or 0),
            }
    return out


def fetch_results(slate: list[dict]) -> dict:
    homers: list[dict] = []
    stats: dict[int, dict] = {}
    games: list[dict] = []
    for game in slate:
        game_id = game.get("game_id") or game.get("gamePk")
        if not game_id:
            continue
        feed = get_json(FEED_URL.format(game_id=game_id))
        if not feed:
            return {"ok": False, "error": f"No result feed returned for game {game_id}"}
        status = (((feed.get("gameData") or {}).get("status") or {}).get("abstractGameState"))
        games.append({"game_id": game_id, "status": status})
        if status != "Final":
            return {"ok": False, "error": f"Game {game_id} is not final (status={status})"}
        stats.update(_box_player_stats(feed))
        plays = (((feed.get("liveData") or {}).get("plays") or {}).get("allPlays") or [])
        for play in plays:
            result = play.get("result") or {}
            if str(result.get("eventType") or "").lower() != "home_run":
                continue
            matchup = play.get("matchup") or {}
            batter = matchup.get("batter") or {}
            about = play.get("about") or {}
            homers.append({
                "game_id": game_id,
                "player_id": batter.get("id"),
                "player": batter.get("fullName"),
                "inning": about.get("inning"),
                "half": about.get("halfInning"),
                "description": result.get("description"),
            })
    return {"ok": True, "homers": homers, "stats": stats, "games": games}


def _find_stat(pick: dict, stats: dict[int, dict]) -> dict | None:
    pid = pick.get("survivor_id")
    if pid is not None:
        try:
            hit = stats.get(int(pid))
            if hit:
                return hit
        except (TypeError, ValueError):
            pass
    name = _norm(pick.get("survivor"))
    for record in stats.values():
        if _norm(record.get("name")) == name:
            return record
    return None


def _labels(stat: dict | None) -> tuple[str, str]:
    if stat and int(stat.get("home_runs") or 0) > 0:
        return "Hit", "Pass"
    if stat and ((int(stat.get("doubles") or 0) + int(stat.get("triples") or 0) > 0) or int(stat.get("hits") or 0) >= 2):
        return "Near-miss", "Watch"
    return "Miss", "Fail"


def _find_removed_gate(result: dict, player_id: int | None, player_name: str | None) -> float | int | None:
    n = _norm(player_name)
    for entry in result.get("audit") or []:
        gate = entry.get("gate")
        for removal in entry.get("removed") or []:
            if player_id is not None and removal.get("player_id") == player_id:
                return gate
            if _norm(removal.get("player")) == n:
                return gate
    return None


def leakage_report(card: dict, homers: list[dict]) -> dict:
    by_game = {str(r.get("game_id")): r for r in card.get("results") or []}
    leaks: list[dict] = []
    gate16 = 0
    side_lock = 0
    for hr in homers:
        result = by_game.get(str(hr.get("game_id")))
        if not result:
            continue
        profiles = result.get("gate1_profiles") or []
        target = result.get("target_side") or {}
        target_side = target.get("side")
        profile = None
        for p in profiles:
            if hr.get("player_id") is not None and str(p.get("id")) == str(hr.get("player_id")):
                profile = p; break
            if _norm(p.get("name")) == _norm(hr.get("player")):
                profile = p; break
        if profile is None:
            continue
        if profile.get("side") != target_side:
            side_lock += 1
            leaks.append({"player": hr.get("player"), "game_id": hr.get("game_id"), "type": "SIDE_LOCK", "gate": 3})
            continue
        removed_gate = _find_removed_gate(result, hr.get("player_id"), hr.get("player"))
        survivor_match = (
            (hr.get("player_id") is not None and str(result.get("survivor_id")) == str(hr.get("player_id")))
            or _norm(result.get("survivor")) == _norm(hr.get("player"))
        )
        if not survivor_match:
            if removed_gate == 16:
                gate16 += 1
            leaks.append({
                "player": hr.get("player"), "game_id": hr.get("game_id"),
                "type": "TARGET_SIDE_RECIPIENT", "removed_gate": removed_gate,
                "final_survivor": result.get("survivor"),
            })
    return {
        "gate16": {"count": gate16, "status": "LEAK" if gate16 else "NO_CONFIRMED_LEAK"},
        "gate19": {"status": "BASELINE_NOT_PRESENT", "note": "v2.1 replay baseline preserves the original elimination path; Gate 19 is evaluated only after a future patch."},
        "gate21": {"status": "PASS" if card.get("card_signature") else "FAIL", "input_signature_count": len([r for r in card.get("results") or [] if r.get("input_signature")])},
        "gate22": {"status": "AUDITED", "confirmed_leaks": leaks, "side_lock_leaks": side_lock},
        "gate23": {"status": "CALIBRATED"},
    }


def grade(card_path: Path) -> dict:
    card = _load(card_path)
    if card.get("source_type") != "BLENDER_REPLAY_CARD":
        raise ValueError("Not a Blender replay card")
    if not card.get("card_signature"):
        raise ValueError("Frozen card has no signature; refusing to grade")

    # Recheck the immutable signature before any postgame fetch.
    expected = stable_signature({
        "date": card.get("date"),
        "pregame_cutoff": card.get("pregame_cutoff"),
        "slate": card.get("slate"),
        "results": card.get("results"),
        "boards": card.get("boards"),
    })
    if expected != card.get("card_signature"):
        raise ValueError("Frozen card signature mismatch; refusing to grade altered history")

    actual = fetch_results(card.get("slate") or [])
    if not actual.get("ok"):
        return {"date": card.get("date"), "status": "RESULTS_UNAVAILABLE", "error": actual.get("error")}

    stats = actual["stats"]
    board_grades: dict[str, list[dict]] = {}
    for board_name in ("core3", "alt3", "chaos3"):
        rows = []
        for pick in ((card.get("boards") or {}).get(board_name) or []):
            stat = _find_stat(pick, stats)
            primary, secondary = _labels(stat)
            rows.append({
                "player": pick.get("survivor"),
                "player_id": pick.get("survivor_id"),
                "game": pick.get("game"),
                "home_runs": int((stat or {}).get("home_runs") or 0),
                "hits": int((stat or {}).get("hits") or 0),
                "doubles": int((stat or {}).get("doubles") or 0),
                "triples": int((stat or {}).get("triples") or 0),
                "label_1": primary,
                "label_2": secondary,
            })
        board_grades[board_name] = rows

    core = board_grades["core3"]
    core_hits = sum(1 for row in core if row["home_runs"] > 0)
    complete = len(core) == 3
    ticket = "WIN" if complete and core_hits == 3 else ("LOSS" if complete else "NO_TICKET")
    leaks = leakage_report(card, actual["homers"])
    leaks["gate23"].update({"core3_hits": core_hits, "core3_size": len(core), "ticket": ticket})

    return {
        "schema": "MLB_HOME_RUN_BLENDER_REPLAY_GRADE_V1",
        "date": card.get("date"),
        "source_type": card.get("source_type"),
        "card_signature": card.get("card_signature"),
        "graded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "GRADED",
        "ticket": ticket,
        "core3_hits": core_hits,
        "core3_size": len(core),
        "boards": board_grades,
        "actual_home_runs": actual["homers"],
        "leakage": leaks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Grade a previously frozen Blender replay card. Results are never fetched before card signature validation.")
    parser.add_argument("card", help="path to replay_output/YYYY-MM-DD/card.json")
    args = parser.parse_args()
    card_path = Path(args.card)
    output = grade(card_path)
    grade_path = card_path.with_name("grade.json")
    _write(grade_path, output)
    print(json.dumps({"date": output.get("date"), "status": output.get("status"), "ticket": output.get("ticket"), "core3_hits": output.get("core3_hits"), "path": str(grade_path)}, sort_keys=True))
    return 0 if output.get("status") == "GRADED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
