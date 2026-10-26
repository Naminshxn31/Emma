"""One small story per template, with drawn pictures -- for the tests and the
dev harness (scripts/record_stories.py), never for a guest.

    from scripts.story_fixture import build
    stories_dir = build(tmp_path, approved=True)          # small pictures
    stories_dir = build(folder, size="screen")            # 1080-wide, for the harness

The pictures are drawn here (PIL), not cut from the owner's material, so the
suite runs on any machine and nothing of the project's media enters git. The
words and numbers are the approved ones (facts / report) so a fixture story
reads like a real one in the harness -- but the real stories are the
designers' (data/projects/<project>/stories/design/).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEMPLATE_STORY = {
    "stack": "fx_room_types", "slabs": "fx_facilities", "drill": "fx_ground",
    "spotlight": "fx_sky_pool", "assembly": "fx_tower", "details": "fx_studio",
    "distance": "fx_nearby", "metric": "fx_units_total", "features": "fx_smart_home",
    "journey": "fx_travel", "neighborhood": "fx_where",
}
#: A template's other drawing, chosen by a param, with a story of its own
#: (the server knows templates by name only -- a view is the page's).
VIEW_STORY = {("journey", "rings"): "fx_around"}


def _line(th, en, cue_th=(), cue_en=(), cue_zh=()):
    return {"th": th, "en": en, "cue_th": list(cue_th), "cue_en": list(cue_en), "cue_zh": list(cue_zh)}


def stories() -> list[dict]:
    return [
        {
            "id": "fx_room_types", "template": "stack",
            "questions_th": ["มีห้องแบบไหนบ้าง", "ห้องขนาดเท่าไหร่"], "questions_en": ["What room types are there?"],
            "lines": [
                _line("ที่นี่มีห้องให้เลือกสามแบบค่ะ", "We have three room types here.", ["สามแบบ"], ["three"]),
                _line("สตูดิโอ ขนาด 24 ถึง 29 ตารางเมตร", "Studio, 24 to 29 square metres.", ["สตูดิโอ"], ["studio"], ["开间"]),
                _line("หนึ่งห้องนอน 33 ถึง 35 ตารางเมตร", "One bedroom, 33 to 35 square metres.",
                      ["หนึ่งห้องนอน"], ["one bedroom", "1 bedroom"], ["一室"]),
                _line("และสองห้องนอน 51.5 ถึง 52.5 ตารางเมตรค่ะ", "And two bedrooms, 51.5 to 52.5 square metres.",
                      ["สองห้องนอน"], ["two bedroom", "2 bedroom"], ["两室"]),
                _line("แตะดูผังห้องจริงของแบบที่สนใจได้เลยค่ะ", "Tap a type to see a real room plan.", ["แตะ"], ["tap"]),
            ],
            "params": {
                "items": [
                    {"label_en": "STUDIO", "sub_en": "Example plan", "asset": "fx_plan_a", "asset_kind": "plan",
                     "value": {"from": 24, "to": 29, "unit_en": "sq.m."}, "line": 1},
                    {"label_en": "1 BEDROOM", "sub_en": "Example plan", "asset": "fx_plan_b", "asset_kind": "plan",
                     "value": {"from": 33, "to": 35, "unit_en": "sq.m."}, "line": 2},
                    {"label_en": "2 BEDROOMS", "sub_en": "Example plan", "asset": "fx_plan_c", "asset_kind": "plan",
                     "value": {"from": 51.5, "to": 52.5, "unit_en": "sq.m."}, "line": 3},
                ],
                "end_hint_en": "Tap a type to see a real room plan",
            },
            "taps": [
                {"id": "story_fx_room_types_studio", "label_en": "Studio", "label_th": "สตูดิโอ", "next_story": None,
                 "tool": {"name": "show_room_type", "args": {"type": "studio"}}},
                {"id": "story_fx_room_types_1br", "label_en": "1 Bedroom", "label_th": "1 ห้องนอน", "next_story": None,
                 "tool": {"name": "show_room_type", "args": {"type": "1br"}}},
                {"id": "story_fx_room_types_2br", "label_en": "2 Bedrooms", "label_th": "2 ห้องนอน", "next_story": None,
                 "tool": {"name": "show_room_type", "args": {"type": "2br"}}},
            ],
            "sources": ["facts:แบบห้องและขนาด"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_facilities", "template": "slabs",
            "questions_th": ["มีสิ่งอำนวยความสะดวกอะไรบ้าง"], "questions_en": ["What facilities are there?"],
            "lines": [
                _line("ในโครงการของเรามีสิ่งอำนวยความสะดวกถึง 3 ชั้นค่ะ", "We have facilities on 3 floors.",
                      ["สิ่งอำนวยความสะดวก"], ["facilities"]),
                _line("ชั้นใต้ดินเป็นสปาและสระในร่ม", "The basement has the spa and indoor pools.", ["ใต้ดิน"], ["basement"]),
                _line("ชั้น G เป็นฟิตเนสและห้องเด็ก", "The ground floor has the fitness and the kids' room.", ["ชั้น g", "ชั้นจี"],
                      ["ground"]),
                _line("และชั้น 3 อาคาร A มีเลานจ์กับ Sky Pool ค่ะ", "And level 3 of building A has lounges and the Sky Pool.",
                      ["อาคาร a", "sky pool"], ["level 3", "sky pool"]),
                _line("ลูกค้าอยากเห็นชั้นไหนเป็นพิเศษไหมคะ", "Which floor would you like to see?", ["ชั้นไหน"], ["which floor"]),
            ],
            "params": {
                "floors": [
                    {"label_en": "G · GROUND", "sub_en": "Fitness · Kids · Lobby", "asset": "fx_floor_g", "line": 2,
                     "next_story": "fx_ground"},
                    {"label_en": "B · BASEMENT", "sub_en": "Spa · Indoor pools", "asset": "fx_floor_b", "line": 1},
                    {"label_en": "3 · BUILDING A", "sub_en": "Lounges · Sky Pool", "asset": "fx_floor_3", "line": 3},
                ],
                "order_bottom_up": [1, 0, 2],
                "end_hint_en": "Tap a floor",
            },
            "taps": [
                {"id": "story_fx_facilities_g", "label_en": "Ground floor", "label_th": "ชั้น G", "next_story": "fx_ground",
                 "tool": None},
            ],
            "sources": ["facts:จุดเด่น"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_ground", "template": "drill",
            "questions_th": ["ชั้น G มีอะไรบ้าง"], "questions_en": ["What is on the ground floor?"],
            "lines": [
                _line("ชั้น G มีฟิตเนส Biogenesis ริมสระค่ะ", "The ground floor has the Biogenesis fitness by the pool.",
                      ["biogenesis"], ["biogenesis"]),
                _line("ห้องปั่นจักรยาน Spinning Class", "A Spinning Class studio,", ["spinning"], ["spinning"]),
                _line("พิลาทิสสตูดิโอ", "a Pilates Studio,", ["พิลาทิส", "pilates"], ["pilates"]),
                _line("บันจี้ยิมและโยคะ", "Bungee Gym and Yoga,", ["บันจี้", "bungee"], ["bungee"]),
                _line("และ Junior World สำหรับเด็กค่ะ", "and Junior World for the children.", ["junior"], ["junior"]),
            ],
            "params": {
                "base_asset": "fx_floor_g",
                "items": [
                    {"label_en": "Biogenesis", "asset": "fx_photo_1", "at": [50, 58], "line": 0},
                    {"label_en": "Spinning Class", "asset": "fx_photo_2", "at": [44.7, 51], "line": 1},
                    {"label_en": "Pilates Studio", "asset": "fx_photo_3", "at": [54.6, 51], "line": 2},
                    {"label_en": "Bungee Gym & Yoga", "asset": "fx_photo_4", "at": [75, 53], "line": 3},
                    {"label_en": "Junior World", "asset": "fx_photo_5", "at": [26.8, 50], "line": 4},
                ],
                "more_en": "Also here: Lobby · Journey to the Future",
            },
            "taps": [
                {"id": "story_fx_ground_back", "label_en": "← All floors", "label_th": "ทุกชั้น",
                 "next_story": "fx_facilities", "tool": None},
            ],
            "sources": ["facts:จุดเด่น"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_sky_pool", "template": "spotlight",
            "questions_th": ["Sky Pool อยู่ตรงไหน"], "questions_en": ["Where is the Sky Pool?"],
            "lines": [
                _line("Sky Pool อยู่ที่ชั้น 3 ของอาคาร A ค่ะ", "The Sky Pool is on level 3 of building A.", ["sky pool"],
                      ["sky pool"]),
                _line("พื้นสระบางส่วนเป็นกระจก มองลงไปเห็นฟิตเนสข้างล่างได้ค่ะ",
                      "Part of the pool floor is glass, looking down into the gym.", ["กระจก"], ["glass"]),
            ],
            "params": {
                "base_asset": "fx_render",
                "zoom": {"x": 50, "y": 57, "scale": 1.8},
                "spot": {"x": 50, "y": 57, "label_en": "SKY POOL", "sub_en": "Level 3 · Building A"},
                "spot_line": 0,
                "photo": {"asset": "fx_photo_6", "title_en": "SKY POOL", "sub_en": "Level 3 · Building A",
                          "chips_en": ["Glass-floor section above the gym", "Sun loungers by the pool"], "line": 1},
            },
            "taps": [],
            "sources": ["facts:จุดเด่น", "slides:intro-041"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_tower", "template": "assembly",
            "questions_th": ["ตึกสูงกี่ชั้น", "ตึกสูงเท่าไหร่"], "questions_en": ["How tall are the buildings?"],
            "lines": [
                _line("อาคารของเราเป็น Low-rise 8 ชั้นค่ะ", "Our buildings are low-rise, 8 floors.", ["low-rise", "lowrise"],
                      ["low-rise", "lowrise"]),
                _line("พื้นดาดฟ้าสูงประมาณ 23 เมตรจากระดับถนน", "The roof deck is about 23 metres above the street.",
                      ["ดาดฟ้า"], ["roof deck"]),
                _line("ทั้งโครงการมี 6 อาคาร ตั้งแต่ A ถึง F ค่ะ", "There are 6 buildings, A to F.", ["อาคาร"], ["buildings"]),
            ],
            "params": {
                "frames": ["fx_build_%02d" % k for k in range(1, 13)],
                "frame_ms": 160,
                "final_asset": "fx_render",
                "ground_y": 80, "roof_y": 22,
                "counters": [
                    {"to": 8, "dec": 0, "prefix_en": "", "unit_en": "FLOORS", "label_en": "LOW-RISE · G + 2–8", "line": 0},
                    {"to": 23, "dec": 0, "prefix_en": "≈", "unit_en": "M", "label_en": "ROOF DECK FROM STREET", "line": 1},
                ],
                "chips": [{"label_en": x, "line": 2} for x in "ABCDEF"],
            },
            "taps": [],
            "sources": ["facts:โครงการ", "report:06_masterplan.md"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_studio", "template": "details",
            "questions_th": ["ห้องสตูดิโอเป็นยังไง"], "questions_en": ["What is the studio like?"],
            "lines": [
                _line("นี่คือห้องสตูดิโอค่ะ", "This is the studio.", ["สตูดิโอ"], ["studio"]),
                _line("ขนาด 24 ถึง 29 ตารางเมตร", "24 to 29 square metres.", ["ตารางเมตร"], ["square"]),
                _line("บรรยากาศภายในจะเป็นแบบนี้ค่ะ", "The interior mood looks like this.", ["บรรยากาศ"], ["mood"]),
            ],
            "params": {
                "wire_asset": "fx_wire", "solid_asset": "fx_solid",
                "value": {"from": 24, "to": 29, "unit_en": "SQ.M."},
                "meta_en": ["STUDIO", "Example layout"],
                "mood_assets": ["fx_photo_1", "fx_photo_2", "fx_photo_3"],
                "mood_tag_en": "Interior mood · show-unit renders",
                "lines": [0, 1, 2],
            },
            "taps": [],
            "sources": ["facts:แบบห้องและขนาด"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_nearby", "template": "distance",
            "questions_th": ["ใกล้อะไรบ้าง", "ห่างทะเลไหม"], "questions_en": ["What is nearby?"],
            "lines": [
                _line("โครงการอยู่ย่านจอมเทียน ห่างหาดจอมเทียนเส้นตรงประมาณ 600 เมตรค่ะ",
                      "We are in Jomtien, about 600 metres from Jomtien Beach in a straight line.", ["จอมเทียน"], ["jomtien"]),
                _line("สนามบินอู่ตะเภาประมาณ 30 นาที", "U-Tapao airport is about 30 minutes away.", ["อู่ตะเภา"],
                      ["u-tapao", "utapao"]),
            ],
            "params": {
                "map_asset": "fx_map",
                "pin": {"x": 58, "y": 46, "label_en": "EMBASSY WORLD"},
                "route_to": {"x": 22, "y": 55, "label_en": "600 m"},
                "rows": [
                    {"label_en": "Jomtien Beach · straight line", "value_en": "≈ 600 m", "line": 0},
                    {"label_en": "U-Tapao Airport", "value_en": "≈ 30 min", "line": 1},
                ],
            },
            "taps": [],
            "sources": ["facts:ทำเล", "report:04_location.md#ระยะหาด"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_units_total", "template": "metric",
            "questions_th": ["โครงการมีกี่ยูนิต"], "questions_en": ["How many units are there?"],
            "lines": [
                _line("ทั้งโครงการมี 1,066 ยูนิตค่ะ", "There are 1,066 units in total.", ["ยูนิต"], ["units"]),
                _line("เป็นสตูดิโอ 117 หนึ่งห้องนอน 801 และสองห้องนอน 148 ยูนิต",
                      "117 studios, 801 one-bedrooms and 148 two-bedrooms.", ["สตูดิโอ"], ["studios"]),
            ],
            "params": {
                "bg_asset": "fx_render",
                "value": {"to": 1066, "dec": 0, "unit_en": "UNITS"},
                "label_en": "6 BUILDINGS · A–F",
                "parts": [{"label_en": "Studio", "to": 117, "line": 1}, {"label_en": "1 Bedroom", "to": 801, "line": 1},
                          {"label_en": "2 Bedrooms", "to": 148, "line": 1}],
                "line": 0,
            },
            "taps": [],
            "sources": ["facts:โครงการ"],
            "notes_th": "fixture",
        },
        {
            "id": "fx_smart_home", "template": "features",
            "questions_th": ["สมาร์ทโฮมมีอะไรบ้าง"], "questions_en": ["What smart home features are there?"],
            "lines": [
                _line("ห้องรองรับ Smart Home สั่งไฟและม่านได้จากแอป", "Rooms are ready for Smart Home: lights and curtains from the app.",
                      ["smart home", "สมาร์ทโฮม"], ["smart home"]),
                _line("Smart Air ดูแลอากาศในห้อง", "Smart Air looks after the air in the room.", ["smart air"], ["smart air"]),
                _line("และ Connected Living ผ่านแอป Embassy AI", "And Connected Living through the Embassy AI app.",
                      ["connected"], ["connected"]),
                _line("ชุด Smart Home ในห้องเป็นแพ็กเกจเสริม ไม่รวมในราคาห้องค่ะ",
                      "The in-room Smart Home set is an optional package, not included in the room price.",
                      ["แพ็กเกจ"], ["optional"]),
            ],
            "params": {
                "items": [
                    {"label_en": "Smart Home", "asset": "fx_photo_4", "note_en": "Lights, curtains", "line": 0},
                    {"label_en": "Smart Air", "asset": "fx_photo_5", "note_en": "Air in the room", "line": 1},
                    {"label_en": "Connected Living", "asset": "fx_photo_6", "note_en": "Embassy AI app", "line": 2},
                ],
                "footnote_en": "In-room Smart Home set: optional package, not included in the room price",
            },
            "taps": [],
            "sources": ["facts:จุดเด่น"],
            "notes_th": "fixture",
        },
        {
            # drawn, no picture: a bar per place on one scale of minutes
            "id": "fx_travel", "template": "journey",
            "questions_th": ["ไปห้างกี่นาที"], "questions_en": ["How far is the mall?"],
            "lines": [
                _line("เซ็นทรัล พัทยา ขับรถประมาณ 12 ถึง 17 นาที", "Central Pattaya is about 12 to 17 minutes by car,",
                      ["เซ็นทรัล"], ["central"]),
                _line("และสนามบินอู่ตะเภาประมาณ 30 นาทีค่ะ", "and U-Tapao Airport about 30 minutes.", ["อู่ตะเภา"],
                      ["u-tapao", "utapao"]),
            ],
            "params": {
                "origin_en": "EMBASSY WORLD", "origin_sub_en": "Jomtien · drive times", "bar_max": 30,
                "rows": [
                    {"icon": "mall", "label_en": "Central Pattaya", "sub_en": "9.9 km", "value_en": "≈ 12–17 min",
                     "bar": [12, 17], "line": 0},
                    {"icon": "plane", "label_en": "U-Tapao Airport", "value_en": "≈ 30 min", "bar": [30], "line": 1},
                ],
            },
            "taps": [],
            "sources": ["facts:ทำเล", "report:92_appendix_travel.md#ค.4.2"],
            "notes_th": "fixture",
        },
        {
            # drawn, no picture: the same rows as rings of drive time round the
            # project's pin (journey's view "rings" -- the nearby stories, 21 Oct)
            "id": "fx_around", "template": "journey",
            "questions_th": ["ใกล้เซเว่นไหม"], "questions_en": ["Is there a 7-Eleven close by?"],
            "lines": [
                _line("เซเว่นที่ใกล้ที่สุดขับรถประมาณ 4 นาทีค่ะ", "The nearest 7-Eleven is about 4 minutes by car,",
                      ["เซเว่น"], ["eleven"]),
                _line("หรือเดินประมาณ 23 นาที", "or about 23 minutes on foot,", ["เดิน"], ["foot"]),
                _line("โรงพยาบาลจอมเทียนประมาณ 8 นาที", "Jomtien Hospital about 8 minutes,", ["โรงพยาบาล"],
                      ["hospital"]),
                _line("และโรงเรียนนานาชาติ German International School ก็ประมาณ 8 นาทีค่ะ",
                      "and German International School about 8 minutes too.", ["german", "เยอรมัน"], ["german"]),
            ],
            "params": {
                "view": "rings", "origin_en": "EMBASSY WORLD", "origin_sub_en": "by car, no traffic", "bar_max": 10,
                "footnote_en": "Drive time from the project · not a map",
                "rows": [
                    {"icon": "shop", "label_en": "7-Eleven", "sub_en": "Nearest by road", "value_en": "≈ 4 min",
                     "bar": [4], "walk": {"value_en": "≈ 23 min", "line": 1}, "line": 0},
                    {"icon": "hospital", "label_en": "Jomtien Hospital", "value_en": "≈ 8 min", "bar": [8], "line": 2},
                    {"icon": "school", "label_en": "German International School", "short_en": "German Int'l",
                     "value_en": "≈ 8 min", "bar": [8], "line": 3},
                ],
            },
            "taps": [
                {"id": "story_fx_around_where", "label_en": "Where we are", "label_th": "ทำเล",
                 "next_story": "fx_where", "tool": None},
            ],
            "sources": ["facts:ทำเล"],
            "notes_th": "fixture",
        },
        {
            # drawn, no picture: a schematic map, distances as dashed lines
            "id": "fx_where", "template": "neighborhood",
            "questions_th": ["อยู่ตรงไหน"], "questions_en": ["Where is it?"],
            "lines": [
                _line("โครงการอยู่ย่านจอมเทียนค่ะ ติดสวนหนองโพรง", "We are in Jomtien, next to Nong Phrong park,",
                      ["จอมเทียน"], ["jomtien"]),
                _line("ห่างหาดเส้นตรงประมาณ 600 เมตร", "about 600 metres from the beach in a straight line.", ["หาด"],
                      ["beach"]),
            ],
            "params": {
                "sea": {"to_x": 14}, "beach": {"to_x": 20, "label_en": "JOMTIEN BEACH", "line": 1},
                "roads": [{"x1": 23, "y1": 0, "x2": 23, "y2": 100},
                          {"x1": 88, "y1": 0, "x2": 88, "y2": 100, "label_en": "Sukhumvit Rd"}],
                "places": [{"kind": "project", "x": 40, "y": 36, "label_en": "EMBASSY WORLD", "line": 0},
                           {"kind": "park", "x": 32, "y": 46, "w": 20, "h": 26, "label_en": "Nong Phrong Park",
                            "line": 0}],
                "measures": [{"x1": 40, "y1": 36, "x2": 17, "y2": 36, "label_en": "≈ 600 m", "line": 1}],
                "rows": [{"icon": "park", "label_en": "Nong Phrong Park", "value_en": "Next door", "line": 0},
                         {"icon": "beach", "label_en": "Jomtien Beach", "value_en": "≈ 600 m", "line": 1}],
                "footnote_en": "Schematic · not to scale",
            },
            "taps": [],
            "sources": ["facts:ทำเล"],
            "notes_th": "fixture",
        },
    ]


# ---- pictures --------------------------------------------------------------------

def _font(size):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_pictures(src: Path, width: int) -> dict[str, str]:
    """name -> file, drawn at `width` px wide."""
    from PIL import Image, ImageDraw

    src.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}

    def save(name, image, ext="png"):
        path = src / ("%s.%s" % (name, ext))
        image.save(path)
        out[name] = path.name

    w = width
    for k, label in enumerate("ABC"):
        h = int(w * 0.75)
        im = Image.new("RGB", (w, h), (246, 247, 251))
        d = ImageDraw.Draw(im)
        pad = w // 10
        d.rectangle([pad, pad, w - pad, h - pad], outline=(30, 30, 40), width=max(2, w // 160))
        d.line([w // 2, pad, w // 2, h - pad - (k + 1) * h // 8], fill=(30, 30, 40), width=max(2, w // 200))
        d.line([pad, h // 2, w // 2, h // 2], fill=(30, 30, 40), width=max(2, w // 200))
        d.text((pad * 1.3, pad * 1.2), "PLAN %s" % label, fill=(30, 30, 40), font=_font(w // 18))
        save("fx_plan_%s" % label.lower(), im)
    hues = [(64, 120, 200), (200, 90, 120), (60, 170, 140), (210, 160, 60), (130, 90, 210), (40, 150, 210)]
    for k, base in enumerate(hues, start=1):
        h = int(w * 0.62)
        im = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(im)
        for y in range(h):
            t = y / h
            d.line([0, y, w, y], fill=tuple(int(c * (1 - .55 * t)) for c in base))
        d.ellipse([w * .55, h * .15, w * .85, h * .45], fill=(255, 236, 200))
        d.rectangle([w * .1, h * .55, w * .45, h * .95], fill=tuple(min(255, c + 40) for c in base))
        d.text((w * .06, h * .08), "PHOTO %d" % k, fill=(255, 255, 255), font=_font(w // 16))
        save("fx_photo_%d" % k, im, "jpg")
    for name, tint in (("fx_floor_g", (90, 120, 200)), ("fx_floor_b", (60, 150, 170)), ("fx_floor_3", (150, 110, 210))):
        h = int(w * 9 / 16)
        im = Image.new("RGB", (w, h), (226, 232, 246))
        d = ImageDraw.Draw(im)
        for k in range(6):
            x0 = w * (.08 + k * .145)
            d.rectangle([x0, h * .25, x0 + w * .12, h * .75], outline=tint, width=max(2, w // 220))
        d.ellipse([w * .4, h * .55, w * .6, h * .9], outline=tint, width=max(2, w // 200))
        d.text((w * .06, h * .06), name[-1].upper() + " FLOOR", fill=tint, font=_font(w // 16))
        save(name, im)
    # the campus "hologram" building up over 12 frames, then the render
    fw, fh = w, int(w * 9 / 16)
    for k in range(1, 13):
        im = Image.new("RGB", (fw, fh), (2, 4, 14))
        d = ImageDraw.Draw(im)
        ground = fh * .80
        d.line([fw * .05, ground, fw * .95, ground], fill=(70, 200, 255), width=2)
        rise = min(1.0, k / 9)
        for b in range(6):
            x0 = fw * (.08 + b * .145)
            top = ground - (ground - fh * .22) * rise
            d.rectangle([x0, top, x0 + fw * .12, ground], outline=(70, 200, 255), width=2)
            if k > 9:
                for f in range(8):
                    y = ground - (ground - fh * .22) * (f + .5) / 8
                    d.line([x0 + 6, y, x0 + fw * .12 - 6, y], fill=(255, 210, 120), width=1)
        save("fx_build_%02d" % k, im)
    im = Image.new("RGB", (fw, fh))
    d = ImageDraw.Draw(im)
    for y in range(fh):
        t = y / fh
        d.line([0, y, fw, y], fill=(int(120 + 100 * t), int(170 + 50 * t), int(230 - 40 * t)))
    for b in range(6):
        x0 = fw * (.08 + b * .145)
        d.rectangle([x0, fh * .22, x0 + fw * .12, fh * .80], fill=(236, 232, 222), outline=(160, 150, 140))
        for f in range(8):
            y = fh * .80 - (fh * .58) * (f + .5) / 8
            d.rectangle([x0 + 6, y - 5, x0 + fw * .12 - 6, y + 5], fill=(90, 120, 150))
    d.rectangle([0, fh * .80, fw, fh], fill=(60, 140, 120))
    save("fx_render", im, "jpg")
    for name, bg, fg in (("fx_wire", (6, 10, 40), (80, 160, 255)), ("fx_solid", (6, 10, 40), (240, 240, 245))):
        h = int(w * 0.62)
        im = Image.new("RGB", (w, h), bg)
        d = ImageDraw.Draw(im)
        pts = [(w * .2, h * .55), (w * .5, h * .35), (w * .8, h * .55), (w * .5, h * .75)]
        if name == "fx_solid":
            d.polygon(pts, fill=fg)
            d.polygon([(w * .2, h * .55), (w * .5, h * .75), (w * .5, h * .9), (w * .2, h * .7)], fill=(200, 200, 210))
        else:
            d.polygon(pts, outline=fg, width=3)
            d.line([w * .2, h * .55, w * .2, h * .7], fill=fg, width=3)
            d.line([w * .5, h * .75, w * .5, h * .9], fill=fg, width=3)
        save(name, im)
    h = int(w * 0.75)
    im = Image.new("RGB", (w, h), (236, 230, 214))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w * .18, h], fill=(120, 180, 220))
    for k in range(5):
        d.line([w * (.2 + k * .17), 0, w * (.25 + k * .15), h], fill=(255, 255, 255), width=max(3, w // 120))
    d.line([0, h * .3, w, h * .42], fill=(255, 255, 255), width=max(3, w // 100))
    save("fx_map", im)
    return out


def recipes(files: dict[str, str]) -> list[dict]:
    out = []
    for name, file in sorted(files.items()):
        out.append({"id": name, "source": file, "crop": None, "max_w": 1080, "format": "webp",
                    "why": "fixture picture (drawn)"})
    return out


def build(folder: Path, *, approved: bool = False, size: str = "test") -> Path:
    """A complete stories folder under `folder` (design/, recipes/, assets/,
    asset_pins.json, stories.json). Returns the stories folder."""
    from scripts import build_story_assets as builder

    folder = Path(folder)
    root = folder / "stories"
    src = folder / "src"
    files = _draw_pictures(src, 1080 if size == "screen" else 240)
    (root / "recipes").mkdir(parents=True, exist_ok=True)
    (root / "design").mkdir(parents=True, exist_ok=True)
    (root / "recipes" / "fixture.json").write_text(json.dumps(recipes(files), indent=1), encoding="utf-8")
    (root / "design" / "fixture.json").write_text(json.dumps(stories(), ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    _pins, problems = builder.build_assets(root, src)
    if problems:
        raise RuntimeError("fixture pictures: %s" % problems)
    builder.merge(root)
    if approved:
        approve(root)
    return root


def approve(root: Path, by: str = "test-fixture") -> dict:
    """The tests pin their own approval state (never the project's catalog)."""
    from app import stories as engine

    path = root / engine.CATALOG_NAME
    catalog = json.loads(path.read_text(encoding="utf-8"))
    catalog.update({"approval_status": "approved", "approved_by": by,
                    "approved_at": "2026-10-12T09:00:00+07:00", "effective_at": "2026-10-12T09:00:00+07:00",
                    "disclosure_scope": "customer", "content_state": "preliminary_concept"})
    catalog["approved_sha256"] = engine.content_digest(catalog)
    path.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
    return catalog
