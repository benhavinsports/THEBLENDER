from __future__ import annotations

REQUIRED_SEQUENCE = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10.5, 11, 12, 13, 14, 15, 16]


def _as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def run_audit(survivors: list[dict], logs: list[dict]) -> dict:
    """Verify that one complete True Blender path actually ran.

    Zero survivors is a valid WHO result when the gates genuinely eliminate the
    pool or Gate 16 cannot break a true tie. Missing required data, null Universe
    signals, fake archetype assignment, broken continuity, or arbitrary Gate-16
    selection blocks Gate 18.
    """
    issues: list[dict] = []
    warnings: list[dict] = []
    participating_gates: list[float] = []

    gates = [item.get("gate") for item in logs]
    if gates != REQUIRED_SEQUENCE:
        issues.append({"code": "GATE_SEQUENCE_INVALID", "expected": REQUIRED_SEQUENCE, "actual": gates})

    by_gate = {item.get("gate"): item for item in logs}
    previous_after = None
    first_empty_gate = None
    first_singleton_gate = None
    effective_gates: list[float] = []

    for index, item in enumerate(logs):
        gate = item.get("gate")
        before = _as_int(item.get("before"))
        after = _as_int(item.get("after"))
        removed = item.get("removed") or []

        if before < 0 or after < 0 or after > before:
            issues.append({"code": "INVALID_GATE_COUNTS", "gate": gate, "before": before, "after": after})
        if index > 0 and previous_after is not None and before != previous_after:
            issues.append(
                {
                    "code": "GATE_CONTINUITY_BROKEN",
                    "gate": gate,
                    "expected_before": previous_after,
                    "actual_before": before,
                }
            )
        if before - after != len(removed):
            issues.append(
                {
                    "code": "REMOVAL_COUNT_MISMATCH",
                    "gate": gate,
                    "before": before,
                    "after": after,
                    "removed_count": len(removed),
                }
            )

        if before != after:
            effective_gates.append(gate)
            participating_gates.append(gate)
        if before > 0 and after == 0 and first_empty_gate is None:
            first_empty_gate = gate
        if before > 1 and after == 1 and first_singleton_gate is None:
            first_singleton_gate = gate

        for removal in removed:
            reason = str(removal.get("reason") or "").lower()
            if any(token in reason for token in ("missing", "unavailable", "invalid source")):
                warnings.append(
                    {
                        "code": "CANDIDATE_DATA_MISSING",
                        "gate": gate,
                        "player": removal.get("player"),
                        "reason": removal.get("reason"),
                    }
                )
        previous_after = after

    # Shared game context.
    for gate in (0, 1, 2):
        item = by_gate.get(gate) or {}
        note = _dict(item.get("note"))
        if item.get("before", 0) > 0 and note.get("data_status") != "ACTIVE":
            issues.append({"code": "REQUIRED_CONTEXT_UNAVAILABLE", "gate": gate, "name": item.get("name")})

    side_lock = by_gate.get(3) or {}
    if side_lock.get("name") != "Side Lock":
        issues.append({"code": "SIDE_LOCK_GATE_INVALID", "gate": 3})

    # Gate 5 must use Pull% percentile, while direct Pull AIR remains a separate lane.
    pull_gate = by_gate.get(5) or {}
    pull_note = _dict(pull_gate.get("note"))
    if pull_gate.get("name") != "Pull-Air Profile":
        issues.append({"code": "PULL_AIR_GATE_INVALID", "gate": 5})
    if pull_gate.get("before", 0) > 0:
        if pull_note.get("required_source") != "SAVANT_PULL_PERCENTILE":
            issues.append({"code": "PULL_IDENTITY_SOURCE_INVALID", "gate": 5})
        if "DIRECT_PULL_AIR" not in str(pull_note.get("raw_source") or ""):
            issues.append({"code": "PULL_AIR_RAW_SOURCE_INVALID", "gate": 5})
        complete = _as_int(pull_note.get("complete_profiles"))
        if complete == 0:
            issues.append({"code": "PULL_AIR_DATA_UNAVAILABLE", "gate": 5})
        elif complete < _as_int(pull_gate.get("before")):
            warnings.append(
                {
                    "code": "PARTIAL_PULL_AIR_COVERAGE",
                    "gate": 5,
                    "complete": complete,
                    "required": pull_gate.get("before"),
                }
            )

    # Data-backed elimination gates.
    for gate in (6, 7, 8, 9):
        item = by_gate.get(gate) or {}
        note = _dict(item.get("note"))
        if item.get("before", 0) > 0 and note.get("data_status") != "ACTIVE":
            issues.append({"code": "GATE_DATA_UNAVAILABLE", "gate": gate, "name": item.get("name")})

    for gate, expected_name in ((7, "Pitch-Type Matchup"), (8, "Zone Match")):
        item = by_gate.get(gate) or {}
        note = _dict(item.get("note"))
        if item.get("name") != expected_name:
            issues.append({"code": "MATCHUP_GATE_INVALID", "gate": gate, "expected": expected_name})
        if note.get("required_source") != "STATCAST_DETAIL_PITCH_ZONE":
            issues.append({"code": "MATCHUP_SOURCE_INVALID", "gate": gate})

    # Gate 10 must audit conversion bands and must not assign the final archetype.
    gate10 = by_gate.get(10) or {}
    gate10_note = _dict(gate10.get("note"))
    profiles = gate10_note.get("profiles") or []
    if gate10.get("before", 0) > 0 and not profiles:
        issues.append({"code": "FINISHER_IDENTITY_NOT_AUDITED", "gate": 10})
    for profile in profiles:
        label = str(profile.get("label") or "")
        if any(token in label for token in ("PRIMARY", "ADJACENT", "WHO", "CHAOS", "CORE_FINISHER")):
            issues.append({"code": "PREASSIGNED_ARCHETYPE", "gate": 10, "player": profile.get("player"), "label": label})
    if profiles:
        participating_gates.append(10)

    # Gate 10.5 must make a real audited decision while the pool is live.
    transfer = by_gate.get(10.5) or {}
    transfer_note = _dict(transfer.get("note"))
    if "executed" not in transfer_note or not transfer_note.get("reason") and not transfer_note.get("executed"):
        issues.append({"code": "TRANSFER_DECISION_NOT_AUDITED", "gate": 10.5})
    if transfer_note.get("executed") and transfer.get("before", 0) < 2:
        issues.append({"code": "TRANSFER_EXECUTED_WITHOUT_LIVE_POOL", "gate": 10.5})
    if "executed" in transfer_note:
        participating_gates.append(10.5)

    # Recent form and protection may legitimately produce NO_SEPARATION, but they
    # must disclose loaded data and the decision mode.
    for gate in (11, 12):
        item = by_gate.get(gate) or {}
        note = _dict(item.get("note"))
        if item.get("before", 0) > 0 and note.get("data_status") != "ACTIVE":
            issues.append({"code": "GATE_DATA_UNAVAILABLE", "gate": gate, "name": item.get("name")})
        if item.get("before", 0) > 1 and item.get("before") == item.get("after"):
            if note.get("mode") != "NO_SEPARATION":
                issues.append({"code": "SUPPORT_GATE_DECISION_MISSING", "gate": gate})
        if note.get("data_status") == "ACTIVE":
            participating_gates.append(gate)

    protection_note = _dict((by_gate.get(12) or {}).get("note"))
    if protection_note.get("pass_rule") != "protection > 0":
        issues.append({"code": "PROTECTION_RULE_INVALID", "gate": 12})

    # Gate 13 must populate an explicit boolean signal and evidence for every live hitter.
    universe = by_gate.get(13) or {}
    universe_note = _dict(universe.get("note"))
    universe_signals = universe_note.get("signals") or []
    if universe.get("before") != universe.get("after") or universe.get("removed"):
        issues.append({"code": "UNIVERSE_MUST_NOT_ELIMINATE", "gate": 13})
    if universe_note.get("mode") != "TIE_BREAK_ONLY" or universe_note.get("source") != "GATE_CONVERGENCE":
        issues.append({"code": "UNIVERSE_MODE_INVALID", "gate": 13})
    if len(universe_signals) != _as_int(universe.get("before")):
        issues.append(
            {
                "code": "UNIVERSE_SIGNAL_COUNT_INVALID",
                "gate": 13,
                "signals": len(universe_signals),
                "live": universe.get("before"),
            }
        )
    for signal in universe_signals:
        if not isinstance(signal.get("active"), bool) or not isinstance(signal.get("lanes"), dict):
            issues.append({"code": "UNIVERSE_SIGNAL_NULL", "gate": 13, "player": signal.get("player")})
    if universe_signals or universe.get("before") == 0:
        participating_gates.append(13)

    # Gate 14 must evaluate chaos naturally; it cannot silently assign CHAOS from Gate 10.
    chaos = by_gate.get(14) or {}
    chaos_note = _dict(chaos.get("note"))
    chaos_profiles = chaos_note.get("profiles") or []
    if len(chaos_profiles) != _as_int(chaos.get("before")):
        issues.append({"code": "CHAOS_PROFILE_COUNT_INVALID", "gate": 14})
    if any(not isinstance(profile.get("qualifies"), bool) for profile in chaos_profiles):
        issues.append({"code": "CHAOS_DECISION_NULL", "gate": 14})
    qualifying = [profile for profile in chaos_profiles if profile.get("qualifies")]
    if chaos_note.get("trigger") == "UNIQUE_WHO_CHAOS" and len(qualifying) != 1:
        issues.append({"code": "CHAOS_TRIGGER_NOT_UNIQUE", "gate": 14})
    if chaos_profiles or chaos.get("before") == 0:
        participating_gates.append(14)

    # Gate 16 must finish with one owner or a transparent WHO tie.
    last_man = by_gate.get(16) or {}
    gate16_note = _dict(last_man.get("note"))
    allowed_decisions = {
        "NO_LIVE_HITTER",
        "SOLE_LIVE_HITTER",
        "ADJACENT_TRANSFER",
        "FINISHER_DOMINANCE",
        "MATCHUP_DOMINANCE",
        "RECENT_PLUS_PROTECTION",
        "UNIVERSE_CONVERGENCE",
        "WHO_CHAOS",
        "WHO_UNRESOLVED_TRUE_TIE",
    }
    if gate16_note.get("decision") not in allowed_decisions:
        issues.append({"code": "GATE16_DECISION_INVALID", "decision": gate16_note.get("decision")})
    if last_man.get("after") not in (0, 1):
        issues.append({"code": "GATE16_CARDINALITY_INVALID", "after": last_man.get("after")})
    if len(survivors) not in (0, 1):
        issues.append({"code": "SURVIVOR_COUNT_INVALID", "count": len(survivors)})
    if len(survivors) != _as_int(last_man.get("after")):
        issues.append({"code": "SURVIVOR_LOG_MISMATCH", "survivors": len(survivors), "gate16_after": last_man.get("after")})
    if any(key in gate16_note for key in ("score", "scores", "ranking", "universe_score")):
        issues.append({"code": "GATE16_RANKING_PRESENT", "gate": 16})
    if gate16_note.get("decision") in allowed_decisions:
        participating_gates.append(16)

    # Surface suspicious early collapses instead of pretending the later gates selected the name.
    if first_singleton_gate is not None and first_singleton_gate <= 8:
        warnings.append({"code": "EARLY_SINGLETON", "gate": first_singleton_gate})
    if pull_gate.get("before", 0) >= 7 and pull_gate.get("after", 0) <= 1:
        warnings.append(
            {
                "code": "PULL_AIR_POOL_COLLAPSE",
                "gate": 5,
                "before": pull_gate.get("before"),
                "after": pull_gate.get("after"),
            }
        )

    outcome = "DATA_ERROR" if issues else "LOCKED" if len(survivors) == 1 else "WHO"
    return {
        "passed": not issues,
        "lockable": not issues and len(survivors) == 1,
        "outcome": outcome,
        "issues": issues,
        "warnings": warnings,
        "executed_gates": gates,
        "effective_gates": effective_gates,
        "participating_gates": sorted(set(participating_gates), key=float),
        "first_empty_gate": first_empty_gate,
        "first_singleton_gate": first_singleton_gate,
        "survivor_count": len(survivors),
    }
