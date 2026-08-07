def final_lock(game: dict, owner: dict | None, audit: dict) -> dict:
    game_name=f"{game.get('away','UNKNOWN')} vs {game.get('home','UNKNOWN')}"
    if not owner:
        return {"game":game_name,"survivor":"NO SURVIVOR","why":"NO VALID EVENT OWNER","status":"FAILED"}
    return {
        "game":game_name,"survivor":owner.get("name") or owner.get("player") or "UNKNOWN",
        "survivor_id":owner.get("id"),
        "team":owner.get("team"),"why":owner.get("event_reason","HR EVENT RECIPIENT"),
        "event_score":owner.get("ownership_score",0),"status":"LOCKED","audit_status":audit,
    }
