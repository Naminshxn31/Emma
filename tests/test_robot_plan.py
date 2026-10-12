"""The owner's showroom plan over the SLAM grid — /robot/plan endpoints.

Owner 2026-09-30: "อัพผังให้ง่ายกว่าใหม่". The image and its two-point
calibration live on the server so every LAN device sees the same plan. The
properties worth holding: token-gated, image types only, bounded size, a new
plan voids the old calibration (stale-but-plausible is worse than absent),
and calibration pairs are validated before anything is written.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.main import app

TOKEN = "let-me-in"

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
GOOD_PAIRS = [{"px": 10.0, "py": 20.0, "wx": 1.0, "wy": 2.0},
              {"px": 400.0, "py": 300.0, "wx": 5.0, "wy": -3.0}]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(main_module, "ROBOT_PLAN_DIR", tmp_path)
    return TestClient(app)


def _upload(client, body=PNG, ctype="image/png", token=TOKEN):
    return client.post(f"/robot/plan?token={token}", content=body,
                       headers={"Content-Type": ctype}).json()


def test_the_plan_is_token_gated_every_way(client):
    assert _upload(client, token="wrong")["error"] == "unauthorized"
    assert client.get("/robot/plan?token=wrong").status_code == 403
    assert client.get("/robot/plan/calibration?token=wrong").json()["error"] == "unauthorized"
    out = client.post("/robot/plan/calibration",
                      json={"token": "wrong", "pairs": GOOD_PAIRS}).json()
    assert out["error"] == "unauthorized"


def test_only_bounded_images_are_accepted(client):
    assert "PNG" in _upload(client, ctype="text/html")["error"] \
        or "JPEG" in _upload(client, ctype="text/html")["error"]
    assert _upload(client, body=b"")["ok"] is False
    big = b"0" * (main_module._ROBOT_PLAN_MAX_BYTES + 1)
    assert "8 MB" in _upload(client, body=big)["error"]
    assert _upload(client)["ok"] is True
    served = client.get(f"/robot/plan?token={TOKEN}")
    assert served.status_code == 200 and served.content == PNG


def test_a_new_plan_voids_the_old_calibration(client, tmp_path):
    assert _upload(client)["ok"] is True
    out = client.post("/robot/plan/calibration",
                      json={"token": TOKEN, "pairs": GOOD_PAIRS}).json()
    assert out["ok"] is True
    assert (tmp_path / "plan-calibration.json").is_file()
    # a JPEG replaces the PNG and the calibration goes with the old drawing
    assert _upload(client, body=b"jpegbytes", ctype="image/jpeg")["ok"] is True
    assert not (tmp_path / "showroom-plan.png").exists()
    assert (tmp_path / "showroom-plan.jpg").is_file()
    assert not (tmp_path / "plan-calibration.json").exists()
    assert client.get(f"/robot/plan/calibration?token={TOKEN}").json()["calibration"] is None


def test_calibration_pairs_are_validated_and_round_trip(client):
    no_plan = client.post("/robot/plan/calibration",
                          json={"token": TOKEN, "pairs": GOOD_PAIRS}).json()
    assert "ผัง" in no_plan["error"]
    assert _upload(client)["ok"] is True
    for bad, why in (
        ([GOOD_PAIRS[0]], "2 จุด"),
        (GOOD_PAIRS + GOOD_PAIRS, "2 จุด"),
        ([{"px": 1, "py": 1, "wx": 0, "wy": 0},
          {"px": 2, "py": 2, "wx": 3, "wy": 3}], "ผัง"),          # plan points ~1px apart
        ([{"px": 0, "py": 0, "wx": 0, "wy": 0},
          {"px": 400, "py": 0, "wx": 0.05, "wy": 0}], "ใกล้กัน"),  # world points 5cm apart
        ([{"px": 0, "py": 0, "wx": "x", "wy": 0}, GOOD_PAIRS[1]], "ตัวเลข"),
    ):
        out = client.post("/robot/plan/calibration",
                          json={"token": TOKEN, "pairs": bad}).json()
        assert out["ok"] is False, bad
        assert why in out["error"], (bad, out["error"])
    ok = client.post("/robot/plan/calibration",
                     json={"token": TOKEN, "pairs": GOOD_PAIRS}).json()
    assert ok["ok"] is True
    back = client.get(f"/robot/plan/calibration?token={TOKEN}").json()
    assert back["calibration"]["pairs"] == GOOD_PAIRS
