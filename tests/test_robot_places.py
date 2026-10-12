"""The places table parser, leg validation and the place matcher (design §2f).

The route file lives in gitignored data/robot/, so the step list of
`floor2_bigroom_roundtrip.json` is copied here (read 8 Oct): 10 = checkpoint
"inside the big room", 11 = door D11 out, 20 = dock.
"""
from __future__ import annotations

import copy
import json

import pytest

from app import robot_places as rp

ROUTE_DO = ["checkpoint", "drive", "drive", "door", "checkpoint", "drive", "drive",
            "checkpoint", "door", "checkpoint", "door", "checkpoint", "drive", "drive",
            "checkpoint", "door", "drive", "checkpoint", "drive", "dock"]
STEPS = [{"do": d} for d in ROUTE_DO]

VALID = {
    "format": 1,
    "route": "data/robot/routes/floor2_bigroom_roundtrip.json",
    "route_sha256": "0" * 64,
    "places": [
        {"id": "dock", "name_th": "แท่นชาร์จ", "name_en": "charging dock",
         "aliases_th": ["ที่ชาร์จ", "แท่น", "จุดจอด"], "aliases_en": ["dock", "charger", "base"],
         "kind": "dock"},
        {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "name_en": "big meeting room",
         "aliases_th": ["ห้องประชุม"], "aliases_en": ["meeting room"], "kind": "room",
         "arrive_tolerance_m": 0.25, "arrive_tolerance_deg": 15},
    ],
    "legs": [
        {"from": "dock", "to": "big_room", "from_step": 1, "to_step": 10, "min_battery": 30,
         "verified": False, "est_s": None, "doors": ["D8", "D11"], "note": "steps 1-10"},
        {"from": "big_room", "to": "dock", "from_step": 10, "to_step": 20, "min_battery": 10,
         "verified": False, "est_s": None, "doors": ["D11", "D8"], "note": "steps 10-20"},
    ],
}


def _data(**changes):
    data = copy.deepcopy(VALID)
    data.update(changes)
    return data


def test_valid_file_loads(tmp_path):
    path = tmp_path / "floor2.json"
    path.write_text(json.dumps(VALID, ensure_ascii=False), encoding="utf-8")
    places = rp.load(path)
    assert [p.id for p in places.places] == ["dock", "big_room"]
    assert places.place("big_room").arrive_tolerance_m == 0.25
    assert places.place("dock").arrive_tolerance_deg == rp.DEFAULT_ARRIVE_TOLERANCE_DEG
    assert rp.leg(places, "dock", "big_room").to_step == 10
    assert rp.leg(places, "big_room", "dock").from_step == 10
    assert rp.leg(places, "dock", "dock") is None
    assert rp.leg(places, "big_room", "dock").est_s is None
    rp.validate(places, STEPS)


def _mutate(path, value):
    data = copy.deepcopy(VALID)
    target = data
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return data


@pytest.mark.parametrize("path,value,named", [
    (("format",), 2, "format"),
    (("format",), True, "format"),
    (("route",), "", "route"),
    (("route_sha256",), "abc", "route_sha256"),
    (("route_sha256",), "A" * 64, "route_sha256"),
    (("places",), [], "places"),
    (("places", 0, "id"), "Dock", "places[0].id"),
    (("places", 0, "id"), "big-room", "places[0].id"),
    (("places", 1, "id"), "dock", "places[1].id"),
    (("places", 0, "name_th"), "", "places[0].name_th"),
    (("places", 0, "name_en"), KeyError, "places[0].name_en"),
    (("places", 0, "aliases_th"), "ที่ชาร์จ", "places[0].aliases_th"),
    (("places", 0, "kind"), "Dock Kind", "places[0].kind"),
    (("places", 1, "arrive_tolerance_m"), -1, "places[1].arrive_tolerance_m"),
    (("places", 1, "arrive_tolerance_deg"), True, "places[1].arrive_tolerance_deg"),
    (("legs", 0, "from"), "kitchen", "legs[0].from"),
    (("legs", 0, "to"), "dock", "legs[0]"),
    (("legs", 0, "from_step"), 0, "legs[0].from_step"),
    (("legs", 0, "to_step"), True, "legs[0].to_step"),
    (("legs", 0, "from_step"), 11, "legs[0]"),
    (("legs", 0, "min_battery"), 101, "legs[0].min_battery"),
    (("legs", 0, "verified"), "no", "legs[0].verified"),
    (("legs", 0, "est_s"), 0, "legs[0].est_s"),
    (("legs", 0, "doors"), ["D8", ""], "legs[0].doors"),
])
def test_each_invalid_field_is_named(path, value, named):
    with pytest.raises(ValueError) as err:
        rp.parse(_mutate(path, value))
    assert str(err.value).startswith(named)


def test_duplicate_leg_is_rejected():
    data = _data()
    data["legs"].append(dict(data["legs"][0]))
    with pytest.raises(ValueError, match="duplicate leg"):
        rp.parse(data)


def test_bad_json_is_a_value_error(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="places file"):
        rp.load(path)


def test_a_leg_ending_on_a_drive_step_is_rejected():
    data = _data()
    data["legs"] = [dict(data["legs"][0], to_step=8)]       # step 8 is a checkpoint
    rp.validate(rp.parse(data), STEPS)
    data["legs"] = [dict(data["legs"][0], to_step=7)]       # step 7 is a drive
    with pytest.raises(ValueError, match=r"legs\[0\]\.to_step"):
        rp.validate(rp.parse(data), STEPS)


def test_a_leg_past_the_route_is_rejected():
    data = _data()
    data["legs"] = [dict(data["legs"][1], to_step=21)]
    with pytest.raises(ValueError, match="past the route"):
        rp.validate(rp.parse(data), STEPS)


def test_overlapping_legs_are_rejected():
    data = _data()
    data["legs"][1] = dict(data["legs"][1], from_step=9)      # shares 9..10
    with pytest.raises(ValueError, match="overlap"):
        rp.validate(rp.parse(data), STEPS)


def test_shared_boundary_must_be_a_checkpoint_ending_one_and_starting_the_other():
    """Leg 2 re-runs step 10 — a checkpoint — so its D11 pass starts from a
    measured pose (build review #2). Sharing a door step is not allowed, and
    neither is a shared step that is not the boundary of both."""
    rp.validate(rp.parse(VALID), STEPS)
    steps = copy.deepcopy(STEPS)
    steps[9] = {"do": "dock"}          # same shape, but step 10 is no checkpoint
    with pytest.raises(ValueError, match="overlap"):
        rp.validate(rp.parse(VALID), steps)
    data = _data()                     # both legs END on step 10
    data["legs"][1] = {"from": "big_room", "to": "dock", "from_step": 5, "to_step": 10,
                       "min_battery": 10, "verified": False}
    with pytest.raises(ValueError, match="overlap"):
        rp.validate(rp.parse(data), STEPS)


def test_sha256_file(tmp_path):
    import hashlib

    path = tmp_path / "route.json"
    path.write_bytes(b'{"steps": []}')
    assert rp.sha256_file(path) == hashlib.sha256(b'{"steps": []}').hexdigest()


# ---- match: whole words, the find_place rule ----


def _places(extra=()):
    data = _data()
    data["places"] = list(data["places"]) + list(extra)
    data["legs"] = []
    return rp.parse(data)


def test_match_names_and_aliases():
    places = _places()
    assert rp.match("พาไปห้องประชุมใหญ่", places).id == "big_room"
    assert rp.match("กลับแท่นชาร์จ", places).id == "dock"
    assert rp.match("take me to the meeting room", places).id == "big_room"
    assert rp.match("go back to the charger", places).id == "dock"
    assert rp.match("ไปไหนมา", places) is None
    assert rp.match("", places) is None


def test_longest_name_wins_and_a_partial_name_does_not_match():
    small = {"id": "small_room", "name_th": "ห้องประชุม", "name_en": "meeting room",
             "kind": "room"}
    big = {"id": "big_room", "name_th": "ห้องประชุมใหญ่", "name_en": "big meeting room",
           "kind": "room"}
    data = _data()
    data["places"] = [data["places"][0], big, small]
    data["legs"] = []
    places = rp.parse(data)
    assert rp.match("ไปห้องประชุมใหญ่", places).id == "big_room"
    assert rp.match("ไปห้องประชุม", places).id == "small_room"
    assert rp.match("ไปห้องประชุมเล็ก", places).id == "small_room"


def test_ambiguous_request_is_none():
    a = {"id": "room_a", "name_th": "ห้องเอ", "name_en": "pantry", "kind": "room"}
    b = {"id": "room_b", "name_th": "ห้องบี", "name_en": "lounge", "kind": "room"}
    places = _places([a, b])
    assert rp.match("pantry or lounge", places) is None
    assert rp.match("pantry", places).id == "room_a"


def test_price_does_not_match_building():
    """ราคา and อาคาร share าคา — the bug that rebuilt the search layer."""
    building = {"id": "building", "name_th": "อาคาร", "name_en": "tower block",
                "kind": "room"}
    places = _places([building])
    assert rp.match("ราคาเท่าไหร่", places) is None
    assert rp.match("ไปอาคาร", places).id == "building"


def test_without_the_segmenter_thai_names_never_match(monkeypatch):
    """Character n-grams are how ราคา matched อาคาร. With no word segmenter a
    Thai name must not match at all — a question costs one sentence."""
    from app.tools import retrieval

    monkeypatch.setattr(retrieval, "_tokeniser", None)
    monkeypatch.setattr(retrieval, "_tokeniser_ready", True)
    places = _places()
    assert rp.match("ห้องประชุมใหญ่", places) is None
    assert rp.match("meeting room", places).id == "big_room"


# ---- review #18: the tracked places file itself ----

def _real_places():
    from pathlib import Path

    return rp.load(Path(__file__).resolve().parent.parent / "data" / "robot-places" / "floor2.json")


@pytest.mark.parametrize("request_text", [
    "พาไปห้องประชุมเล็ก", "take me to the small meeting room", "ไปห้องประชุม",
])
def test_the_real_file_does_not_send_another_meeting_room_to_big_room(request_text):
    """The bare aliases ห้องประชุม / "meeting room" made every meeting-room
    request — including the small one — walk to big_room. Without them a
    request that does not name the big room is no match: one question
    instead of a guest walked to the wrong room."""
    assert rp.match(request_text, _real_places()) is None


@pytest.mark.parametrize("request_text", ["พาไปห้องประชุมใหญ่", "take me to the big meeting room"])
def test_the_real_file_still_names_the_big_room(request_text):
    assert rp.match(request_text, _real_places()).id == "big_room"
