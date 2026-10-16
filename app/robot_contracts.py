"""Ids and codes shared by the server, the robot launcher, the app and the page.

docs/emma-body-nav-design-2026-10-08.md §2.0. One Python source; the Java
(`BodyGestures.java`) and HTML (`client/voice-preview.html`) copies are checked
against it by tests, because two hand-written copies of a rule drift — the
eval-vs-tool measuring bug in CLAUDE.md, in a third language.

**Stdlib only, no app imports.** `scripts/emma_nav.py` imports this on the
robot under a bare python-build-standalone with no numpy and no pythainlp, and
`robot_python.CODE` deploys it as one file. Anything here that needs the Thai
tokenizer is a bug waiting for the first walk.

The server never holds a pulse, a channel, a group number or a step index —
the same rule as `show_unit(room)` and not `show_unit(room, price)`: the model
chooses *which* named thing, and the machine that owns the hardware owns the
numbers. So nothing below is a number that moves anything.
"""
from __future__ import annotations

import re

#: id, Thai label (UI + instruction), kind. The order is the order the tool
#: enum and the page list them in.
BODY_ACTIONS = (
    ("head_up", "เงยหน้า", "head"), ("head_down", "ก้มหน้า", "head"),
    ("head_left", "หันหน้าไปทางซ้าย", "head"), ("head_right", "หันหน้าไปทางขวา", "head"),
    ("head_center", "มองตรง", "head"), ("nod", "พยักหน้า", "head"),
    ("shake_head", "ส่ายหน้า", "head"), ("raise_left_arm", "ยกแขนซ้าย", "arm"),
    ("raise_right_arm", "ยกแขนขวา", "arm"), ("greet_arm", "ยกแขนทักทาย", "arm"),
    ("wave", "โบกมือ", "arm"), ("handshake", "ยื่นมือจับมือ", "arm"),
    ("hands_left", "ขยับมือไปทางซ้าย", "arm"), ("hands_right", "ขยับมือไปทางขวา", "arm"),
    ("rest", "เอาแขนลง/ท่าพัก", "arm"), ("stop", "หยุดท่าทาง", "stop"),
)
BODY_ACTION_IDS = tuple(a[0] for a in BODY_ACTIONS)
BODY_LABELS = {a[0]: a[1] for a in BODY_ACTIONS}
BODY_KINDS = {a[0]: a[2] for a in BODY_ACTIONS}
#: Recorded arm poses the server may play while she talks (`style: "speech"`,
#: app/speech_gestures.py; owner 15 Oct). The same set as the app's
#: BodyGestures.speechCapable and the page's SPEECH_BODY_IDS: no head (ch8
#: burned 12 Sep), no vendor group, no rest, no stop.
SPEECH_BODY_ACTIONS = ("raise_left_arm", "raise_right_arm", "wave", "hands_left", "hands_right")

# Heard guard (§2d): per action, the caption must contain one VERB token, and
# for a sided action also the SIDE token — and, for the actions in
# BODY_HEARD_NOUNS, a BODY NOUN too. Bare nouns (หน้า ท่า มือ ซ้าย ขวา ลด)
# never count on their own: "ลดราคา", "ข้างหน้า" and "ท่าน" must not authorise
# motion. Matched on retrieval.tokenize() words, never on substrings — `ยก`
# sits inside `ยกเลิก`, which is the opposite request (CLAUDE.md: the
# `ราคา`/`อาคาร` bug happened twice in this project already).
#
# A verb is here alone only when it cannot occur in an ordinary sentence of
# this room. Review #19/#22 probed the first table with the real tokenizer
# and each of these authorised a gesture: "ราคาลงไหม", "ผมจะลงที่ชั้นไหนครับ",
# "ขึ้นลงลิฟต์" -> rest (bare ลง); "ตรงไปเลย", "ผ่อนตรงไหม", "Where is the
# shopping center" -> head_center (bare ตรง, center); "Is the lift on the
# left?" -> raise_left_arm; "ขยับไปทางขวาหน่อยค่ะ", "Can we move to the right
# tower" -> hands_right; "มองไปทางซ้ายจะเห็นสระ" (Emma's own narration,
# echoed — the "bit like" case heard.py exists for) -> head_left; "โบกรถ" ->
# wave; "ทักทายลูกค้า" -> greet_arm. So bare ตรง / straight / center / พัก /
# rest are gone, and an ambiguous verb needs its body noun in the same
# caption (BODY_HEARD_NOUNS).
# Value: (verbs, sides) — sides None for an action that has no side.
BODY_HEARD = {
    "head_up": (("เงย", "look up", "head up"), None),
    "head_down": (("ก้ม", "look down", "head down"), None),
    "head_left": (("หัน", "มอง", "turn", "look"), ("ซ้าย", "left")),
    "head_right": (("หัน", "มอง", "turn", "look"), ("ขวา", "right")),
    # "มองหน้าตรง": newmm keeps มองหน้า whole, so the มองตรง run never forms.
    "head_center": (("มองตรง", "หันตรง", "หน้าตรง", "หันหน้าตรง", "มองหน้าตรง",
                     "look straight", "face forward"), None),
    "nod": (("พยัก", "nod"), None),
    "shake_head": (("ส่าย", "shake"), None),
    "raise_left_arm": (("ยก", "raise", "lift"), ("ซ้าย", "left")),
    "raise_right_arm": (("ยก", "raise", "lift"), ("ขวา", "right")),
    "greet_arm": (("ทักทาย", "โบก", "greet", "wave"), None),
    "wave": (("โบก", "wave"), None),
    # "ขอเชคแฮน" (the owner, 13 Oct) reaches the caption as เช็ค/เชค + แฮนด์/แฮน --
    # only จับมือ was a verb, so the guard refused the robot's most-asked gesture
    "handshake": (("จับมือ", "handshake", "shake hands", "shake hand",
                   "เช็คแฮนด์", "เช็กแฮนด์", "เชคแฮนด์", "เช็คแฮน", "เชคแฮน"), None),
    "hands_left": (("ขยับ", "เลื่อน", "move"), ("ซ้าย", "left")),
    "hands_right": (("ขยับ", "เลื่อน", "move"), ("ขวา", "right")),
    # "เอามือลง" is how people say "hands down" (review 2 #13); มือ is not a
    # rest noun ("ลงมือทำเลย"), so the whole run is the verb and a free one.
    # "arms down": "put your arms down" never forms the "put down" run.
    "rest": (("ลง", "เก็บ", "lower", "put down", "ท่าพัก", "พักแขน", "เอามือลง",
              "arms down", "arm down"), None),
}   # "stop" is never guarded: asking before a stop is the wrong way round

_HEAD_NOUNS = ("หน้า", "หัว", "head", "face")
_ARM_NOUNS = ("แขน", "มือ", "arm", "arms", "hand", "hands")
#: Actions whose verb alone is ordinary speech (review #19/#22): the caption
#: must also name the body part, as its own word or glued into a compound
#: (หันหน้า, แขนขวา, โบกมือ, ยกมือ). Value: (nouns, verbs that are enough on
#: their own). หัน is free because "หันขวา" is how people say it and nobody
#: narrates with it; มอง/look/turn are not — "มองไปทางซ้ายจะเห็นสระ" and
#: "turn right at the lobby" are directions, not commands. rest takes แขน
#: only: "ลงมือทำเลย" is ลง+มือ and means "get on with it".
BODY_HEARD_NOUNS = {
    "head_left": (_HEAD_NOUNS, ("หัน",)),
    "head_right": (_HEAD_NOUNS, ("หัน",)),
    "shake_head": (_HEAD_NOUNS, ("ส่าย",)),     # "shake hands" is not shake_head
    "raise_left_arm": (_ARM_NOUNS, ()),
    "raise_right_arm": (_ARM_NOUNS, ()),
    "greet_arm": (_ARM_NOUNS, ()),
    "wave": (_ARM_NOUNS, ()),
    "hands_left": (_ARM_NOUNS, ()),
    "hands_right": (_ARM_NOUNS, ()),
    "rest": (("แขน", "arm", "arms"), ("ท่าพัก", "พักแขน", "เอามือลง")),
}

#: A verb with one of these among the two words just before it is refused,
#: not obeyed (review #22: "ไม่ต้องยกแขนซ้ายนะ" authorised raise_left_arm).
#: Only the words just before the verb: a negation elsewhere in the sentence
#: ("ยกแขนซ้าย ไม่ต้องโบกมือ") is about the other verb. "don't" tokenizes to
#: don + t, so "don" is the word.
#: Review 2 #12 (real tokenizer, 9 Oct) found the verb still obeyed after a
#: STOP — "หยุดพยักหน้า", "เลิกพยักหน้า": the guard exists for the model that
#: re-sends nod when the guest said stop nodding — after "I won't" (won + t),
#: and in the PAST ("เมื่อกี้พยักหน้าทำไม" asks why it nodded). So these
#: words refuse the verb after them too. เมื่อกี๊ splits into เมื่อ + กี๊, and
#: เมื่อ alone is "when", so กี๊ is the word. ไม่ได้ / ไม่ต้องการ are one token
#: each ("ผมไม่ได้สั่งให้ยกแขน", "ไม่ต้องการให้ยกแขน").
BODY_HEARD_NEGATORS = ("ไม่", "ไม่ต้อง", "ไม่ได้", "ไม่ต้องการ", "อย่า", "ห้าม",
                       "หยุด", "เลิก", "พอ", "พอแล้ว",
                       "เคย", "เพิ่ง", "เมื่อกี้", "ตะกี้", "กี๊",
                       "don", "dont", "not", "never", "won", "didn", "stop", "quit")
#: Words a negation reaches *across* without using up the two-word window:
#: "ไม่อยากให้ก้มหน้า" is ไม่ อยาก ให้ ก้มหน้า, the negator three words back
#: (review 2 #12), and "I don't want you to nod" is six. Only the want/ask
#: chain and the addressee: ช่วย and ขอ are deliberately not here — "ถ้าไม่
#: รบกวน ช่วยยกแขน" is a polite request and must not reach back to its ไม่.
BODY_HEARD_REACH = ("อยาก", "ให้", "ต้องการ", "สั่ง", "บอก", "หุ่น", "หุ่นยนต์",
                    "เอ็มม่า", "คุณ", "น้อง",
                    "want", "you", "to", "me", "please", "ask", "asked", "tell",
                    "told", "ever", "emma")
#: Somebody other than the robot doing the gesture is narration, not a
#: command (review 2 #12): "เขาพยักหน้าแล้ว", "ลูกค้าส่ายหน้า", "ผมยกมือขวา
#: แล้ว". Only when the narrator is the word right before the verb (aspect
#: words between them are skipped): "ผมอยากให้ยกแขน" puts ให้ there and is a
#: request. Not หนู/พี่/เรา — Thai uses those for the listener too ("หนูยก
#: แขนหน่อย" is said *to* the robot). handshake is exempt: it is done *with*
#: the speaker, so "ผมจับมือได้ไหม" asks for it.
BODY_HEARD_NARRATORS = ("เขา", "เค้า", "พวกเขา", "ลูกค้า", "มัน", "ผม", "ฉัน", "ดิฉัน")
#: กำลัง is spelled as tokenize leaves it: NFKC turns ำ into ํ+า and newmm
#: then cuts "กำลัง" into กํา + ลัง, so the word itself never arrives.
_ASPECT = ("กํา", "ลัง", "จะ", "ก็", "ได้")
_NARRATION_OK = ("handshake",)
#: A caption that ends as a yes/no question asks *about* the gesture
#: ("หุ่นพยักหน้าเป็นไหม", "เมื่อกี้ส่ายหน้าหรือเปล่า"), the same rule as the
#: place guard's where-questions. "...ได้ไหม" is how Thai asks politely for
#: something and "...กันไหม" is "shall we" ("จับมือกันไหม"): those still pass.
#: Trailing politeness particles are skipped first.
YES_NO_TAILS = ("ไหม", "มั้ย", "หรือเปล่า", "รึเปล่า", "เปล่า", "หรือยัง", "รึยัง",
                "หรือไม่", "ใช่ไหม", "ใช่มั้ย")
YES_NO_ASKS_FOR_IT = ("ได้", "กัน")
PARTICLES = ("ครับ", "คับ", "ค่ะ", "คะ", "ค่า", "นะ", "น่ะ", "จ้ะ", "จ้า", "จ๊ะ", "ฮะ",
             "เนอะ", "สิ", "ซิ")
#: An English caption that opens like a yes/no question ("Did you nod?",
#: "Do you wave at people?"). Not can/could/will/would: "Can you raise your
#: right arm?" is a polite request (R-body-raise_right-en).
EN_QUESTION_STARTS = ("is", "are", "was", "were", "do", "does", "did", "have", "has")
#: Reduplication: newmm leaves ๆ glued ("ตรงๆ") or alone, and "มองตรงๆ หน่อย"
#: was refused head_center in the 9 Oct live eval (review 2 #13). It repeats
#: the word; it never changes which word it is.
_MAI_YAMOK = "ๆ"
#: Pieces that may be glued to one action's verb only. ลง is not a global
#: filler ("ลงมือ", "ราคาลง") but "ก้มลง" can only mean the head going down.
BODY_HEARD_GLUE = {"head_down": ("ลง",)}

#: Words that may sit *glued* to a verb or side inside one tokenizer word.
#: Measured on retrieval.tokenize, 8 Oct: newmm keeps body compounds whole —
#: "ก้มหน้าหน่อย" -> [ก้มหน้า, หน่อย], "ยกแขนขวา" -> [ยก, แขนขวา],
#: "หันหน้าไปทางซ้าย" -> [หันหน้า, ไป, ทางซ้าย], "โบกมือ" -> [โบกมือ] — so a
#: whole-token match alone would refuse almost every real command. A token
#: counts when it splits *completely* into the term plus these pieces; one
#: unknown piece and it does not: "ยกเลิก" is ยก+เลิก, "มือถือ" is มือ+ถือ,
#: "ข้างหน้า" is ข้าง+หน้า, "ห้องพัก" is ห้อง+พัก — none of them pass. The
#: pieces alone never count (they are the bare nouns above).
BODY_HEARD_FILLERS = ("หน้า", "หัว", "มือ", "แขน", "ทาง", "ไป", "ขึ้น")

#: The integrator's check of #19 (real tokenizer, 9 Oct): a SIDED gesture
#: still passed on sentences about the room, not the robot —
#: "ห้องนี้หันหน้าไปทางขวาไหม" (which way the unit faces) -> head_right,
#: "หันไปทางขวาจะเจอลิฟต์" and "มองไปทางซ้ายจะเห็นสระหน้าตึก" (Emma's own
#: directions, echoed) -> head_right/left, "look at the left face of the
#: building" -> head_left, "Is the lift on the left hand side?" ->
#: raise_left_arm. A sided gesture is refused when the caption names a thing
#: in the building or says "you will see/find": a command to the robot's
#: body does not point at the lift. Thai tokens match by prefix (ห้องประชุม,
#: สระว่ายน้ำ): a wrong veto costs one question, never a motion.
BODY_HEARD_SCENE = (
    "ห้อง", "ตึก", "อาคาร", "คอนโด", "ระเบียง", "วิว", "ทิศ", "สระ", "ลิฟต์", "ลิฟท์",
    "ล็อบบี้", "บันได", "ที่จอด", "โครงการ", "ฝั่ง", "เห็น", "เจอ",
    "room", "unit", "building", "tower", "balcony", "view", "lift", "elevator", "pool",
    "lobby", "entrance", "exit", "stairs", "parking",
)
#: ซ้ายมือ / ขวามือ / "left hand side" are a SIDE, not a hand: the same
#: check found "ขยับไปทางซ้ายมือหน่อย" authorising hands_left because the
#: one token gave both the side and the arm noun.
_HAND_SIDE_TH = ("ซ้ายมือ", "ขวามือ")

BODY_STATUSES = ("verified", "direction_known", "operator_mapped", "needs_mapping")

#: Every id that crosses a boundary (body action, place id). Lower-case ASCII
#: only, so an id can never carry a path, a shell word or Thai into `su`.
ID_RE = r"^[a-z][a-z0-9_]{1,31}$"
COMMAND_ID_RE = r"^[0-9a-f]{32}$"
#: Any page->server body/nav message larger than this is dropped. The biggest
#: legitimate one is nav_caps with a handful of places; 8 KiB is several
#: times that and still too small to be a useful channel for anything else.
MAX_INBOUND_BYTES = 8192

BODY_REASONS = {   # code -> Thai (UI text and tool instruction stem)
    "disabled": "ปิดการสั่งท่าทางไว้บนหุ่น", "server_disabled": "ปิดการสั่งท่าทางไว้ที่เซิร์ฟเวอร์",
    "needs_mapping": "ท่านี้ยังไม่ได้ตั้งค่าบนตัวหุ่น",
    "direction_unconfirmed": "ท่านี้ยังไม่ได้เปิดให้ใช้ (ยังไม่ทดสอบทิศ)",
    "busy": "แขนกำลังทำท่าอื่นอยู่", "cooldown": "เพิ่งทำท่าไป รอสักครู่", "rate_limited": "สั่งท่าถี่เกินไป",
    "navigating": "หุ่นกำลังเดินอยู่ ขยับแขนไม่ได้", "latched": "เพิ่งกดหยุดแขน ล็อกไว้ชั่วคราว",
    "operator_screen": "เจ้าหน้าที่เปิดหน้าควบคุมอยู่", "not_foreground": "หน้าจอหุ่นไม่ได้อยู่ด้านหน้า",
    "arm_unavailable": "ต่อบอร์ดแขนไม่ได้", "not_robot_face": "ไม่ได้คุยผ่านหน้าหุ่น",
    "heard_missing": "ไม่ได้ยินคำสั่งท่าทางชัดเจน", "timeout": "หุ่นไม่ตอบรับคำสั่ง", "bad_request": "คำสั่งไม่ถูกต้อง",
    "nav_unknown": "ตรวจไม่ได้ว่าหุ่นกำลังเดินอยู่หรือไม่ จึงยังไม่ขยับแขน",
    # link level (robot_face_link), distinct from "busy" (= the ARM is busy,
    # decided on the robot): one word meaning two things was review point B10.
    "request_pending": "คำสั่งก่อนหน้ายังรอหุ่นตอบอยู่", "disconnected": "หน้าจอหุ่นหลุดการเชื่อมต่อ",
}
NAV_REASONS = {
    "disabled": "ฟังก์ชันนำทางปิดอยู่บนหุ่น",
    "not_installed": "ยังไม่ได้ติดตั้งระบบนำทางบนหุ่น (ติดตั้งจากคอมพิวเตอร์ก่อน)",
    "places_stale": "ข้อมูลจุดหมายไม่ตรงกับเส้นทางที่ติดตั้ง — ติดตั้งใหม่จากคอมพิวเตอร์",
    "unknown_place": "ไม่รู้จักจุดหมายนี้", "already_there": "อยู่ที่นี่แล้ว",
    "no_leg": "ยังไม่มีเส้นทางจากจุดนี้ไปที่นั่น",
    "location_unknown": "ไม่รู้ว่าหุ่นอยู่ตรงไหน — ให้เจ้าหน้าที่พาหุ่นกลับแท่นชาร์จก่อน",
    "low_battery": "แบตเตอรี่ไม่พอ", "run_active": "หุ่นกำลังเดินอยู่",
    "leg_unverified": "เส้นทางนี้ยังไม่เคยทดสอบแยก — ต้องให้เจ้าหน้าที่กดสั่งเอง",
    "body_busy": "แขนกำลังทำท่าอยู่ รอสักครู่", "body_latched": "แขนถูกล็อกหลังกดหยุด",
    "arms_not_rest": "เก็บแขนไม่สำเร็จ — ยังเดินไม่ได้", "board_unreachable": "ติดต่อฐานล้อไม่ได้",
    "operator_screen": "เจ้าหน้าที่เปิดหน้าควบคุมอยู่", "not_foreground": "หน้าจอหุ่นไม่ได้อยู่ด้านหน้า",
    "launcher_failed": "เริ่มระบบนำทางไม่สำเร็จ", "heard_missing": "ไม่ได้ยินชื่อจุดหมายชัดเจน",
    "not_robot_face": "ไม่ได้คุยผ่านหน้าหุ่น", "timeout": "หุ่นไม่ตอบรับคำสั่ง", "bad_request": "คำสั่งไม่ถูกต้อง",
    "request_pending": "คำสั่งก่อนหน้ายังรอหุ่นตอบอยู่", "disconnected": "หน้าจอหุ่นหลุดการเชื่อมต่อ",
    "nav_latched": "เพิ่งกดหยุดหุ่น ล็อกการเดินไว้ชั่วคราว",
    "voice_disabled": "ปิดการสั่งเดินด้วยเสียงไว้ — ให้เจ้าหน้าที่กดสั่งที่หน้าจอ",
    "relocalize": "ระบบระบุตำแหน่งของฐานล้อปิดอยู่หรืออ่านไม่ได้ — ให้เจ้าหน้าที่เปิด/ตั้งตำแหน่งใหม่ก่อน",
    # terminal reasons of a run (launcher/drive_route)
    "stop_pressed": "กดหยุดแล้ว", "door_blocked": "ประตูปิดหรือมีคนยืนขวางประตู", "obstacle": "มีสิ่งกีดขวางทาง",
    "link_lost": "ขาดการติดต่อกับฐานล้อ", "dock_blocked": "เข้าแท่นชาร์จไม่สำเร็จ — หุ่นจอดอยู่ข้างแท่น",
    "app_lost": "แอปหน้าจอหุ่นหยุดทำงาน หุ่นจึงหยุด", "launcher_lost": "ตัวควบคุมการเดินหยุดทำงาน หุ่นจึงหยุด",
    "refused": "ระบบนำทางไม่ยอมเริ่ม (ตรวจก่อนออกเดินไม่ผ่าน)", "stopped": "หุ่นหยุดกลางทาง",
    "error": "ระบบนำทางหยุดเพราะข้อผิดพลาด",
    "interrupted": "การเดินถูกขัดจังหวะ หุ่นหยุดแล้ว", "pc_run": "มีการสั่งเดินจากคอมพิวเตอร์อยู่",
}
NAV_STATES = ("idle", "preparing", "starting", "running", "arrived", "stopped", "refused", "failed")
NAV_TERMINAL = ("arrived", "stopped", "refused", "failed")
#: Who started a leg: `emma_nav.py go --origin`, the app's go() and goCommand
#: (NavRules.ORIGINS) and the page's status copy. "auto" (owner, 10 ต.ค. 2026:
#: "ทำงานเสร็จก็ให้เข้าไปชาร์จที่แท่น") is the app's own walk back to the dock
#: after the robot stood idle at a place (AutoReturn.java). It is treated like
#: "tap" for an unverified leg -- staff switched it on and the robot warns on
#: its face first -- and NEVER like "voice"; the page cannot ask for it (the
#: bridge grammar is still `emma-nav:go:<place>:tap|voice`) and the server
#: never sends it (robot_face_link.NAV_ORIGINS stays "voice").
NAV_ORIGINS = ("tap", "voice", "auto")
#: ...plus "pc": a `robot_python run` the app's poller sees through run.pid. It
#: appears in a nav_status, never as a go origin.
NAV_STATUS_ORIGINS = NAV_ORIGINS + ("pc",)
NAV_ACTIVITY = ("idle", "active", "stale", "unknown")   # robot_face_link.nav_activity(), §2a
RUN_ID_RE = COMMAND_ID_RE                               # run ids are 32 hex, generated by the app (§2b)
INBOUND_TYPES = ("body_ack", "body_status", "body_caps", "nav_ack", "nav_status", "nav_caps")

#: Body gesture lifecycle states in `body_status` (§2a).
BODY_STATUS_STATES = ("started", "done", "stopped", "failed")

_ID = re.compile(ID_RE)
_COMMAND_ID = re.compile(COMMAND_ID_RE)


def valid_id(value: object) -> bool:
    """A body action or place id that may cross a boundary. `re.fullmatch`
    semantics: `$` alone would accept a trailing newline, which is exactly
    the kind of byte that turns an id into two shell lines."""
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def valid_command_id(value: object) -> bool:
    """32 lower-case hex, for command ids and run ids alike."""
    return isinstance(value, str) and _COMMAND_ID.fullmatch(value) is not None


valid_run_id = valid_command_id


def _is_thai(text: str) -> bool:
    return any("\u0e00" <= ch <= "\u0e7f" for ch in text)


def _splits_into(token: str, term: str, pieces: frozenset[str]) -> bool:
    """Can `token` be cut into pieces from `pieces`, one of them `term`?"""
    n = len(token)
    # reach[i] = (reachable, used_term) for token[:i]
    reach: list[set[bool]] = [set() for _ in range(n + 1)]
    reach[0].add(False)
    for i in range(n):
        if not reach[i]:
            continue
        for piece in pieces:
            if piece and token.startswith(piece, i):
                for used in reach[i]:
                    reach[i + len(piece)].add(used or piece == term)
    return True in reach[n]


def _term_starts(words: list[str], term: str, pieces: frozenset[str]) -> list[int]:
    """Indices of the words where `term` begins; empty when it is not there."""
    term = term.lower()
    if not _is_thai(term):
        # Latin: the term's own words, contiguous, whole ("stop" is not
        # "stopwatch", "look up" is two words in a row).
        parts = term.split()
        return [i for i in range(len(words) - len(parts) + 1)
                if words[i:i + len(parts)] == parts]
    out: list[int] = []
    for i in range(len(words)):
        joined = ""
        found = False
        for j in range(i, len(words)):
            joined += words[j]
            if joined == term:
                found = True         # one word, or a run of them ("มอง"+"ตรง")
                break
            if len(joined) >= len(term):
                break
        if not found and _is_thai(words[i]):
            found = _splits_into(words[i], term, pieces)   # glued ("ก้มหน้า", "แขนขวา")
        if found:
            out.append(i)
    return out


def _has_term(words: list[str], term: str, pieces: frozenset[str]) -> bool:
    return bool(_term_starts(words, term, pieces))


def _negated(words: list[str], at: int) -> bool:
    """A negator among the two words just before index `at` (review #22),
    not counting the want/ask/addressee words of BODY_HEARD_REACH in between
    (review 2 #12: "ไม่อยากให้ก้มหน้า", "I don't want you to nod")."""
    counted = 0
    i = at - 1
    while i >= 0 and counted < 2:
        if words[i] in BODY_HEARD_NEGATORS:
            return True
        if words[i] not in BODY_HEARD_REACH:
            counted += 1
        i -= 1
    return False


def _narrated(words: list[str], at: int) -> bool:
    """Is the word just before index `at` (past aspect words) a narrator?"""
    i = at - 1
    while i >= 0 and words[i] in _ASPECT:
        i -= 1
    return i >= 0 and words[i] in BODY_HEARD_NARRATORS


def strip_particles(words: list[str]) -> list[str]:
    """The words without trailing politeness particles."""
    end = len(words)
    while end > 0 and words[end - 1] in PARTICLES:
        end -= 1
    return words[:end]


def asks_yes_no(words: list[str]) -> bool:
    """Does an already tokenized, lower-cased caption ask a yes/no question
    rather than make a request? Shared shape with the place guard
    (heard.declines_or_asks_where); stdlib-only like the rest of this file."""
    core = strip_particles(words)
    if not core:
        return False
    if core[-1] in YES_NO_TAILS:
        return not (len(core) > 1 and core[-2] in YES_NO_ASKS_FOR_IT)
    return all(not _is_thai(w) for w in core) and core[0] in EN_QUESTION_STARTS


def _names_the_scene(words: list[str]) -> bool:
    for w in words:
        for term in BODY_HEARD_SCENE:
            if w == term or (_is_thai(term) and w.startswith(term)):
                return True
    return False


def _without_hand_sides(words: list[str]) -> list[str]:
    """The words with ซ้ายมือ/ขวามือ/"left hand side" read as a side only,
    for the body-noun check (the side check still sees them)."""
    out: list[str] = []
    for i, w in enumerate(words):
        idiom = next((t for t in _HAND_SIDE_TH if w.endswith(t)), None)
        if idiom is not None:
            out.append(w[:-len("มือ")])
            continue
        if (w == "มือ" and i > 0 and words[i - 1].endswith(("ซ้าย", "ขวา"))):
            continue
        if (w in ("hand", "hands") and i > 0 and words[i - 1] in ("left", "right")
                and i + 1 < len(words) and words[i + 1] == "side"):
            continue
        out.append(w)
    return out


def heard_matches(action: str, words: list[str]) -> bool:
    """Does a caption, already split by `retrieval.tokenize`, ask for `action`?

    One VERB of BODY_HEARD[action] with no negator in the two words before
    it (BODY_HEARD_REACH words not counted) and no narrator just before it,
    for a sided action one SIDE, and for an action in BODY_HEARD_NOUNS
    one body NOUN unless the verb said is one of its free verbs — each as a
    whole word, a run of whole words, or a compound that splits completely
    into it plus BODY_HEARD_FILLERS / BODY_HEARD_GLUE / the action's own
    terms. Never a substring. A caption that is a yes/no question
    (`asks_yes_no`) asks for nothing. ๆ is dropped before matching.
    `stop` is never guarded, so it always matches; an unknown action never
    does.

    Pure and stdlib-only so the guard's rule lives with its table; the caller
    (robot_body, P1) supplies the tokens, because the tokenizer is not
    available where this module also runs (the robot).
    """
    if action == "stop":
        return True
    entry = BODY_HEARD.get(action)
    if entry is None:
        return False
    verbs, sides = entry
    lowered = [w.lower().replace(_MAI_YAMOK, "") for w in words or []
               if isinstance(w, str) and w.strip()]
    lowered = [w for w in lowered if w.strip()]
    if not lowered or asks_yes_no(lowered):
        return False
    pieces = (frozenset(BODY_HEARD_FILLERS) | set(BODY_HEARD_GLUE.get(action, ()))
              | {t for t in verbs if _is_thai(t)} | {t for t in (sides or ()) if _is_thai(t)})
    narration_ok = action in _NARRATION_OK
    # The verbs said without a negator just before them (review #22), and
    # not as somebody else's action (review 2 #12).
    said = [v for v in verbs
            if any(not _negated(lowered, i) and (narration_ok or not _narrated(lowered, i))
                   for i in _term_starts(lowered, v, pieces))]
    if not said:
        return False
    if sides is not None and not any(_has_term(lowered, s, pieces) for s in sides):
        return False
    if sides is not None and _names_the_scene(lowered):
        return False                 # directions about the room (BODY_HEARD_SCENE)
    rule = BODY_HEARD_NOUNS.get(action)
    if rule is None:
        return True
    nouns, free = rule
    if any(v in free for v in said):
        return True
    # The noun may be glued to another gesture verb ("ยกมือ" before "ทักทาย"),
    # so every Thai verb of the table is a piece here — still a *complete*
    # split, so "มือถือ" and "หน้าต่าง" stay out.
    noun_pieces = pieces | {t for vs, _ in BODY_HEARD.values() for t in vs if _is_thai(t)} | {
        n for n in nouns if _is_thai(n)}
    plain = _without_hand_sides(lowered)
    return any(_has_term(plain, n, noun_pieces) for n in nouns)


def body_reason_th(code: str | None) -> str:
    """Thai text for a body refusal code; an unknown code reads as a bad
    request rather than being spoken raw (a code name in a customer's ear)."""
    return BODY_REASONS.get(code or "", BODY_REASONS["bad_request"])


def nav_reason_th(code: str | None) -> str:
    return NAV_REASONS.get(code or "", NAV_REASONS["bad_request"])
