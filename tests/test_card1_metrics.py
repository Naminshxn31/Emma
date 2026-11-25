"""scripts/card1_metrics.py on small synthetic logs: run finding, the four clocks, the board-pose numbers."""
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import card1_metrics as cm  # noqa: E402

TH = dt.timezone(dt.timedelta(hours=7))


def _ep(hms):
    h, m, s = hms.split(":")
    return dt.datetime(2026, 11, 23, int(h), int(m), 0, tzinfo=TH).timestamp() + float(s)


def _turnlog(tmp_path, rows):
    p = tmp_path / "robot_turnlog_20261123.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    return p


CEREMONY = [
    {"t": "22:41:43", "event": "debut_start", "run": "debut-2", "wall_ms": int(_ep("22:41:43.6") * 1000),
     "impact_in_s": 7.0},
    {"t": "22:41:43", "event": "event_tour", "phase": "pressed", "scene": "debut", "owner": "ceremony"},
    {"t": "22:41:43", "event": "body", "action": "rest", "state": "started"},
    {"t": "22:41:45", "event": "body", "action": "rest", "state": "done"},
    {"t": "22:41:50", "event": "event_walk", "phase": "start", "spot": "forward", "run_id": "w1"},
    {"t": "22:41:51", "event": "event_tour", "phase": "late_walk_out", "late_s": 1.29},
    {"t": "22:41:55", "event": "head_look", "why": "walk", "pan": 0, "tilt": 0},
    {"t": "22:42:03", "event": "event_walk", "phase": "end", "spot": "forward", "run_id": "w1", "state": "arrived"},
    {"t": "22:42:05", "event": "event_box", "action": "out", "walked_m": 1.001, "of_m": 1.0},
    {"t": "22:42:05", "event": "event_pageant", "phase": "start", "ok": True},
    {"t": "22:42:09", "event": "metric_first_audio"},
    {"t": "22:42:14", "event": "event_cue", "id": "scene_debut", "mode": "recorded", "ok": True, "seconds": 7.12},
    {"t": "22:42:24", "event": "event_pageant", "phase": "end", "why": "line_done"},
    {"t": "22:42:29", "event": "body", "action": "pageant", "state": "done"},
    {"t": "22:42:30", "event": "body_ack", "action": "rest", "accepted": False, "reason": "cooldown"},
    {"t": "22:42:31", "event": "body_ack", "action": "rest", "accepted": False, "reason": "cooldown"},
    {"t": "22:42:32", "event": "body_ack", "action": "rest", "accepted": True},
    {"t": "22:42:33", "event": "event_walk", "phase": "start", "spot": "beside_host", "run_id": "w2"},
    {"t": "22:42:50", "event": "event_walk", "phase": "end", "spot": "beside_host", "run_id": "w2",
     "state": "arrived", "seconds": 17.0},
    {"t": "22:42:50", "event": "body", "action": "handshake", "state": "started"},
    {"t": "22:43:10", "event": "body", "action": "handshake", "state": "done"},
    {"t": "22:43:10", "event": "event_tour", "phase": "end", "why": "done_counter"},
    {"t": "22:43:10", "event": "head_look", "why": "recentre", "pan": 0, "tilt": 0},
]


def _board(tmp_path):
    """Still at (-7, -6.8) facing 180°, wheels from 22:41:52.5 at 0.12 m/s to 1.0 m, a 0.2 m jump on the
    counter leg, arrival at counter + (0.06, -0.08)."""
    lines = []
    t = _ep("22:41:40")
    yaw = math.pi
    x, y = -7.0, -6.8
    while t < _ep("22:43:15"):
        if _ep("22:41:52.5") <= t < _ep("22:42:00.83"):
            x = -7.0 - 0.12 * (t - _ep("22:41:52.5"))
            y = -6.8 - 0.03 * (t - _ep("22:41:52.5")) / 8.33    # 3 cm to the robot's left (facing -x)
        elif t >= _ep("22:42:00.83") and t < _ep("22:42:34"):
            x, y = -8.0, -6.83
        elif _ep("22:42:34") <= t < _ep("22:42:48"):
            f = (t - _ep("22:42:34")) / 14.0
            x, y = -8.0 + (-0.56 + 0.06 + 0.0) * f, -6.83 + (2.63 - 0.08) * f
            if t >= _ep("22:42:40"):
                y += 0.2                                   # the board jumps and stays
        elif t >= _ep("22:42:48"):
            x, y = -8.56, -4.28 + 0.2 - 0.2                  # counter (-8.62, -4.20) + (0.06, -0.08)
            yaw = 0.0
        lines.append("%.3f|86|%s" % (t, json.dumps({"x": x, "y": y, "yaw": yaw})))
        t += 0.3
    p = tmp_path / "boardlog.txt"
    p.write_text("\n".join(lines) + "\n1795448500|x|\n", encoding="utf-8")
    return p


def _state(tmp_path):
    """Staff-page polls: PC clock 5 s behind the robot; one press here that the turnlog never saw."""
    rows = []
    t0 = _ep("23:20:40")
    for k in range(0, 120):
        ts = t0 + k * 0.5
        rel = ts - _ep("23:20:43.5")
        s = {"server_time": ts, "box": {"left": None if rel < 0 else ("pending" if rel < 20 else "out"),
                                        "walked_m": 1.01 if rel >= 20 else 0.0},
             "tour": {"owner": "ceremony", "scene": "debut", "ended": None if rel < 45 else "staff",
                      "steps": [{"state": "now"}], "now_th": None},
             "walk": {"run": {"id": "a" if rel < 7 else "b", "spot": "forward",
                              "state": "walking" if 7 <= rel < 19 else ("arrived" if rel >= 19 else "stopped")}},
             "manual": {"pageant_up": 21 <= rel < 44}}
        if rel < -1:
            s["tour"]["ended"] = "staff"
        rows.append(json.dumps({"t": ts - 5.0, "s": s}))
    p = tmp_path / "scene_watch.jsonl"
    p.write_text("\n".join(rows) + "\n{broken\n", encoding="utf-8")
    return p


def test_turnlog_seconds_are_thai_and_midnight_rolls(tmp_path):
    p = _turnlog(tmp_path, [{"t": "23:59:59", "event": "a"}, {"t": "00:00:01", "event": "b"}])
    rows = cm.load_turnlog(p)
    assert rows[0]["_ts"] == _ep("23:59:59")
    assert rows[1]["_ts"] - rows[0]["_ts"] == 2.0         # the next day, not 24 h back


def test_ceremony_run_numbers(tmp_path):
    runs = cm.extract_runs(_turnlog(tmp_path, CEREMONY), boardlogs=[_board(tmp_path)])
    assert len(runs) == 1
    r = runs[0]
    assert r["kind"] == "ceremony" and r["ended_why"] == "done_counter"
    assert r["press_th"].startswith("22:41:43.6")          # wall_ms, not the truncated second
    assert r["hit_to_walker_s"] == 1.29
    # wheels started at 52.5 = 1.9 s after the hit (50.6); half a sample either way
    assert abs(r["hit_to_wheels_s"] - 1.9) <= 0.2
    assert r["walkout_box_m"] == 1.001
    assert abs(r["walkout_pose_m"] - 1.0) < 0.01
    assert abs(r["walkout_lateral_m"] - 0.03) < 0.005       # facing -x: -y is the robot's left
    assert r["rest_refusals"] == 2 and r["rest_wait_s"] == 2.0
    assert r["pageant_to_line_s"] == 4.0                    # two turnlog seconds: midpoints cancel
    assert r["line_s"] == 7.12
    assert r["handshake_s"] == 20.0
    assert r["head_cmds"] == 1 and r["head_whys"] == {"walk": 1}   # the recentre after the end is not counted
    assert r["pose_jumps"] == 1 and r["pose_jump_list"][0]["spot"] == "beside_host"
    assert abs(r["arrival_err_m"] - 0.1) < 0.005
    assert r["reveal"] == "played through"
    assert not cm.check_run(dict(r, pose_jumps=0))


def test_state_only_press_uses_server_time_not_pc(tmp_path):
    runs = cm.extract_runs(None, statelogs=[_state(tmp_path)])
    assert len(runs) == 1
    r = runs[0]
    assert r["sources"] == ["state"] and r["kind"] == "ceremony"
    assert abs(r["press_ts"] - _ep("23:20:43.5")) <= 0.3      # the PC's t is 5 s off: not used
    assert r["walkout_box_m"] == 1.01 and r["ended_why"] == "staff"
    assert abs(r["press_to_walker_s"] - 7.0) <= 0.3


def test_same_press_in_both_sources_is_one_run(tmp_path):
    rows = [dict(r, t=r["t"].replace("22:41", "23:20").replace("22:42", "23:21").replace("22:43", "23:22"))
            for r in CEREMONY if r["event"] != "debut_start"]
    runs = cm.extract_runs(_turnlog(tmp_path, rows), statelogs=[_state(tmp_path)])
    assert len(runs) == 1 and set(runs[0]["sources"]) >= {"turnlog", "state"}


def test_since_skips_older_presses(tmp_path):
    runs = cm.extract_runs(_turnlog(tmp_path, CEREMONY), since=_ep("22:50:00"))
    assert runs == []


def test_jump_rule_ignores_noise_that_snaps_back():
    seg = [(i * 0.3, 86, 0.04 * i, 0.0, 0.0) for i in range(10)]
    noisy = list(seg)
    noisy[5] = (1.5, 86, 0.2 + 0.15, 0.0, 0.0)               # out and back
    assert cm.find_jumps(noisy) == []
    stays = [(t, q, x + (0.2 if t >= 1.5 else 0.0), y, a) for t, q, x, y, a in seg]
    assert len(cm.find_jumps(stays)) == 1


def test_reports_are_written(tmp_path):
    runs = cm.extract_runs(_turnlog(tmp_path, CEREMONY), boardlogs=[_board(tmp_path)])
    cm.write_jsonl(runs, tmp_path / "o.jsonl")
    cm.write_markdown(runs, tmp_path / "o.md", hand=cm.HAND_20261123)
    cm.write_html(runs, tmp_path / "o.html", hand=cm.HAND_20261123)
    assert json.loads((tmp_path / "o.jsonl").read_text(encoding="utf-8").splitlines()[0])["kind"] == "ceremony"
    assert "prefers-color-scheme:dark" in (tmp_path / "o.html").read_text(encoding="utf-8")
