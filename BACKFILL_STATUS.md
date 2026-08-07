# 2026 Blender Season Backfill Status

Target window: April 2026 through August 6, 2026.

## Replay integrity work completed
- Previous-day (`D-1`) cutoff for every historical slate.
- Historical MLB starting-nine recovery that excludes substitutions.
- Hitter basic stats use MLB `byDateRange` instead of full-season stats.
- Pitcher stats use MLB `byDateRange` instead of full-season stats.
- Replay bullpen input uses a historical team-pitching proxy, explicitly labeled.
- Hitter Pull%, Pull AIR%, Pull Barrel%, Hard Hit%, Barrel%, Sweet Spot%, EV and
  AIR% are rebuilt from daily Statcast detail rows through the cutoff date.
- Pull identity remains an empirical percentile of raw Pull%, preserving Gate 5.
- Historical matchup data uses detail rows through the cutoff; full-season
  pitch-arsenal fallbacks are disabled.
- Every frozen card receives a SHA-256 signature before result grading.
- Grader refuses altered cards and grades Core 3 strictly: 3/3 = WIN.

## Offline QA
9 anti-hindsight / replay-integrity tests pass.

## First queued replay date
2026-04-27 is queued because a detailed archived pregame slate exists in the
library. In this runtime the remote MLB schedule host cannot resolve, so the
runner correctly wrote `replay_error.json` and did not freeze a fake Core 3.
