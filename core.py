from __future__ import annotations

from engine.audit import run_audit
from engine.final_lock import final_lock
from engine.gates import gate_log, run_all_gates
from engine.target_layer import lock_target
from services.bullpen import build_bullpen_card
from services.environment import build_environment_card
from services.lineups import build_game_pool
from services.pitchers import build_pitcher_card
from services.statcast import get_game_matchup_profiles
from services.stats import attach_stats
from services.replay import pregame_cutoff, stable_signature


def prepare_game(raw_game: dict, season: int, *, replay_mode: bool = False, cutoff: str | None = None) -> dict:
    game = dict(raw_game)
    away_raw = dict(game.get("away_pitcher") or {})
    away_raw.update({"side": "away", "team": game.get("away")})
    home_raw = dict(game.get("home_pitcher") or {})
    home_raw.update({"side": "home", "team": game.get("home")})
    game["away_pitcher_card"] = build_pitcher_card(away_raw, season, as_of=cutoff, replay_mode=replay_mode)
    game["home_pitcher_card"] = build_pitcher_card(home_raw, season, as_of=cutoff, replay_mode=replay_mode)
    game["environment"] = build_environment_card(game, replay_mode=replay_mode)
    game["away_bullpen"] = build_bullpen_card(
        game.get("away_bullpen"),
        team_id=game.get("away_id"),
        season=season,
        team_name=game.get("away"),
        as_of=cutoff,
        replay_mode=replay_mode,
    )
    game["home_bullpen"] = build_bullpen_card(
        game.get("home_bullpen"),
        team_id=game.get("home_id"),
        season=season,
        team_name=game.get("home"),
        as_of=cutoff,
        replay_mode=replay_mode,
    )
    return game


def _wire_matchup_context(hitters: list[dict], game: dict, target: dict, season: int, *, as_of: str | None = None, replay_mode: bool = False) -> None:
    """Attach real pitch-type, zone, and mistake-location matchup evidence."""
    target_hitters = [player for player in hitters if player.get("side") == target.get("side")]
    hitter_ids = tuple(
        int(player["id"])
        for player in target_hitters
        if player.get("id") is not None
    )
    opposing_pitcher = target.get("pitcher") or {}
    pitcher_id = opposing_pitcher.get("id")
    profiles = get_game_matchup_profiles(
        hitter_ids,
        int(pitcher_id) if pitcher_id else None,
        int(season),
        as_of if replay_mode else game.get("date"),
        strict_as_of=replay_mode,
    )

    bullpen = game.get("away_bullpen") if target.get("side") == "home" else game.get("home_bullpen")
    bullpen = bullpen or {}

    for player in target_hitters:
        matchup = profiles.get(int(player["id"])) if player.get("id") is not None else None
        if matchup:
            player.update(matchup)
        else:
            player.update(
                {
                    "pitch_type_edge": None,
                    "zone_edge": None,
                    "pitch_edge": None,
                    "pitch_edge_source": "MATCHUP_DATA_INCOMPLETE",
                    "matchup_data_complete": False,
                    "mistake_edge": None,
                    "pitcher_mistake_rate": None,
                    "pitch_matchup_evidence": [],
                    "zone_matchup_evidence": [],
                    "mistake_matchup_evidence": [],
                    "pitcher_top_pitches": [],
                    "pitcher_top_zones": [],
                }
            )
        player["bullpen_risk"] = bullpen.get("risk_score")
        player["bullpen_source"] = bullpen.get("source")


def _debug_profiles(hitters: list[dict]) -> list[dict]:
    fields = (
        "id",
        "name",
        "team",
        "side",
        "slot",
        "lineup_status",
        "pull",
        "pull_percent",
        "pua",
        "pull_barrel",
        "pull_air_source",
        "pull_identity_source",
        "fb",
        "air_source",
        "hard_hit",
        "barrel",
        "ev",
        "blast",
        "squared_up",
        "sweet_spot",
        "bat_speed",
        "iso",
        "hr_pa",
        "damage_score",
        "hr_model_score",
        "pitch_type_edge",
        "zone_edge",
        "pitch_edge",
        "pitch_edge_source",
        "mistake_edge",
        "pitcher_mistake_rate",
        "hr_heat",
        "recent_hr",
        "recent_pa",
        "protection",
        "protection_score",
        "bullpen_risk",
        "bullpen_source",
        "advanced_metrics_loaded",
        "basic_stats_source",
        "basic_stats_cutoff",
        "statcast_profile_source",
        "statcast_profile_cutoff",
    )
    return [{field: player.get(field) for field in fields} for player in hitters]


def _who_result(game: dict, reason: str, *, status: str = "WHO") -> dict:
    return {
        "game": f"{game.get('away', 'UNKNOWN')} vs {game.get('home', 'UNKNOWN')}",
        "survivor": "WHO",
        "team": None,
        "why": reason,
        "event_score": 0,
        "status": status,
        "core_lane": "CHAOS" if status == "WHO" else None,
    }


def run_blender(games: list[dict], season: int, *, replay_mode: bool = False) -> list[dict]:
    results: list[dict] = []
    for raw_game in games:
        cutoff = pregame_cutoff(raw_game.get("date"), fallback_year=season) if replay_mode else raw_game.get("date")
        game = prepare_game(raw_game, season, replay_mode=replay_mode, cutoff=cutoff)
        if replay_mode:
            game["replay_mode"] = True
            game["replay_cutoff"] = cutoff
        target = lock_target(game)
        raw_hitters = build_game_pool(game, replay_mode=replay_mode)
        side_counts = {
            "away": sum(1 for player in raw_hitters if player.get("side") == "away"),
            "home": sum(1 for player in raw_hitters if player.get("side") == "home"),
        }
        hitters = attach_stats(raw_hitters, season, cutoff if replay_mode else game.get("date"), replay_mode=replay_mode)
        _wire_matchup_context(hitters, game, target, season, as_of=cutoff, replay_mode=replay_mode)

        target_side = target.get("side")
        target_pool = [player for player in hitters if player.get("side") == target_side]
        complete_matchups = sum(1 for player in target_pool if player.get("matchup_data_complete"))
        direct_pull_air = sum(
            1
            for player in target_pool
            if player.get("pull_identity_source") == "SAVANT_PULL_PERCENTILE"
            and "DIRECT_PULL_AIR" in str(player.get("pull_air_source") or "")
        )
        pipeline_health = {
            "away_lineup": side_counts["away"],
            "home_lineup": side_counts["home"],
            "lineup_source": game.get("lineup_source"),
            "pitcher_cards": bool(game.get("away_pitcher_card")) and bool(game.get("home_pitcher_card")),
            "advanced_profiles": sum(1 for player in hitters if player.get("advanced_metrics_loaded")),
            "target_profiles": len(target_pool),
            "direct_pull_air_profiles": direct_pull_air,
            "real_pitch_zone_matchups": complete_matchups,
            "environment": bool(game.get("environment")),
            "away_bullpen_source": (game.get("away_bullpen") or {}).get("source"),
            "home_bullpen_source": (game.get("home_bullpen") or {}).get("source"),
            "replay_mode": replay_mode,
            "replay_cutoff": cutoff if replay_mode else None,
        }
        if replay_mode:
            signature_payload = {
                "date": game.get("date"),
                "cutoff": cutoff,
                "away": game.get("away"),
                "home": game.get("home"),
                "away_pitcher": game.get("away_pitcher_card"),
                "home_pitcher": game.get("home_pitcher_card"),
                "environment": game.get("environment"),
                "lineup": [
                    {
                        "id": p.get("id"), "name": p.get("name"), "side": p.get("side"),
                        "slot": p.get("slot"), "lineup_status": p.get("lineup_status"),
                        "statcast_cutoff": p.get("statcast_profile_cutoff"),
                        "basic_cutoff": p.get("basic_stats_cutoff"),
                    }
                    for p in hitters
                ],
            }
            pipeline_health["input_signature"] = stable_signature(signature_payload)
        profiles = _debug_profiles(hitters)

        if not target_pool:
            result = _who_result(
                game,
                f"TARGET LINEUP NOT LOADED: {target.get('team')} ({target_side})",
                status="DATA ERROR",
            )
            result.update(
                {
                    "target_side": target,
                    "lineup_counts": side_counts,
                    "pipeline_health": pipeline_health,
                    "gate1_profiles": profiles,
                    "audit": [
                        gate_log(
                            0,
                            "Hydrated Game Pool",
                            len(hitters),
                            len(hitters),
                            note={
                                "data_status": "UNAVAILABLE",
                                "target": target,
                                "lineup_counts": side_counts,
                            },
                        )
                    ],
                }
            )
            result["game_id"] = game.get("game_id")
            result["date"] = game.get("date")
            results.append(result)
            continue

        survivors, logs = run_all_gates(hitters, game, target)
        audit = run_audit(survivors, logs)
        audit_before = len(survivors)
        audit_after = audit_before if audit.get("passed") else 0
        logs.append(
            gate_log(
                17,
                "Audit",
                audit_before,
                audit_after,
                []
                if audit.get("passed")
                else [
                    {
                        "player": survivors[0].get("name") if survivors else "WHO",
                        "reason": "audit failed",
                    }
                ],
                note=audit,
            )
        )

        owner = survivors[0] if audit.get("lockable") else None
        if owner:
            result = final_lock(game, owner, audit)
            result.update(
                {
                    "core_lane": owner.get("core_lane"),
                    "conversion_band": owner.get("conversion_band"),
                    "conversion_label": owner.get("conversion_label"),
                    "last_man_decision": owner.get("last_man_decision"),
                    "transfer_flag": bool(owner.get("transfer_flag")),
                    "chaos_flag": bool(owner.get("chaos_flag")),
                    "core_evidence": {
                        "matchup_trifecta": (
                            float(owner.get("pitch_type_edge") or 0) > 0
                            and float(owner.get("zone_edge") or 0) > 0
                            and float(owner.get("mistake_edge") or 0) > 0
                        ),
                        "recent_heat": owner.get("hr_heat") is True,
                        "positive_protection": float(owner.get("protection") or 0) > 0,
                        "top_four_slot": int(owner.get("slot") or 99) <= 4,
                        "universe_signal": owner.get("universe_signal") is True,
                        "elite_pull_damage": (
                            float(owner.get("pull") or 0) >= 65
                            and float(owner.get("hard_hit") or 0) >= 45
                        ),
                        "hr_pa": owner.get("hr_pa"),
                        "iso": owner.get("iso"),
                        "damage_score": owner.get("damage_score"),
                        "pull": owner.get("pull"),
                        "hard_hit": owner.get("hard_hit"),
                    },
                }
            )
        else:
            issue_codes = [issue.get("code") for issue in audit.get("issues") or []]
            if issue_codes:
                result = _who_result(
                    game,
                    f"DATA ERROR: audit blocked lock ({', '.join(str(code) for code in issue_codes)})",
                    status="DATA ERROR",
                )
            else:
                gate16_note = next(
                    (
                        entry.get("note") or {}
                        for entry in logs
                        if entry.get("gate") == 16
                    ),
                    {},
                )
                decision = gate16_note.get("decision")
                if decision == "WHO_UNRESOLVED_TRUE_TIE":
                    reason = "WHO: true tie remained after every elimination lane; no arbitrary name was forced"
                else:
                    first_empty = audit.get("first_empty_gate")
                    reason = f"WHO: no hitter cleared the full Blender; first empty gate {first_empty}"
                result = _who_result(game, reason)

        lock_before = 1 if owner else 0
        lock_after = 1 if result.get("status") == "LOCKED" else 0
        logs.append(
            gate_log(
                18,
                "Final Lock",
                lock_before,
                lock_after,
                note={
                    "status": result.get("status"),
                    "survivor": result.get("survivor"),
                    "why": result.get("why"),
                },
            )
        )
        result["audit"] = logs
        result["target_side"] = target
        result["pipeline_health"] = pipeline_health
        result["gate1_profiles"] = profiles
        result["game_id"] = game.get("game_id")
        result["date"] = game.get("date")
        if replay_mode:
            result["source_type"] = "BLENDER_REPLAY_CARD"
            result["replay_cutoff"] = cutoff
            result["input_signature"] = pipeline_health.get("input_signature")
        results.append(result)
    return results


def _board_key(result: dict) -> tuple:
    """Cross-game evidence order with no weighted event score or name bias."""
    evidence = result.get("core_evidence") or {}
    decision_priority = {
        "FINISHER_DOMINANCE": 6,
        "ADJACENT_TRANSFER": 5,
        "MATCHUP_DOMINANCE": 4,
        "RECENT_PLUS_PROTECTION": 3,
        "UNIVERSE_CONVERGENCE": 2,
        "SOLE_LIVE_HITTER": 1,
        "WHO_CHAOS": 0,
    }
    return (
        int(result.get("conversion_band") or 0),
        decision_priority.get(str(result.get("last_man_decision") or ""), 0),
        int(bool(evidence.get("matchup_trifecta"))),
        int(bool(evidence.get("elite_pull_damage"))),
        int(bool(evidence.get("recent_heat"))),
        int(bool(evidence.get("positive_protection"))),
        int(bool(evidence.get("universe_signal"))),
        float(evidence.get("hr_pa") or 0.0),
        float(evidence.get("iso") or 0.0),
        float(evidence.get("damage_score") or 0.0),
        float(evidence.get("pull") or 0.0),
        float(evidence.get("hard_hit") or 0.0),
    )


def build_boards(results: list[dict]) -> dict[str, list[dict]]:
    """Build Core 3, Alt 3, and Chaos 3 from distinct validated game owners.

    The boards use the survivor's audited categorical path and direct gate
    evidence. No weighted event score, site popularity, reputation, or forced
    Primary/Transfer/Chaos quota is used.
    """
    locked = [
        result
        for result in results
        if result.get("status") == "LOCKED"
        and result.get("survivor") not in {None, "NO SURVIVOR", "NONE", "WHO"}
    ]
    standard = sorted(
        [result for result in locked if result.get("core_lane") != "CHAOS"],
        key=_board_key,
        reverse=True,
    )
    chaos = sorted(
        [result for result in locked if result.get("core_lane") == "CHAOS"],
        key=_board_key,
        reverse=True,
    )
    return {
        "core3": standard[:3],
        "alt3": standard[3:6],
        "chaos3": chaos[:3],
    }


def build_core3(results: list[dict]) -> list[dict]:
    return build_boards(results)["core3"]


def build_alt3(results: list[dict]) -> list[dict]:
    return build_boards(results)["alt3"]


def build_chaos3(results: list[dict]) -> list[dict]:
    return build_boards(results)["chaos3"]
