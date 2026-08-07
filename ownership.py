from __future__ import annotations


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def assign_event_ownership(players: list[dict], game: dict, target: dict) -> list[dict]:
    """Gate 12: score who receives the event, without name or star bias.

    The locked ownership formula is intentionally narrow. Matchup, environment,
    rhythm and bullpen have already been handled by their own gates and must not
    be counted a second time here.
    """
    for player in players:
        damage_component = _number(player.get("damage_score")) * 0.35
        pull_component = _number(player.get("pull")) * 0.25
        hard_hit_component = _number(player.get("hard_hit")) * 0.20
        opportunity_bonus = 10.0 if (player.get("slot") or 99) <= 5 else 0.0

        player["ownership_components"] = {
            "damage": round(damage_component, 3),
            "pull": round(pull_component, 3),
            "hard_hit": round(hard_hit_component, 3),
            "slot_bonus": opportunity_bonus,
        }
        player["ownership_score"] = round(
            damage_component
            + pull_component
            + hard_hit_component
            + opportunity_bonus,
            3,
        )
        player["event_reason"] = "HR event ownership: damage + pull path + opportunity"
    return players


def get_owner(players: list[dict]):
    return max(players, key=lambda p: p.get("ownership_score", 0)) if players else None
