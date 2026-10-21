#!/usr/bin/env python3
"""Тесты инструментов разбора демок. Только стандартная библиотека.

Запуск из корня проекта:  python -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import decode as decode_mod  # noqa: E402
import compare as compare_mod  # noqa: E402
import duel as duel_mod  # noqa: E402


def run_cli(script, *argv):
    r = subprocess.run([sys.executable, os.path.join(ROOT, script), *argv],
                       capture_output=True, text=True, cwd=ROOT)
    return r.stdout + r.stderr


def make_decoded(tmp, players=None, timeline=None, events=None):
    d = os.path.join(tmp, "fake_decoded")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "players.json"), "w", encoding="utf-8") as f:
        json.dump(players or [], f, ensure_ascii=False)
    with open(os.path.join(d, "timeline.jsonl"), "w", encoding="utf-8") as f:
        for e in timeline or []:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8") as f:
        for e in events or []:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return d


SF_TIMELINE = [{"tick": 1800, "game_time_s": 60.0,
                "heroes": [{"player_id": 1, "hero": "Nevermore",
                            "hero_internal": "npc_dota_hero_shadow_fiend"}]}]
SF_PLAYERS = [{"player_id": 1, "name": "Bot", "hero": "Nevermore",
               "team": "Dire", "team_num": 3, "is_bot": True}]


class TestDecodeHelpers(unittest.TestCase):
    def test_canon_aliases(self):
        self.assertEqual(decode_mod.canon_cl_name("npc_dota_hero_nevermore"),
                         "npc_dota_hero_shadow_fiend")
        self.assertEqual(decode_mod.canon_cl_name("npc_dota_hero_wisp"),
                         "npc_dota_hero_io")
        self.assertEqual(decode_mod.canon_cl_name("npc_dota_hero_sven"),
                         "npc_dota_hero_sven")
        self.assertIsNone(decode_mod.canon_cl_name(None))

    def test_summarize_waves(self):
        npcs = [
            {"class": "CDOTA_BaseNPC_Creep_Lane", "alive": True, "team_num": 2,
             "x": 0, "y": 0, "hp": 550, "hp_max": 550},
            {"class": "CDOTA_BaseNPC_Creep_Lane", "alive": True, "team_num": 2,
             "x": 300, "y": 300, "hp": 150, "hp_max": 300},
            {"class": "CDOTA_BaseNPC_Creep_Siege", "alive": True, "team_num": 2,
             "x": 600, "y": 600, "hp": 900, "hp_max": 900},
            {"class": "CDOTA_BaseNPC_Creep_Lane", "alive": False, "team_num": 2,
             "x": 0, "y": 0, "hp": 0, "hp_max": 550},
            {"class": "CDOTA_BaseNPC_Tower", "alive": True, "team_num": 2,
             "x": 0, "y": 0, "hp": 2500, "hp_max": 2500},
        ]
        w = decode_mod.summarize_waves(npcs)
        mid = w["2"]["мид"]
        self.assertEqual(mid["n"], 3)
        self.assertEqual(mid["melee"], 1)
        self.assertEqual(mid["ranged"], 1)
        self.assertEqual(mid["siege"], 1)
        self.assertEqual(mid["cx"], 300)

    def test_lane_of_pos(self):
        self.assertEqual(decode_mod.lane_of_pos(0, 0), "мид")
        self.assertEqual(decode_mod.lane_of_pos(-6000, 6000), "топ")
        self.assertEqual(decode_mod.lane_of_pos(6000, -6000), "бот")


class TestFindDeathsFallback(unittest.TestCase):
    def test_death_found_without_deaths_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = make_decoded(tmp, players=SF_PLAYERS, timeline=SF_TIMELINE, events=[
                {"tick": 5000, "clock": "01:01", "kind": "death", "source": "combatlog",
                 "attacker": "npc_dota_hero_shadow_fiend",
                 "target": "npc_dota_hero_shadow_fiend", "text": "x"}])
            out = run_cli("find.py", d, "deaths")
            self.assertIn("Смерть Nevermore", out)
            self.assertIn("найдено: 1", out)


class TestFindTowers(unittest.TestCase):
    def test_t4_pair_not_collapsed(self):
        ev = []
        for i, sfx in enumerate(("T4-1", "T4-2")):
            ev.append({"tick": 10000 + 30 * i, "clock": "05:00", "kind": "building_death",
                       "source": "snapshot", "npc_class": "CDOTA_BaseNPC_Tower",
                       "team": "Radiant",
                       "text": f"Объект разрушен: Radiant {sfx} (у трона) (Radiant), район: мид"})
        ev.append({"tick": 10010, "clock": "05:00", "kind": "building_death",
                   "source": "combatlog", "target": "npc_dota_goodguys_tower4_top",
                   "text": "Здание разрушено: npc_dota_goodguys_tower4_top"})
        with tempfile.TemporaryDirectory() as tmp:
            d = make_decoded(tmp, players=[], timeline=SF_TIMELINE, events=ev)
            out = run_cli("find.py", d, "towers")
            self.assertIn("T4-1", out)
            self.assertIn("T4-2", out)
            self.assertIn("найдено: 2", out)

    def test_combatlog_only_tower_found(self):
        ev = [{"tick": 9000, "clock": "04:30", "kind": "building_death",
               "source": "combatlog", "target": "npc_dota_badguys_tower1_mid",
               "text": "Здание разрушено: npc_dota_badguys_tower1_mid"}]
        with tempfile.TemporaryDirectory() as tmp:
            d = make_decoded(tmp, players=[], timeline=SF_TIMELINE, events=ev)
            out = run_cli("find.py", d, "towers")
            self.assertIn("Dire T1 мид", out)
            self.assertIn("найдено: 1", out)


class TestCompareAtMinute(unittest.TestCase):
    def test_short_game_reports_real_minute(self):
        d = {"minutes": [{"minute": 3, "p0": {"last_hits": 10}},
                         {"minute": 5, "p0": {"last_hits": 20}}]}
        val, used = compare_mod.at_minute(d, 10, 0, "last_hits")
        self.assertEqual(val, 20)
        self.assertEqual(used, 5)


class TestPickPair(unittest.TestCase):
    def test_human_vs_most_active_enemy(self):
        players = [
            {"player_id": 0, "name": "Human", "hero": "Nevermore", "team": "Radiant",
             "team_num": 2, "is_bot": False, "final_stats": {"last_hits": 48}},
            {"player_id": 1, "name": None, "hero": "Nevermore", "team": "Dire",
             "team_num": 3, "is_bot": True, "final_stats": {"last_hits": 13}},
            {"player_id": 2, "name": None, "hero": "Beastmaster", "team": "Dire",
             "team_num": 3, "is_bot": True, "final_stats": {"last_hits": 0}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            d = make_decoded(tmp, players=players)
            pair = duel_mod.pick_pair(d)
            self.assertEqual([p["player_id"] for p in pair], [0, 1])


if __name__ == "__main__":
    unittest.main()


class TestMirrorAttribution(unittest.TestCase):
    """Зеркальный SF vs SF: боевой лог различает игроков по команде."""

    def _hero(self, pid, cd):
        return {"player_id": pid, "hero_internal": "npc_dota_hero_shadow_fiend",
                "hero": "Nevermore", "hp_pct": 100, "x": 0, "y": 0, "alive": True,
                "level": 3, "stats": {},
                "abilities": [{"ability": "nevermore_shadowraze1", "level": 1,
                               "cooldown_left": cd, "in_ability_phase": False}]}

    def test_mirror_damage_souls_raze_by_player(self):
        players = [
            {"player_id": 0, "name": "Me", "hero": "Nevermore", "team": "Radiant",
             "team_num": 2, "is_bot": False, "final_stats": {"last_hits": 10}},
            {"player_id": 1, "name": None, "hero": "Nevermore", "team": "Dire",
             "team_num": 3, "is_bot": True, "final_stats": {"last_hits": 5}},
        ]
        timeline = []
        for gt, cd0 in ((0, 0), (30, 8), (60, 7)):
            timeline.append({"tick": 3000 + int(gt * 30), "game_time_s": float(gt),
                             "clock": "00:00", "heroes": [self._hero(0, cd0), self._hero(1, 0)],
                             "npcs": []})
        sf = "npc_dota_hero_shadow_fiend"
        combatlog = [
            {"_tick": 3930, "_type_name": "DOTA_COMBATLOG_DAMAGE", "_attacker": sf,
             "_target": sf, "_inflictor": "nevermore_shadowraze1", "value": 100,
             "attacker_team": 2, "target_team": 3,
             "is_attacker_hero": True, "is_target_hero": True},
            {"_tick": 4000, "_type_name": "DOTA_COMBATLOG_DAMAGE", "_attacker": sf,
             "_target": sf, "_inflictor": "dota_unknown", "value": 40,
             "attacker_team": 3, "target_team": 2,
             "is_attacker_hero": True, "is_target_hero": True},
            {"_tick": 3900, "_type_name": "DOTA_COMBATLOG_MODIFIER_STACK_EVENT",
             "_target": sf, "_inflictor": "modifier_nevermore_necromastery",
             "target_team": 2, "stack_count": 5},
            {"_tick": 4000, "_type_name": "DOTA_COMBATLOG_MODIFIER_STACK_EVENT",
             "_target": sf, "_inflictor": "modifier_nevermore_necromastery",
             "target_team": 3, "stack_count": 3},
        ]
        import duel as duel_mod_local
        with tempfile.TemporaryDirectory() as tmp:
            d = make_decoded(tmp, players=players, timeline=timeline)
            with open(os.path.join(d, "combatlog.jsonl"), "w", encoding="utf-8") as f:
                for e in combatlog:
                    f.write(json.dumps(e) + "\n")
            out = duel_mod_local.compute_duel(d)
            self.assertTrue(out["mirror_matchup"])
            self.assertEqual(out["combatlog_attribution"], "player_id")
            self.assertEqual(out["hero_damage_by_player"], {"0": 100, "1": 40})
            self.assertEqual(out["raze"]["0"]["hits"], 1)
            self.assertEqual(out["raze"]["0"]["accuracy_attributed_pct"], 100.0)
            m0 = out["minutes"][0]
            self.assertEqual(m0["p0"]["souls"], 5)
            self.assertEqual(m0["p1"]["souls"], 3)
