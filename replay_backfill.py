from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.core import build_boards, run_blender
from services.replay import pregame_cutoff, stable_signature
from services.slate import get_mlb_slate


FORBIDDEN_REPLAY_TOKENS = (
    "MLB_SEASON",
    "SAVANT_BATTED_BALL_LEADERBOARD",
    "SAVANT_BAT_TRACKING",
    "PITCH_ARSENAL_LEADERBOARD",
    "FULL_SEASON",
)


def _iso_days(start: str, end: str):
    a = dt.date.fromisoformat(start)
    b = dt.date.fromisoformat(end)
    if b < a:
        raise ValueError("end date must be >= start date")
    cur = a
    while cur <= b:
        yield cur.isoformat()
        cur += dt.timedelta(days=1)


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _iter_strings(value: Any):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _iter_strings(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)
    elif value is not None:
        yield str(value)


def replay_source_guard(results: list[dict], replay_date: str) -> dict:
    cutoff = pregame_cutoff(replay_date, fallback_year=int(replay_date[:4]))
    issues: list[dict] = []
    warnings: list[dict] = []

    for result in results:
        game = result.get("game")
        health = result.get("pipeline_health") or {}
        if health.get("replay_mode") is not True:
            issues.append({"game": game, "code": "REPLAY_MODE_NOT_SET"})
        if health.get("replay_cutoff") != cutoff:
            issues.append({"game": game, "code": "WRONG_REPLAY_CUTOFF", "found": health.get("replay_cutoff"), "expected": cutoff})
        if not health.get("input_signature"):
            issues.append({"game": game, "code": "MISSING_INPUT_SIGNATURE"})

        target = result.get("target_side") or {}
        pitcher = target.get("pitcher") or {}
        if pitcher and pitcher.get("stats_cutoff") not in {None, cutoff}:
            issues.append({"game": game, "code": "PITCHER_CUTOFF_MISMATCH", "found": pitcher.get("stats_cutoff"), "expected": cutoff})

        for profile in result.get("gate1_profiles") or []:
            for key in ("basic_stats_cutoff", "statcast_profile_cutoff"):
                value = profile.get(key)
                if value not in {None, cutoff}:
                    issues.append({"game": game, "player": profile.get("name"), "code": f"{key.upper()}_MISMATCH", "found": value, "expected": cutoff})

        all_text = "\n".join(_iter_strings(result)).upper()
        for token in FORBIDDEN_REPLAY_TOKENS:
            if token in all_text:
                issues.append({"game": game, "code": "FORBIDDEN_FUTURE_SOURCE", "token": token})

        lineup_source = health.get("lineup_source")
        if lineup_source not in {"MLB", None}:
            warnings.append({"game": game, "code": "NON_OFFICIAL_LINEUP_SOURCE", "source": lineup_source})

    return {
        "passed": not issues,
        "replay_date": replay_date,
        "cutoff": cutoff,
        "issues": issues,
        "warnings": warnings,
    }


def freeze_date(date: str, out_root: Path, *, force: bool = False) -> dict:
    season = int(date[:4])
    day_dir = out_root / date
    card_path = day_dir / "card.json"
    error_path = day_dir / "replay_error.json"

    if card_path.exists() and not force:
        return {"date": date, "status": "ALREADY_FROZEN", "path": str(card_path)}

    slate = get_mlb_slate(date)
    if not slate:
        payload = {
            "date": date,
            "status": "NO_SLATE_OR_NETWORK",
            "message": "No MLB slate data returned. No card was frozen.",
        }
        _atomic_json(error_path, payload)
        return payload

    results = run_blender(slate, season, replay_mode=True)
    boards = build_boards(results)
    guard = replay_source_guard(results, date)

    frozen = {
        "schema": "MLB_HOME_RUN_BLENDER_REPLAY_CARD_V1",
        "source_type": "BLENDER_REPLAY_CARD",
        "date": date,
        "season": season,
        "pregame_cutoff": pregame_cutoff(date, fallback_year=season),
        "frozen_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "slate": slate,
        "results": results,
        "boards": boards,
        "source_guard": guard,
        "core3_complete": len(boards.get("core3") or []) == 3,
    }
    frozen["card_signature"] = stable_signature({
        "date": date,
        "pregame_cutoff": frozen["pregame_cutoff"],
        "slate": slate,
        "results": results,
        "boards": boards,
    })

    # A replay with a detected future-data source is never allowed to become a
    # frozen historical card. Keep the diagnostic separate instead.
    if not guard["passed"]:
        payload = {
            "date": date,
            "status": "SOURCE_GUARD_BLOCKED",
            "source_guard": guard,
        }
        _atomic_json(error_path, payload)
        return payload

    _atomic_json(card_path, frozen)
    if error_path.exists():
        error_path.unlink()
    return {
        "date": date,
        "status": "FROZEN",
        "core3": [x.get("survivor") for x in boards.get("core3") or []],
        "core3_complete": frozen["core3_complete"],
        "card_signature": frozen["card_signature"],
        "path": str(card_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze hindsight-safe Blender replay cards before grading results.")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD; defaults to --start")
    parser.add_argument("--out", default=str(ROOT / "replay_output"))
    parser.add_argument("--force", action="store_true", help="replace an existing frozen card")
    args = parser.parse_args()

    out_root = Path(args.out)
    summaries = []
    for date in _iso_days(args.start, args.end or args.start):
        summary = freeze_date(date, out_root, force=args.force)
        summaries.append(summary)
        print(json.dumps(summary, sort_keys=True))

    index = out_root / "freeze_index.json"
    _atomic_json(index, summaries)
    return 0 if all(item.get("status") in {"FROZEN", "ALREADY_FROZEN"} for item in summaries) else 2


if __name__ == "__main__":
    raise SystemExit(main())
