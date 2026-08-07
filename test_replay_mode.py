from __future__ import annotations

import unittest
from unittest.mock import patch

from services.replay import pregame_cutoff
import services.statcast as statcast
import services.pitchers as pitchers
import services.stats as stats
import services.bullpen as bullpen


class ReplayCutoffTests(unittest.TestCase):
    def test_previous_day_cutoff(self):
        self.assertEqual(pregame_cutoff("2026-04-27", fallback_year=2026), "2026-04-26")


class ReplayStatcastTests(unittest.TestCase):
    def setUp(self):
        statcast._REPLAY_BBE_STATES.clear()
        statcast._replay_day_batter_rows.cache_clear()

    def test_asof_profiles_use_only_rows_through_cutoff_and_rank_pull(self):
        # Coordinates chosen only to produce opposite pull classifications for
        # two RHBs. launch_speed_angle=6 is Savant's barrel category.
        rows_by_day = {
            "2026-03-01": (),
            "2026-03-02": (
                {"batter":"1", "player_name":"Pull Guy", "stand":"R", "type":"X", "bb_type":"fly_ball", "launch_speed":"101", "launch_angle":"28", "launch_speed_angle":"6", "hc_x":"80", "hc_y":"100"},
                {"batter":"2", "player_name":"Oppo Guy", "stand":"R", "type":"X", "bb_type":"ground_ball", "launch_speed":"88", "launch_angle":"-5", "launch_speed_angle":"2", "hc_x":"170", "hc_y":"100"},
            ),
        }
        def fake_rows(season, day):
            return rows_by_day.get(day, ())
        with patch.object(statcast, "_replay_day_batter_rows", side_effect=fake_rows):
            profiles = statcast.load_replay_hitter_profiles(2026, "2026-03-02")
        self.assertGreater(profiles[1]["pull"], profiles[2]["pull"])
        self.assertEqual(profiles[1]["hard_hit"], 100.0)
        self.assertEqual(profiles[1]["barrel"], 100.0)
        self.assertEqual(profiles[1]["pua"], 100.0)
        self.assertEqual(profiles[1]["statcast_profile_cutoff"], "2026-03-02")

    def test_replay_profile_never_calls_full_season_leaderboards(self):
        with patch.object(statcast, "load_replay_hitter_profiles", return_value={7:{"pull":77, "statcast_profile_source":"STATCAST_DETAIL_ASOF"}}), \
             patch.object(statcast, "load_statcast_hitter_maps", side_effect=AssertionError("future leak")), \
             patch.object(statcast, "load_batted_ball_maps", side_effect=AssertionError("future leak")), \
             patch.object(statcast, "load_bat_tracking_maps", side_effect=AssertionError("future leak")), \
             patch.object(statcast, "load_pitch_arsenal_maps", side_effect=AssertionError("future leak")):
            profile = statcast.get_hitter_statcast_profile(7, "Test", 2026, as_of="2026-04-26", replay_mode=True)
        self.assertEqual(profile["pull"], 77)


class ReplayBasicStatsTests(unittest.TestCase):
    def test_pitcher_replay_uses_date_range(self):
        captured = {}
        def fake_get(url, params=None, **kwargs):
            captured.update(params or {})
            return {"stats":[{"splits":[{"stat":{"inningsPitched":"20.0","homeRuns":3,"strikeOuts":20,"battersFaced":80,"era":"3.60"}}]}]}
        pitchers._pitching_as_of.cache_clear()
        with patch.object(pitchers, "get_json", side_effect=fake_get), patch.object(pitchers, "_pitcher_hand", return_value="R"):
            card = pitchers.build_pitcher_card({"id":99,"name":"P"}, 2026, as_of="2026-04-26", replay_mode=True)
        self.assertEqual(captured["stats"], "byDateRange")
        self.assertEqual(captured["endDate"], "2026-04-26")
        self.assertEqual(card["stats_source"], "MLB_BY_DATE_RANGE")

    def test_hitter_replay_does_not_call_season_stats(self):
        hitter = {"id":7,"name":"H","side":"away","slot":2}
        with patch.object(stats, "_player_bio", return_value={"handedness":"R"}), \
             patch.object(stats, "_mlb_hitting", side_effect=AssertionError("season hitter leak")), \
             patch.object(stats, "_mlb_hitting_as_of", return_value={"pa":50,"hr":4,"hr_pa":.08,"iso":.25,"slg":.5,"basic_stats_source":"MLB_BY_DATE_RANGE","basic_stats_cutoff":"2026-04-26"}), \
             patch.object(stats, "_recent_hitting", return_value={"recent_pa":20,"recent_hr":2,"recent_slg":.6,"recent_iso":.3,"hr_heat":True}), \
             patch.object(stats, "get_hitter_statcast_profile", return_value={"pull":70,"pull_percent":50,"pua":30,"pull_barrel":12,"hard_hit":50,"barrel":15,"ev":92,"sweet_spot":40,"fb":55,"statcast_profile_source":"STATCAST_DETAIL_ASOF","statcast_profile_cutoff":"2026-04-26"}):
            out = stats.attach_stats([hitter], 2026, "2026-04-26", replay_mode=True)
        self.assertEqual(out[0]["basic_stats_cutoff"], "2026-04-26")
        self.assertEqual(out[0]["statcast_profile_cutoff"], "2026-04-26")

    def test_bullpen_replay_uses_historical_proxy_not_full_season_relief(self):
        with patch.object(bullpen, "_relief_pitching", side_effect=AssertionError("future bullpen leak")), \
             patch.object(bullpen, "_pitching_as_of", return_value={"hr9":1.2,"era":4.0,"whip":1.3,"innings":50,"source":"MLB_TEAM_PITCHING_BY_DATE_RANGE","stats_cutoff":"2026-04-26"}):
            card = bullpen.build_bullpen_card({}, team_id=1, season=2026, team_name="X", as_of="2026-04-26", replay_mode=True)
        self.assertEqual(card["source"], "MLB_TEAM_PITCHING_BY_DATE_RANGE")
        self.assertEqual(card["stats_cutoff"], "2026-04-26")


if __name__ == "__main__":
    unittest.main()

class ReplayLineupTests(unittest.TestCase):
    def test_historical_starter_ids_exclude_substitutions(self):
        from services.lineups import _starter_player_ids
        team = {
            "players": {
                "ID1": {"person": {"id": 1}, "battingOrder": "100"},
                "ID2": {"person": {"id": 2}, "battingOrder": "200"},
                "ID3": {"person": {"id": 3}, "battingOrder": "300"},
                "ID30": {"person": {"id": 30}, "battingOrder": "301"},
                "ID4": {"person": {"id": 4}, "battingOrder": "400"},
                "ID5": {"person": {"id": 5}, "battingOrder": "500"},
                "ID6": {"person": {"id": 6}, "battingOrder": "600"},
                "ID7": {"person": {"id": 7}, "battingOrder": "700"},
                "ID8": {"person": {"id": 8}, "battingOrder": "800"},
                "ID9": {"person": {"id": 9}, "battingOrder": "900"},
            }
        }
        self.assertEqual(_starter_player_ids(team), [1,2,3,4,5,6,7,8,9])

class ReplayFreezeGradeTests(unittest.TestCase):
    def test_source_guard_rejects_future_source(self):
        from tools.replay_backfill import replay_source_guard
        result = {
            "game": "A vs B",
            "pipeline_health": {"replay_mode": True, "replay_cutoff": "2026-04-26", "input_signature": "abc", "lineup_source": "MLB"},
            "target_side": {"pitcher": {"stats_cutoff": "2026-04-26"}},
            "gate1_profiles": [{"name": "X", "basic_stats_cutoff": "2026-04-26", "statcast_profile_cutoff": "2026-04-26", "statcast_profile_source": "SAVANT_BATTED_BALL_LEADERBOARD"}],
        }
        guard = replay_source_guard([result], "2026-04-27")
        self.assertFalse(guard["passed"])
        self.assertTrue(any(i.get("code") == "FORBIDDEN_FUTURE_SOURCE" for i in guard["issues"]))

    def test_grade_refuses_tampered_card_before_results_fetch(self):
        import json, tempfile
        from pathlib import Path
        from unittest.mock import patch
        from tools.grade_replay import grade
        card = {
            "source_type": "BLENDER_REPLAY_CARD", "date": "2026-04-27",
            "pregame_cutoff": "2026-04-26", "slate": [], "results": [], "boards": {"core3": [], "alt3": [], "chaos3": []},
            "card_signature": "tampered",
        }
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"card.json"; p.write_text(json.dumps(card))
            with patch("tools.grade_replay.fetch_results") as fetch:
                with self.assertRaises(ValueError): grade(p)
                fetch.assert_not_called()
