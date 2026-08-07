from __future__ import annotations

from typing import Callable

Player = dict
Predicate = Callable[[Player], tuple[bool, str]]


def gate_log(gate, name, before, after, removed=None, note=None):
    return {
        "gate": gate,
        "name": name,
        "before": before,
        "after": after,
        "removed": removed or [],
        "note": note,
    }


def _number(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric(player: Player, field: str, default: float = -999.0) -> float:
    value = _number(player.get(field))
    return default if value is None else value


def _name(player: Player) -> str:
    return str(player.get("name") or player.get("player") or "UNKNOWN")


def _missing(player: Player, *fields: str) -> list[str]:
    return [field for field in fields if player.get(field) is None]


def _apply(
    players: list[Player],
    gate,
    name: str,
    predicate: Predicate,
    logs: list[dict],
    *,
    note=None,
) -> list[Player]:
    """Apply a strict elimination gate.

    Missing/failed evidence removes the hitter. The gate never rescues a player
    and never chooses a fallback winner.
    """
    before = len(players)
    kept: list[Player] = []
    removed: list[dict] = []
    for player in players:
        passed, reason = predicate(player)
        if passed:
            kept.append(player)
        else:
            removed.append({"player": _name(player), "reason": reason})
    logs.append(gate_log(gate, name, before, len(kept), removed, note=note))
    return kept


def _separator(
    players: list[Player],
    gate,
    name: str,
    predicate: Predicate,
    logs: list[dict],
    *,
    note=None,
) -> list[Player]:
    """Use a support signal only when it genuinely separates the live pool.

    Recent form and protection are supporting evidence, not permission to erase
    an entire valid game. When nobody owns the signal, the incoming pool remains
    unchanged and the audit records NO_SEPARATION. Missing feed data is still
    exposed to Gate 17 and can block the final lock.
    """
    before = len(players)
    if not players:
        logs.append(gate_log(gate, name, 0, 0, note=note))
        return []

    passed: list[Player] = []
    failed: list[tuple[Player, str]] = []
    for player in players:
        ok, reason = predicate(player)
        if ok:
            passed.append(player)
        else:
            failed.append((player, reason))

    if passed and len(passed) < before:
        logs.append(
            gate_log(
                gate,
                name,
                before,
                len(passed),
                [{"player": _name(player), "reason": reason} for player, reason in failed],
                note=note,
            )
        )
        return passed

    detail = {
        "mode": "NO_SEPARATION",
        "failed_checks": [
            {"player": _name(player), "reason": reason} for player, reason in failed
        ],
    }
    if isinstance(note, dict):
        detail = {**note, **detail}
    elif note is not None:
        detail["context"] = note
    logs.append(gate_log(gate, name, before, before, note=detail))
    return list(players)


def _conversion_band(player: Player) -> tuple[int, str, str]:
    """Return the hitter's HR-conversion band without assigning an archetype."""
    missing = _missing(player, "hr_pa", "iso", "damage_score")
    if missing:
        return 0, "NO_CONVERSION", f"missing finisher data: {', '.join(missing)}"

    hr_pa = float(player["hr_pa"])
    iso = float(player["iso"])
    damage = float(player["damage_score"])

    if hr_pa >= 0.050 and iso >= 0.200 and damage >= 65.0:
        return 3, "ELITE_CONVERSION", "elite HR rate, ISO, and damage conversion"
    if hr_pa >= 0.035 and iso >= 0.180 and damage >= 55.0:
        return 2, "STRONG_CONVERSION", "strong HR rate, ISO, and damage conversion"
    if hr_pa >= 0.025 and iso >= 0.150 and damage >= 45.0:
        return 1, "LIVE_CONVERSION", "live HR conversion floor"
    return 0, "NO_CONVERSION", "HR conversion floor failed"


def _chaos_signals(player: Player, game: dict) -> dict:
    environment = game.get("environment") or {}
    return {
        "weak_slot": 5 <= int(player.get("slot") or 99) <= 7,
        "positive_pitch_type": _metric(player, "pitch_type_edge") >= 0.0,
        "positive_zone": _metric(player, "zone_edge") >= 0.0,
        "mistake_leak": _metric(player, "mistake_edge") >= 0.10,
        "bullpen_chaos": _metric(player, "bullpen_risk", 0.0) >= 5.5,
        "unstable_environment": abs(
            _number(environment.get("environment_score"), 0.0) or 0.0
        ) >= 1.5,
        "recent_heat": player.get("hr_heat") is True,
        "isolated_cluster": _metric(player, "protection", 0.0) <= 0.0,
    }


def _universe_convergence(player: Player) -> dict:
    """Build the explicit Gate-13 convergence signal.

    This is a boolean tie-break layer, not a score. It only records whether the
    same hitter owns independent event lanes already proven by earlier gates.
    """
    lanes = {
        "pull_damage": (
            _metric(player, "pull", 0.0) >= 65.0
            and _metric(player, "hard_hit", 0.0) >= 45.0
        ),
        "matchup_trifecta": (
            _metric(player, "pitch_type_edge") > 0.0
            and _metric(player, "zone_edge") > 0.0
            and _metric(player, "mistake_edge") > 0.0
        ),
        "live_rhythm": player.get("hr_heat") is True,
        "protected_access": _metric(player, "protection", -1.0) > 0.0,
        "adjacent_transfer": player.get("transfer_flag") is True,
    }
    active = bool(
        lanes["matchup_trifecta"]
        and lanes["pull_damage"]
        and (lanes["live_rhythm"] or lanes["protected_access"] or lanes["adjacent_transfer"])
    )
    return {"active": active, "lanes": lanes, "source": "GATE_CONVERGENCE"}


def _dominates_fields(
    left: Player,
    right: Player,
    fields: tuple[tuple[str, float], ...],
    *,
    minimum_clear: int,
) -> bool:
    no_worse = 0
    clearly_better = 0
    for field, tolerance in fields:
        left_value = _metric(left, field)
        right_value = _metric(right, field)
        if left_value >= right_value - tolerance:
            no_worse += 1
        if left_value > right_value + tolerance:
            clearly_better += 1
    return no_worse == len(fields) and clearly_better >= minimum_clear


def _unique_dominant(
    players: list[Player],
    fields: tuple[tuple[str, float], ...],
    *,
    minimum_clear: int,
) -> Player | None:
    owners = [
        candidate
        for candidate in players
        if all(
            candidate is other
            or _dominates_fields(
                candidate,
                other,
                fields,
                minimum_clear=minimum_clear,
            )
            for other in players
        )
    ]
    return owners[0] if len(owners) == 1 else None


def _adjacent_transfer(players: list[Player]) -> tuple[list[Player], dict]:
    """Run Gate 10.5 while multiple live hitters still exist.

    A transfer can remove the obvious primary only when exactly one adjacent
    hitter is in the same conversion band, is no worse as a finisher, and owns a
    clear pitch-type/zone advantage. Pressure alone never creates a transfer.
    """
    if len(players) < 2:
        return players, {"executed": False, "reason": "fewer than two live hitters"}

    highest_band = max(int(player.get("conversion_band") or 0) for player in players)
    top_band = [
        player for player in players
        if int(player.get("conversion_band") or 0) == highest_band
    ]
    primary = top_band[0] if len(top_band) == 1 else _unique_dominant(
        top_band,
        (("hr_pa", 0.002), ("iso", 0.010), ("damage_score", 3.0)),
        minimum_clear=2,
    )
    if primary is None:
        return players, {
            "executed": False,
            "reason": "no unique finisher primary",
            "top_band": [_name(player) for player in top_band],
        }

    primary["primary_flag"] = True
    primary_slot = int(primary.get("slot") or 99)
    eligible: list[Player] = []
    evidence: dict[str, dict] = {}

    for adjacent in players:
        if adjacent is primary:
            continue
        if abs(int(adjacent.get("slot") or 99) - primary_slot) != 1:
            continue
        if int(adjacent.get("conversion_band") or 0) != highest_band:
            continue

        pitch_advantage = _metric(adjacent, "pitch_type_edge") - _metric(primary, "pitch_type_edge")
        zone_advantage = _metric(adjacent, "zone_edge") - _metric(primary, "zone_edge")
        finisher_held = (
            _metric(adjacent, "hr_pa", -1.0) >= _metric(primary, "hr_pa", -1.0) - 0.003
            and _metric(adjacent, "iso", -1.0) >= _metric(primary, "iso", -1.0) - 0.015
            and _metric(adjacent, "damage_score", -1.0)
            >= _metric(primary, "damage_score", -1.0) - 5.0
        )
        matchup_owned = (
            pitch_advantage >= 0.0
            and zone_advantage >= 0.0
            and max(pitch_advantage, zone_advantage) >= 0.15
        )
        if not (finisher_held and matchup_owned):
            continue

        eligible.append(adjacent)
        evidence[_name(adjacent)] = {
            "adjacent_slot": True,
            "same_conversion_band": True,
            "finisher_held": True,
            "pitch_advantage": round(pitch_advantage, 3),
            "zone_advantage": round(zone_advantage, 3),
            "recent_support": adjacent.get("hr_heat") is True,
            "protection_support": _metric(adjacent, "protection", -1.0)
            > _metric(primary, "protection", -1.0),
        }

    if len(eligible) != 1:
        return players, {
            "executed": False,
            "reason": "transfer lane not unique",
            "primary": _name(primary),
            "eligible": [_name(player) for player in eligible],
            "evidence": evidence,
        }

    adjacent = eligible[0]
    adjacent["transfer_flag"] = True
    adjacent["primary_source"] = _name(primary)
    adjacent["transfer_evidence"] = evidence[_name(adjacent)]
    kept = [player for player in players if player is not primary]
    return kept, {
        "executed": True,
        "primary": _name(primary),
        "adjacent": _name(adjacent),
        "primary_slot": primary.get("slot"),
        "adjacent_slot": adjacent.get("slot"),
        "conversion_band": highest_band,
        "evidence": evidence[_name(adjacent)],
    }


def _last_man_stage(
    candidates: list[Player],
    winner: Player | None,
    stage: str,
    stages: list[dict],
) -> Player | None:
    if winner is None:
        stages.append({"stage": stage, "before": len(candidates), "after": len(candidates), "mode": "NO_SEPARATION"})
        return None
    stages.append({"stage": stage, "before": len(candidates), "after": 1, "winner": _name(winner)})
    return winner


def run_all_gates(hitters: list[dict], game: dict, target: dict):
    logs: list[dict] = []

    # Gate 0 — hydrated game pool. No side is selected here.
    current = list(hitters)
    logs.append(
        gate_log(
            0,
            "Hydrated Game Pool",
            len(hitters),
            len(current),
            note={
                "data_status": "ACTIVE" if current else "UNAVAILABLE",
                "away_count": sum(1 for player in current if player.get("side") == "away"),
                "home_count": sum(1 for player in current if player.get("side") == "home"),
            },
        )
    )

    # Gate 1 — opposing pitcher vulnerability must be hydrated.
    pitcher = target.get("pitcher") or {}
    pitcher_missing = [
        field
        for field, value in (
            ("pitcher_id", pitcher.get("id")),
            ("throws", pitcher.get("throws")),
            ("hr9", pitcher.get("hr9")),
            ("leak_score", target.get("leak_score")),
        )
        if value is None
    ]
    pitcher_ready = not pitcher_missing
    current = _apply(
        current,
        1,
        "Pitcher Vulnerability",
        lambda _player: (
            pitcher_ready,
            f"pitcher vulnerability data missing: {', '.join(pitcher_missing)}",
        ),
        logs,
        note={
            "data_status": "ACTIVE" if pitcher_ready else "UNAVAILABLE",
            "pitcher": pitcher.get("name"),
            "pitcher_id": pitcher.get("id"),
            "throws": pitcher.get("throws"),
            "hr9": pitcher.get("hr9"),
            "k_rate": pitcher.get("k_rate"),
            "leak_score": target.get("leak_score"),
            "missing": pitcher_missing,
        },
    )

    # Gate 2 — game environment must exist, but is common to every hitter.
    environment = game.get("environment") or {}
    environment_missing = [
        field
        for field in ("venue", "park_factor", "environment_score")
        if environment.get(field) is None
    ]
    environment_ready = not environment_missing
    current = _apply(
        current,
        2,
        "Game Environment",
        lambda _player: (
            environment_ready,
            f"environment data missing: {', '.join(environment_missing)}",
        ),
        logs,
        note={
            **environment,
            "data_status": "ACTIVE" if environment_ready else "UNAVAILABLE",
            "missing": environment_missing,
        },
    )

    # Gate 3 — one pitcher-side only; no cross contamination.
    side_before = len(current)
    target_side = target.get("side")
    side_kept = [player for player in current if player.get("side") == target_side]
    side_removed = [
        {"player": _name(player), "reason": "opposite offense side eliminated"}
        for player in current
        if player.get("side") != target_side
    ]
    current = side_kept
    logs.append(
        gate_log(
            3,
            "Side Lock",
            side_before,
            len(current),
            side_removed,
            note={
                "target_team": target.get("team"),
                "target_side": target_side,
                "opposing_pitcher": pitcher.get("name"),
            },
        )
    )

    # Gate 4 — valid starting hitter pool.
    def valid_hitter(player: Player) -> tuple[bool, str]:
        if player.get("id") is None or not _name(player) or _name(player) == "UNKNOWN":
            return False, "missing player identity"
        if player.get("lineup_status") not in {"OFFICIAL", "CONFIRMED", "PROJECTED"}:
            return False, "not a valid starting lineup status"
        if not 1 <= int(player.get("slot") or 99) <= 9:
            return False, "invalid lineup slot"
        if str(player.get("position") or "").upper() in {"P", "SP", "RP"}:
            return False, "pitcher excluded from hitter pool"
        return True, "valid hitter"

    current = _apply(current, 4, "Valid Hitter Pool", valid_hitter, logs)

    # Gate 5 — Pull-Air identity using Pull% percentile plus direct support lanes.
    pull_complete = sum(
        1
        for player in current
        if player.get("pull") is not None
        and player.get("pull_percent") is not None
        and player.get("pua") is not None
        and player.get("hard_hit") is not None
        and player.get("pull_identity_source") == "SAVANT_PULL_PERCENTILE"
    )

    def pull_air_gate(player: Player) -> tuple[bool, str]:
        missing = _missing(player, "pull", "pull_percent", "pua", "hard_hit")
        if missing:
            return False, f"missing direct Pull-Air data: {', '.join(missing)}"
        if player.get("pull_identity_source") != "SAVANT_PULL_PERCENTILE":
            return False, f"invalid Pull identity source: {player.get('pull_identity_source')}"
        if "DIRECT_PULL_AIR" not in str(player.get("pull_air_source") or ""):
            return False, f"invalid Pull AIR source: {player.get('pull_air_source')}"

        pull = float(player["pull"])
        raw_pull = float(player["pull_percent"])
        pua = float(player["pua"])
        hard_hit = float(player["hard_hit"])
        air = _number(player.get("fb"))
        pull_barrel = _number(player.get("pull_barrel"))
        pitch_edge = _number(player.get("pitch_edge"))

        # Locked combo passes.
        if pull >= 70.0 and hard_hit >= 45.0:
            return True, "elite Pull percentile / HH combo pass"
        if pull >= 65.0 and hard_hit >= 50.0:
            return True, "strong Pull percentile / HH combo pass"
        if pull >= 65.0:
            return True, "Pull percentile pass lane"
        if 55.0 <= pull < 65.0:
            passed = hard_hit >= 45.0 and pitch_edge is not None and pitch_edge >= 0.0
            return passed, "borderline Pull percentile requires HH >=45 and positive pitch edge"

        # The 50-54 lane and the sub-50 exception require every raw support cue.
        support_missing = [
            name
            for name, value in (
                ("air", air),
                ("pull_barrel", pull_barrel),
                ("pitch_edge", pitch_edge),
            )
            if value is None
        ]
        full_support = bool(
            not support_missing
            and raw_pull >= 45.0
            and float(air) >= 40.0
            and pua >= 28.0
            and float(pull_barrel) >= 10.0
            and hard_hit >= 45.0
            and float(pitch_edge) >= 0.0
        )
        if 50.0 <= pull < 55.0:
            return full_support, (
                "50-54 Pull lane owns full raw Pull-Air support"
                if full_support
                else f"50-54 Pull lane lacks full support{': ' + ', '.join(support_missing) if support_missing else ''}"
            )
        if pull < 50.0 and full_support:
            player["pull_floor_exception"] = True
            return True, "sub-50 Pull percentile retained by full raw Pull-Air exception"
        return False, "Pull percentile <50 auto-kill without full raw Pull-Air exception"

    current = _apply(
        current,
        5,
        "Pull-Air Profile",
        pull_air_gate,
        logs,
        note={
            "data_status": "ACTIVE" if pull_complete else "UNAVAILABLE",
            "complete_profiles": pull_complete,
            "required_source": "SAVANT_PULL_PERCENTILE",
            "raw_source": "SAVANT_BATTED_BALL_DIRECT_PULL_AIR",
            "thresholds": {
                "elite": ">=70 with HH >=45",
                "pass": "65-69",
                "borderline": "55-64 plus HH and pitch edge",
                "auto_kill": "<50 unless raw Pull>=45, AIR>=40, PUA>=28, Pull Barrel>=10, HH>=45, positive pitch edge",
            },
        },
    )

    # Gate 6 — damage quality.
    damage_complete = sum(
        1 for player in current if player.get("hard_hit") is not None and player.get("damage_score") is not None
    )

    def damage_gate(player: Player) -> tuple[bool, str]:
        missing = _missing(player, "hard_hit", "damage_score")
        if missing:
            return False, f"missing damage data: {', '.join(missing)}"
        hard_hit = float(player["hard_hit"])
        damage = float(player["damage_score"])
        barrel = _number(player.get("barrel"))
        pull = _metric(player, "pull", 0.0)

        if hard_hit < 40.0:
            return False, "Hard Hit < 40 auto-kill"
        if pull >= 70.0 and hard_hit >= 45.0:
            return True, "Pull >=70 / HH >=45 combo pass"
        if pull >= 65.0 and hard_hit >= 50.0:
            return True, "Pull >=65 / HH >=50 combo pass"
        if hard_hit >= 45.0 and damage >= 45.0:
            return True, "strong damage lane"
        if 40.0 <= hard_hit < 45.0:
            passed = barrel is not None and barrel >= 10.0 and damage >= 55.0
            return passed, "borderline HH requires Barrel >=10 and damage >=55"
        return False, "damage quality below floor"

    current = _apply(
        current,
        6,
        "Damage Quality",
        damage_gate,
        logs,
        note={
            "data_status": "ACTIVE" if damage_complete else "UNAVAILABLE",
            "complete_profiles": damage_complete,
        },
    )

    # Gate 7 — pitch-type matchup is separate from zone.
    pitch_complete = sum(
        1
        for player in current
        if player.get("pitch_type_edge") is not None
        and player.get("pitch_edge_source") == "STATCAST_DETAIL_PITCH_ZONE"
    )

    def pitch_type_gate(player: Player) -> tuple[bool, str]:
        if player.get("pitch_type_edge") is None:
            return False, "pitch-type matchup data missing"
        if player.get("pitch_edge_source") != "STATCAST_DETAIL_PITCH_ZONE":
            return False, f"invalid pitch-type source: {player.get('pitch_edge_source')}"
        return float(player["pitch_type_edge"]) >= 0.0, "negative pitch-type matchup"

    current = _apply(
        current,
        7,
        "Pitch-Type Matchup",
        pitch_type_gate,
        logs,
        note={
            "data_status": "ACTIVE" if pitch_complete else "UNAVAILABLE",
            "complete_profiles": pitch_complete,
            "required_source": "STATCAST_DETAIL_PITCH_ZONE",
            "pitcher": pitcher.get("name"),
        },
    )

    # Gate 8 — zone match remains its own gate.
    zone_complete = sum(
        1
        for player in current
        if player.get("zone_edge") is not None
        and player.get("pitch_edge_source") == "STATCAST_DETAIL_PITCH_ZONE"
    )

    def zone_gate(player: Player) -> tuple[bool, str]:
        if player.get("zone_edge") is None:
            return False, "zone matchup data missing"
        if player.get("pitch_edge_source") != "STATCAST_DETAIL_PITCH_ZONE":
            return False, f"invalid zone source: {player.get('pitch_edge_source')}"
        return float(player["zone_edge"]) >= 0.0, "negative zone matchup"

    current = _apply(
        current,
        8,
        "Zone Match",
        zone_gate,
        logs,
        note={
            "data_status": "ACTIVE" if zone_complete else "UNAVAILABLE",
            "complete_profiles": zone_complete,
            "required_source": "STATCAST_DETAIL_PITCH_ZONE",
        },
    )

    # Gate 9 — count/mistake conversion access.
    mistake_complete = sum(1 for player in current if player.get("mistake_edge") is not None)
    current = _apply(
        current,
        9,
        "Count / Mistake Conversion",
        lambda player: (
            player.get("mistake_edge") is not None and float(player["mistake_edge"]) >= 0.0,
            "mistake-location data missing"
            if player.get("mistake_edge") is None
            else "negative mistake-location conversion",
        ),
        logs,
        note={
            "data_status": "ACTIVE" if mistake_complete else "UNAVAILABLE",
            "complete_profiles": mistake_complete,
            "pitcher_mistake_rate": next(
                (
                    player.get("pitcher_mistake_rate")
                    for player in current
                    if player.get("pitcher_mistake_rate") is not None
                ),
                None,
            ),
        },
    )

    # Gate 10 — HR finisher identity. This is a conversion gate, not an
    # archetype assignment and not a blended ranking.
    conversion_profiles: list[dict] = []
    conversion_before = len(current)
    conversion_kept: list[Player] = []
    conversion_removed: list[dict] = []
    for player in current:
        band, label, reason = _conversion_band(player)
        player["conversion_band"] = band
        player["conversion_label"] = label
        conversion_profiles.append(
            {
                "player": _name(player),
                "band": band,
                "label": label,
                "hr_pa": player.get("hr_pa"),
                "iso": player.get("iso"),
                "damage": player.get("damage_score"),
            }
        )
        if band > 0:
            conversion_kept.append(player)
        else:
            conversion_removed.append({"player": _name(player), "reason": reason})
    current = conversion_kept
    logs.append(
        gate_log(
            10,
            "HR Finisher Identity",
            conversion_before,
            len(current),
            conversion_removed,
            note={
                "profiles": conversion_profiles,
                "rule": "conversion bands only; no Primary/Adjacent/WHO archetype assigned here",
            },
        )
    )

    # Gate 10.5 — mandatory adjacent/decoy transfer decision while the pool is live.
    transfer_before = len(current)
    current, transfer_note = _adjacent_transfer(current)
    transfer_removed = []
    if transfer_note.get("executed"):
        transfer_removed = [
            {
                "player": transfer_note.get("primary"),
                "reason": f"event transferred to adjacent {transfer_note.get('adjacent')}",
            }
        ]
    logs.append(
        gate_log(
            10.5,
            "Adjacent / Decoy Transfer",
            transfer_before,
            len(current),
            transfer_removed,
            note=transfer_note,
        )
    )

    # Gate 11 — recent HR signal. It separates only when the live signal exists.
    recent_loaded = sum(
        1
        for player in current
        if player.get("recent_pa") is not None and player.get("recent_hr") is not None
    )
    current = _separator(
        current,
        11,
        "Recent HR Signal",
        lambda player: (
            player.get("recent_pa") is not None
            and player.get("recent_hr") is not None
            and float(player["recent_pa"]) >= 8.0
            and (float(player["recent_hr"]) >= 1.0 or player.get("hr_heat") is True),
            "recent feed unavailable"
            if player.get("recent_pa") is None or player.get("recent_hr") is None
            else "no live 14-day HR signal",
        ),
        logs,
        note={
            "data_status": "ACTIVE" if recent_loaded else "UNAVAILABLE",
            "loaded_profiles": recent_loaded,
            "window": "14 days",
            "minimum_pa": 8,
        },
    )

    # Gate 12 — only positive protection owns the support lane. Neutral is not a pass.
    protection_loaded = sum(1 for player in current if player.get("protection") is not None)
    current = _separator(
        current,
        12,
        "Lineup Protection",
        lambda player: (
            player.get("protection") is not None and float(player["protection"]) > 0.0,
            "protection data unavailable"
            if player.get("protection") is None
            else "no positive lineup protection",
        ),
        logs,
        note={
            "data_status": "ACTIVE" if protection_loaded else "UNAVAILABLE",
            "loaded_profiles": protection_loaded,
            "pass_rule": "protection > 0",
            "profiles": [
                {
                    "player": _name(player),
                    "protection": player.get("protection"),
                    "protection_score": player.get("protection_score"),
                }
                for player in current
            ],
        },
    )

    # Gate 13 — Universe. Every live hitter receives an explicit, auditable
    # boolean convergence signal; this gate never eliminates by itself.
    universe_profiles: list[dict] = []
    for player in current:
        signal = _universe_convergence(player)
        player["universe_signal"] = bool(signal["active"])
        player["universe_evidence"] = signal
        universe_profiles.append({"player": _name(player), **signal})
    logs.append(
        gate_log(
            13,
            "Universe",
            len(current),
            len(current),
            note={
                "mode": "TIE_BREAK_ONLY",
                "source": "GATE_CONVERGENCE",
                "signals": universe_profiles,
                "rule": "explicit boolean convergence only; no hash, hidden score, or automatic elimination",
            },
        )
    )

    # Gate 14 — Chaos/WHO qualification. It marks a legitimate chaos lane but
    # cannot eliminate or override a stronger finisher band.
    chaos_profiles: list[dict] = []
    chaos_candidates: list[Player] = []
    highest_band = max((int(player.get("conversion_band") or 0) for player in current), default=0)
    for player in current:
        signals = _chaos_signals(player, game)
        player["chaos_signals"] = signals
        qualifies = bool(
            int(player.get("conversion_band") or 0) == highest_band
            and signals["weak_slot"]
            and signals["positive_pitch_type"]
            and signals["positive_zone"]
            and signals["mistake_leak"]
            and (
                signals["bullpen_chaos"]
                or signals["unstable_environment"]
                or signals["recent_heat"]
            )
        )
        if qualifies:
            chaos_candidates.append(player)
        chaos_profiles.append(
            {"player": _name(player), "qualifies": qualifies, "signals": signals}
        )

    if len(chaos_candidates) == 1:
        chaos_candidates[0]["chaos_flag"] = True
        chaos_decision = {
            "trigger": "UNIQUE_WHO_CHAOS",
            "candidate": _name(chaos_candidates[0]),
        }
    else:
        chaos_decision = {
            "trigger": "NO_UNIQUE_CHAOS",
            "candidates": [_name(player) for player in chaos_candidates],
        }
    logs.append(
        gate_log(
            14,
            "Chaos / WHO Engine",
            len(current),
            len(current),
            note={
                **chaos_decision,
                "profiles": chaos_profiles,
                "rule": "chaos is marked only inside the strongest live conversion band",
            },
        )
    )

    # Gate 15 — pressure can never outweigh the strongest live finisher band.
    finisher_before = len(current)
    finisher_pool = list(current)
    if current:
        highest_band = max(int(player.get("conversion_band") or 0) for player in current)
        current = [
            player
            for player in current
            if int(player.get("conversion_band") or 0) == highest_band
        ]
    else:
        highest_band = None
    logs.append(
        gate_log(
            15,
            "Finisher Gate",
            finisher_before,
            len(current),
            [
                {
                    "player": _name(player),
                    "reason": f"lower conversion band {player.get('conversion_band')}",
                }
                for player in finisher_pool
                if player not in current
            ],
            note={
                "live_conversion_band": highest_band,
                "rule": "finisher conversion is resolved before transfer pressure, recent form, Universe, or chaos",
            },
        )
    )

    # Gate 16 — last-man elimination. Each stage is a discrete ownership test;
    # no weighted score, name order, slate order, or reputation tie-break exists.
    last_before = len(current)
    if not current:
        logs.append(
            gate_log(
                16,
                "Last-Man Elimination",
                0,
                0,
                note={"decision": "NO_LIVE_HITTER", "stages": []},
            )
        )
        return [], logs

    candidates = list(current)
    stages: list[dict] = []
    winner: Player | None = candidates[0] if len(candidates) == 1 else None
    decision = "SOLE_LIVE_HITTER" if winner is not None else None

    if winner is None:
        transfer_live = [player for player in candidates if player.get("transfer_flag") is True]
        transfer_winner = transfer_live[0] if len(transfer_live) == 1 else None
        if transfer_winner is not None:
            # A transfer cannot beat a clearly stronger finisher that remained live.
            clearly_stronger = any(
                other is not transfer_winner
                and _dominates_fields(
                    other,
                    transfer_winner,
                    (("hr_pa", 0.002), ("iso", 0.010), ("damage_score", 3.0)),
                    minimum_clear=2,
                )
                for other in candidates
            )
            if clearly_stronger:
                transfer_winner = None
        winner = _last_man_stage(candidates, transfer_winner, "ADJACENT_TRANSFER", stages)
        if winner is not None:
            decision = "ADJACENT_TRANSFER"

    if winner is None:
        finisher_winner = _unique_dominant(
            candidates,
            (("hr_pa", 0.002), ("iso", 0.010), ("damage_score", 3.0)),
            minimum_clear=2,
        )
        winner = _last_man_stage(candidates, finisher_winner, "FINISHER_DOMINANCE", stages)
        if winner is not None:
            decision = "FINISHER_DOMINANCE"

    if winner is None:
        matchup_winner = _unique_dominant(
            candidates,
            (("pitch_type_edge", 0.05), ("zone_edge", 0.05), ("mistake_edge", 0.05)),
            minimum_clear=2,
        )
        winner = _last_man_stage(candidates, matchup_winner, "MATCHUP_DOMINANCE", stages)
        if winner is not None:
            decision = "MATCHUP_DOMINANCE"

    if winner is None:
        live_support = [
            player
            for player in candidates
            if player.get("hr_heat") is True and _metric(player, "protection", -1.0) > 0.0
        ]
        support_winner = live_support[0] if len(live_support) == 1 else None
        winner = _last_man_stage(candidates, support_winner, "RECENT_PLUS_PROTECTION", stages)
        if winner is not None:
            decision = "RECENT_PLUS_PROTECTION"

    if winner is None:
        universe_live = [player for player in candidates if player.get("universe_signal") is True]
        universe_winner = universe_live[0] if len(universe_live) == 1 else None
        winner = _last_man_stage(candidates, universe_winner, "UNIVERSE_CONVERGENCE", stages)
        if winner is not None:
            decision = "UNIVERSE_CONVERGENCE"

    if winner is None:
        chaos_live = [player for player in candidates if player.get("chaos_flag") is True]
        chaos_winner = chaos_live[0] if len(chaos_live) == 1 else None
        winner = _last_man_stage(candidates, chaos_winner, "WHO_CHAOS", stages)
        if winner is not None:
            decision = "WHO_CHAOS"

    if winner is None:
        logs.append(
            gate_log(
                16,
                "Last-Man Elimination",
                last_before,
                0,
                [
                    {"player": _name(player), "reason": "unresolved true tie; no arbitrary owner"}
                    for player in current
                ],
                note={
                    "decision": "WHO_UNRESOLVED_TRUE_TIE",
                    "live_hitters": [_name(player) for player in candidates],
                    "stages": stages,
                    "rule": "no score, name order, slate order, hash, or reputation tie-break",
                },
            )
        )
        return [], logs

    winner["core_lane"] = (
        "TRANSFER"
        if winner.get("transfer_flag") is True
        else "CHAOS"
        if decision == "WHO_CHAOS"
        else "PRIMARY"
    )
    winner["last_man_decision"] = decision
    winner["ownership_score"] = 0
    winner["event_reason"] = (
        f"True Blender last man: {winner.get('conversion_label')} / {decision}"
    )

    logs.append(
        gate_log(
            16,
            "Last-Man Elimination",
            last_before,
            1,
            [
                {"player": _name(player), "reason": f"lost {decision} last-man decision"}
                for player in current
                if player is not winner
            ],
            note={
                "decision": decision,
                "winner": _name(winner),
                "conversion_label": winner.get("conversion_label"),
                "core_lane": winner.get("core_lane"),
                "stages": stages,
                "rule": "discrete elimination only; no blended ranking or fabricated Universe score",
            },
        )
    )
    return [winner], logs
