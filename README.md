# MLB HR Blender V5.2

Run locally:

```bash
pip install -r requirements.txt
streamlit run app.py
```

V5.2 repairs Baseball Savant ingestion by using the actual `custom.csv`
download route, adds name fallback matching when Savant omits player IDs, adds
multiple bat-tracking CSV parameter fallbacks, exposes pipeline health in debug
mode, and makes Gate 18's audit count reflect the final one-player lock.

## Historical Replay / Season Backfill

This branch contains a hindsight-safe replay path for season calibration.

### Locked replay rule
For slate date `D`, all performance/form/Statcast windows end at `D-1`.
The replay uses the archived official MLB starting nine and excludes post-start
substitutions. It never uses current RotoWire/current-roster fallbacks in replay
mode. Full-season hitter, pitcher, bullpen, batted-ball and arsenal leaderboards
are blocked from a frozen historical card.

### Freeze first, grade second

```bash
python tools/replay_backfill.py --start 2026-04-27 --end 2026-08-06
python tools/grade_replay.py replay_output/2026-04-27/card.json
python tools/summarize_replay.py
```

`replay_backfill.py` freezes a signed pre-result card containing the full game
board and Core 3 / Alt 3 / Chaos 3. `grade_replay.py` refuses to fetch results
if that signature has changed. Core 3 grading is strict: 3/3 = WIN; 0/3, 1/3,
or 2/3 = LOSS. An incomplete Core 3 is recorded as NO_TICKET rather than
inventing a selection.

### Replay calibration layers
- Gate 16: original last-man elimination is preserved and audited.
- Gate 19: marked baseline-not-present during v2.1 replay; no retroactive rule is
  inserted into historical picks.
- Gate 21: replay signature/provenance validation.
- Gate 22: post-slate leakage reconstruction, including the exact removal gate
  for an eventual HR hitter when recoverable from the audit trail.
- Gate 23: dual-label outcome calibration and strict Core 3 ticket grading.

### Current execution environment
The code is fully testable offline with mocks, but this container cannot resolve
the MLB/Savant hosts. A failed network lookup writes `replay_error.json`; it
never freezes a fake card.
