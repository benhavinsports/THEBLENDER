def lock_target(game: dict) -> dict:
    away_pitcher = game.get("away_pitcher_card") or {}
    home_pitcher = game.get("home_pitcher_card") or {}
    # Away pitcher is faced by home offense; home pitcher is faced by away offense.
    candidates = [
        {"team": game.get("home"), "side": "home", "pitcher": away_pitcher, "leak_score": away_pitcher.get("leak_score", 0)},
        {"team": game.get("away"), "side": "away", "pitcher": home_pitcher, "leak_score": home_pitcher.get("leak_score", 0)},
    ]
    return max(candidates, key=lambda x: x.get("leak_score", 0))
