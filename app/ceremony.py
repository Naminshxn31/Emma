"""Emma Ceremony -- the opening ceremony as a robot app of its own.

Owner 9 Nov 2026: "อันนี้ทำแยกกับอันเดิมนะ ไม่งั้นจะปนกัน อันนี้จะเป็นสำหรับพิธี ที่สามารถใช้ข้อมูล
อันเก่าได้". Asked, he chose a THIRD app, "Emma Ceremony" (`gradlew assembleDebug
-PemmaApp=ceremony`, com.emma.robot.ceremony), with its own staff page (/ceremony-control on
the event listener, the same EVENT_CONTROL_KEY) and its own script and running order
(CEREMONY_FILE, app/event_script.py); Emma Event keeps only its own scenes. The ceremony
shares the old data: the approved project facts, stories and report (`ceremony` is a sales
profile, app/prompts.py), the floor-1 map and the event file's walk spots and films (the
order names them by id), the big screen, the registered guests.

**The mode is the app.** On exactly while the active call is the ceremony app's robot face
(profile `ceremony`): claimed when that call starts (`claim`, from session.run at the face's
claim -- before the dial, so a call that opens mid-ceremony never greets the room), off when it
ends (`release`) or another call takes the robot: the Event app's face, a desk tab. `on()`
compares with session._active, so a takeover turns it off at once, before the old call's
cleanup has run. Nothing is kept on disk and nothing times it out. Until 9 Nov a ceremony was a
SCENE of the event file (`ceremony_start`), kept in scene.json across restarts and ended by
EVENT_CEREMONY_MAX_S because that file outlived the event; an app being open does not.

Asked on every audio frame, every camera frame and every reply (app/event_host.py
`ceremony_now` and the probes beside it, which callers and tests already use): no file is read
here per frame -- `replies` comes from CEREMONY_FILE once, at the claim.

What the mode does is what the ceremony scene did (5-7 Nov): the camera greets nobody, she asks
nothing, the call is not hung up, and with `"replies": "cues"` (the file's default) she speaks
only the lines staff press -- the live window below is the one exception; `"named"` is the
older ceremony that answers when the host says her name. What it keeps OUT, the "ไม่ปน" half:
nothing of the event host runs while it is on (event_host `_say`, the roam, walking up to
guests -- all three stopped at the claim too, `_quiet_the_event`), and the Event page's presses
that drive her are refused (app/event_control_server.py); the red stop works from either page.

The live window (moved here from event_host, 9 Nov; the order's s4_recommend step): staff open
it ahead of the host's question, she answers when named, the cue's approved brief rides beside
tool results (`live_note`, registry.model_view); "ถัดไป", the red stop, the app closing and its
own end (EVENT_LIVE_MAX_S) shut it.

"ตอบสด" (owner 23 Nov, `adhoc_open`): the same window opened from the ceremony page's own button
at any moment -- the host off-script -- with an id and a brief of its own (ADHOC_ID, ADHOC_BRIEF_TH:
fixed here, never words from the phone). Every press of the script closes it first (the staff
server's `_back_to_script`); see the section below.

The handoff (the order's last step, `{"do": "handoff", "to": "event"}`): the server asks the
ceremony face to open the Emma Event app -- `{"type": "open_app", "app": "event"}` -- and waits
HANDOFF_WAIT_S for the page's `{"type": "open_app_result", "app": "event", "ok", "reason"}`
(session's receive loop -> `open_app_result`). Only ever to the ceremony app's own face, only
"event": a voice cannot ask for it, and no other app is named anywhere.

Nothing here has run on the robot yet.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import time

from app import turnlog
from app.config import settings

logger = logging.getLogger("condo_voice.ceremony")

#: The profile the Emma Ceremony app's face opens with (?profile=ceremony).
PROFILE = "ceremony"
#: `replies` in CEREMONY_FILE: "cues" (only the lines staff press) or "named" (answers when the
#: host says her name -- the ceremony of 5-6 Nov). Absent or anything else = "cues".
REPLIES = ("cues", "named")
#: The apps a handoff may open. One: the ceremony ends into the event's after-part.
APPS = ("event",)
#: How long the handoff waits for the robot face's answer (the bridge starts an activity: a
#: second at most when it works; past this the staff phone says to open it by hand).
HANDOFF_WAIT_S = 5.0
HANDOFF_FAILED_TH = "เปิดแอป Emma Event ไม่ได้ — เปิดเองบนหุ่น"
#: Why a handoff failed, for the staff phone (the page's own reason is shown as it came).
HANDOFF_WHY_TH = {
    "no_answer": "หน้าหุ่นไม่ตอบใน 5 วินาที",
    "not_sent": "ส่งคำสั่งให้หน้าหุ่นไม่ได้",
    "closed": "หน้าหุ่นปิดไปก่อนตอบ ดูที่หุ่นว่าแอป Emma Event ขึ้นแล้วหรือยัง",
    "replaced": "มีคำสั่งเปิดแอปใหม่มาแทน",
}
NOT_APP = "not_ceremony_app"
NOT_APP_TH = "เปิดแอป Emma Ceremony บนหุ่นก่อน"

#: Beside tool results while the live window is open: the cue's own brief (its approved
#: `live_th`), by id. No sentence of hers is quoted -- a quoted sentence is one she says (CLAUDE.md).
LIVE_NOTE = "ช่วงตอบสดของพิธี (คิว %s · ทีมงานเปิดให้ตอบเอง เมื่อพิธีกรเรียกชื่อ): %s"
#: The same as context when the window opens (EVENT_CUE_CONTEXT_NOTE only, off: never measured
#: live -- the cue note's reason).
LIVE_CONTEXT = "[ทีมงานเปิดช่วงตอบสดของพิธี — ข้อความระบบ ไม่ใช่คำพูดแขก ไม่ต้องตอบ ไม่ต้องพูดอะไร] %s"

#: "ตอบสด" (owner 23 Nov, `adhoc_open`): the window's id -- not a cue of the script (the order's own
#: windows carry their cue's id; tests/test_ceremony_adhoc.py holds the script to never use this one).
ADHOC_ID = "adhoc"
#: ...its note beside tool results: no cue id to name, the reason instead.
ADHOC_NOTE = "ช่วงตอบสดของพิธี (พิธีกรพูดนอกบท · ทีมงานเปิดให้ตอบเอง เมื่อพิธีกรเรียกชื่อ): %s"
#: ...and its brief, written HERE and nowhere else: the phone sends {open}, never words (the
#: `show_unit(room)` rule -- a staff phone is not a way to put words in her mouth). Instructions only,
#: no sentence of hers quoted (a quoted sentence is one she says, CLAUDE.md 27 Oct); no number (the
#: price rules are the system prompt's, already there for every turn).
ADHOC_BRIEF_TH = ("พิธีกรพูดนอกบท ทีมงานเปิดให้คุณตอบเขาเองชั่วคราว ตอบเฉพาะสิ่งที่เขาถามหรือพูดกับคุณ "
                  "สั้นๆ หนึ่งหรือสองประโยคแล้วหยุด ไม่ถามกลับ ไม่ชวนคุยต่อ ตอบเป็นภาษาที่เขาพูดกับคุณ "
                  "(พิธีนี้ใช้ภาษาอังกฤษ) เรื่องโครงการตอบจากข้อมูลที่อนุมัติเท่านั้น ไม่แน่ใจให้ค้นก่อนตอบ "
                  "คำชมหรือคำถามเล่นๆ บนเวทีตอบรับสั้นๆ อย่างเป็นกันเองได้ "
                  "เรื่องราคาพูดได้เท่าที่กฎราคาข้างบนอนุญาต ห้ามแนะนำตัวใหม่ (แนะนำตัวในพิธีไปแล้ว) "
                  "ถ้าเขาขอให้ทักทายแขกให้ทักสั้นๆ ได้ ห้ามพูดถึงบทพิธี ทีมงาน ปุ่ม หรือช่วงตอบสดนี้")
#: ...and the one rule of hers it needs in the SYSTEM PROMPT (event_host.ceremony_block; owner 23 Nov, after
#: the live check): the brief above reaches the model only beside a tool result, and banter calls no tool --
#: measured, "how are you feeling?" got the sales room's "I only handle information about the project ...
#: would you like to know about our room types?" and answers ran 18-27 s. Worded for her live answers only:
#: the lines staff press for her to read (event_cues read mode) are said whole, as before -- re-measured.
#: Instructions only, no sentence of hers quoted (CLAUDE.md 27 Oct), no number.
ADHOC_PROMPT_TH = ("คำตอบสดของคุณระหว่างพิธี (เมื่อพิธีกรพูดกับคุณนอกบท): ตอบหนึ่งหรือสองประโยคสั้นๆ แล้วหยุด "
                   "ข้อนี้ใช้แทนกฎความยาวและคำถามท้ายคำตอบของห้องขาย "
                   "คำทักทาย คำชม หรือคำถามเล่นๆ ของพิธีกรบนเวทีตอบรับอย่างเป็นกันเองได้ "
                   "ไม่ต้องบอกว่าตอบได้แค่เรื่องโครงการ เรื่องที่ไม่รู้ (เช่นอากาศวันนี้) ตอบรับสั้นๆ โดยไม่แต่งข้อมูล "
                   "ไม่ชวนดูชั้นหรือห้อง ไม่ถามกลับ ไม่แนะนำตัวใหม่ (แนะนำตัวในพิธีไปแล้ว) "
                   "ไม่พูดถึงบทพิธี ทีมงานที่คุมพิธี ปุ่ม หรือช่วงตอบสด "
                   "ข้อนี้ไม่ใช้กับบทพิธีที่ทีมงานกดให้อ่าน บทที่อ่านให้อ่านครบทุกคำตามเดิม")
#: "ทั้งพิธีคุยสด" (owner 24 Nov, app/ceremony_follow.py): while follow mode is on the same window stays open for
#: the rest of the ceremony (no 2-minute cap) and the brief gains this. Instructions only, no sentence of hers
#: quoted (CLAUDE.md 27 Oct), no number. Its job: her live answer must not run ahead onto the lines the server
#: plays by itself when the host reads his script.
FOLLOW_BRIEF_TH = (" ช่วงนี้ระบบเดินตามบทของพิธีกรเอง: เมื่อเขาพูดตามบท ระบบจะเล่นบทของคุณให้เอง ไม่ต้องรีบตอบก่อน "
                   "ตอบสดเฉพาะเมื่อเขาเรียกชื่อคุณและพูดนอกบท ตอบเฉพาะสิ่งที่ถูกถามเท่านั้น "
                   "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธี (ทำเล ห้องพัก สิ่งอำนวยความสะดวก เทคโนโลยี สมาร์ทโฮม) "
                   "เกินกว่าที่ถูกถาม")
#: ...and the one rule of the same kind in the SYSTEM PROMPT, only when CEREMONY_FOLLOW_HOST is on
#: (event_host.ceremony_block) -- the brief reaches the model only beside a tool result.
FOLLOW_PROMPT_TH = ("ถ้าพิธีกรพูดตามบทของพิธี ระบบจะเล่นบทของคุณให้เอง ถ้าคำตอบสดของคุณเริ่มก่อน ระบบจะตัดทิ้ง "
                    "ตอบสดเฉพาะเมื่อเขาเรียกชื่อคุณและพูดนอกบท ตอบเฉพาะที่ถูกถาม "
                    "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธีเกินกว่าที่ถูกถาม")
#: CEREMONY_FOLLOW_ANSWER_ALL (owner 24 Nov 11:00, "ให้หุ่นสามารถตอบได้แม้จะไม่ตรงกับบทมาก"): the same two rules without
#: "only when he says your name" -- she answers whatever he says to her, and says as little as possible when he is
#: only talking to the guests (a bare acknowledgement is dropped by the server: SILENCE_BARE_ACK).
FOLLOW_BRIEF_ALL_TH = (" ช่วงนี้ระบบเดินตามบทของพิธีกรเอง: เมื่อเขาพูดตามบท ระบบจะเล่นบทของคุณให้เอง ไม่ต้องรีบตอบก่อน "
                       "ถ้าเขาพูดกับคุณหรือถามคุณนอกบท ตอบสดสั้นๆ ตอบเฉพาะสิ่งที่ถูกถามเท่านั้น (ไม่ต้องรอให้เรียกชื่อ) "
                       "ถ้าเขาพูดกับแขกหรือพูดกับห้อง ไม่ได้ถามคุณ ให้ตอบรับสั้นที่สุดหรือเงียบ "
                       "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธี (ทำเล ห้องพัก สิ่งอำนวยความสะดวก เทคโนโลยี สมาร์ทโฮม) "
                       "เกินกว่าที่ถูกถาม")
FOLLOW_PROMPT_ALL_TH = ("ถ้าพิธีกรพูดตามบทของพิธี ระบบจะเล่นบทของคุณให้เอง ถ้าคำตอบสดของคุณเริ่มก่อน ระบบจะตัดทิ้ง "
                        "ถ้าเขาพูดกับคุณหรือถามคุณนอกบท ตอบสดสั้นๆ ได้เลยโดยไม่ต้องรอให้เรียกชื่อ ตอบเฉพาะที่ถูกถาม "
                        "ถ้าเขาพูดกับแขกหรือกับห้อง ไม่ได้ถามคุณ ให้ตอบรับสั้นที่สุดหรือเงียบ "
                        "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธีเกินกว่าที่ถูกถาม")


#: CEREMONY_FOLLOW_CUES=false (owner 24 Nov ~11:40, "อยากให้มันตอบสดเลย ไม่ต้องมากดตามบท"): nothing plays her lines for
#: her -- she answers the host live EVERY time, never silent. A lead-in of the script (in her context) gets roughly the
#: scripted answer in her own words. Instructions only, no sentence of hers quoted (CLAUDE.md 27 Oct), no number.
#: "ปรับสคริปให้มีบทสั้นด้วย แล้วเวลามิกิพูดบางทีอาจจะพูดยาวแล้วค่อยมาถามเกี่ยวกับบทด้วย" (owner 24 Nov ~12:15): her live answer to a
#: lead-in is the SHORT form of the scripted line (1-3 short sentences, about 20-35 words, the key facts and the script's own figures
#: only) -- the signed lines stay as they are -- and while the host talks to the guests (a speech, a joke, a story) she waits for
#: the question to her and answers that, not each pause. Instructions only, no sentence of hers quoted (CLAUDE.md 27 Oct), no number
#: of the project.
FOLLOW_BRIEF_LIVE_TH = (" ช่วงนี้คุณตอบสดทุกครั้งที่พิธีกรถามคุณหรือส่งมือให้คุณ ไม่ต้องรอให้เรียกชื่อ ไม่มีระบบเล่นบทให้ "
                        "ถ้าเขานำเข้าสู่เรื่องที่อยู่ในบทพิธีที่คุณมีในบริบท ให้ตอบเรื่องนั้นเองด้วยคำของคุณแบบย่อของบท: "
                        "หนึ่งถึงสามประโยคสั้นๆ ราว 20-35 คำ เอาแค่ประเด็นหลักและตัวเลขของบทนั้น (ใช้แทนข้อสั้นๆ ข้างบน) "
                        "ใช้เฉพาะตัวเลขที่อยู่ในบทหรือข้อมูลโครงการ ไม่ต้องเล่าครบทุกท่อนของบท "
                        "ถ้าเขากำลังพูดกับแขก เล่ามุก หรือเล่าเรื่องยาวอยู่ และยังไม่ได้ถามคุณ ให้เงียบหรือรับสั้นที่สุด "
                        "รอจนเขาถามหรือส่งมือให้คุณจึงตอบ ถ้าเขาพูดยาวแล้วถามเรื่องในบท ตอบเฉพาะคำถามท้ายนั้น "
                        "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปเกินกว่าที่ถูกถาม")
FOLLOW_PROMPT_LIVE_TH = ("ระหว่างพิธีนี้คุณตอบสดทุกครั้งที่พิธีกรถามคุณหรือส่งมือให้คุณ โดยไม่ต้องรอให้เรียกชื่อ "
                         "ไม่มีระบบเล่นบทให้ ถ้าเขานำเข้าสู่เรื่องที่อยู่ในบทพิธี ให้ตอบเรื่องนั้นเองด้วยคำของคุณแบบย่อของบท: "
                         "หนึ่งถึงสามประโยคสั้นๆ ราว 20-35 คำ ประเด็นหลักและตัวเลขของบทนั้นเท่านั้น ข้อนี้ใช้แทนกฎความยาวข้างบน "
                         "ถ้าเขากำลังพูดกับแขก เล่ามุก หรือเล่าเรื่องยาวอยู่ และยังไม่ได้ถามคุณ ให้เงียบหรือรับสั้นที่สุด "
                         "รอจนเขาถามหรือส่งมือให้คุณ ถ้าเขาพูดยาวแล้วถามเรื่องในบท ตอบเฉพาะคำถามท้ายนั้น "
                         "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธีเกินกว่าที่ถูกถาม")


#: CEREMONY_FREE_TALK (owner 24 Nov ~12:40, "สุดท้ายก็คุยด้นสด"): the live-first wording WITHOUT the script's short form -- nothing
#: of the script is quoted or asked for; she just answers him, briefly, in English. The source order, the script's facts and
#: the common-area rule are separate blocks and stay. Instructions only, no sentence of hers quoted (CLAUDE.md 27 Oct), no number.
FOLLOW_BRIEF_FREE_TH = (" ช่วงนี้คุณคุยสดกับพิธีกร ตอบทุกครั้งที่เขาถามคุณหรือส่งมือให้คุณ ไม่ต้องรอให้เรียกชื่อ ไม่มีระบบเล่นบทให้ "
                        "ตอบอย่างอิสระและสั้น หนึ่งถึงสองประโยค เป็นภาษาอังกฤษ ตอบเฉพาะที่ถูกถามด้วยคำของคุณเอง "
                        "ใช้เฉพาะตัวเลขที่อยู่ในข้อมูลโครงการหรือข้อเท็จจริงของบทพิธี "
                        "ถ้าเขากำลังพูดกับแขก เล่ามุก หรือเล่าเรื่องยาวอยู่ และยังไม่ได้ถามคุณ ให้เงียบหรือรับสั้นที่สุด "
                        "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธีเกินกว่าที่ถูกถาม")
FOLLOW_PROMPT_FREE_TH = ("ระหว่างพิธีนี้คุณคุยสดกับพิธีกร ตอบทุกครั้งที่เขาถามคุณหรือส่งมือให้คุณ โดยไม่ต้องรอให้เรียกชื่อ "
                         "ไม่มีระบบเล่นบทให้ ตอบอย่างอิสระและสั้น หนึ่งถึงสองประโยค เป็นภาษาอังกฤษ ตอบเฉพาะที่ถูกถามด้วยคำของคุณเอง "
                         "ถ้าเขากำลังพูดกับแขก เล่ามุก หรือเล่าเรื่องยาวอยู่ และยังไม่ได้ถามคุณ ให้เงียบหรือรับสั้นที่สุด "
                         "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธีเกินกว่าที่ถูกถาม")
#: ... and with the script in her context (CEREMONY_FOLLOW_SCRIPT_PROMPT, owner 24 Nov ~13:40 after the rehearsal answered
#: "what can you do?" with the robot's body: "ให้มันตรงกับโครงการหรือบทที่เรามีด้วยสิ" / "เอาของสคริปมาเป็นคำถามได้หนิ"): his
#: question near a question of the script = that line's content, short; off the script = her own words from the project data.
FOLLOW_BRIEF_FREE_SCRIPT_TH = (" ช่วงนี้คุณคุยสดกับพิธีกร ตอบทุกครั้งที่เขาถามคุณหรือส่งมือให้คุณ ไม่ต้องรอให้เรียกชื่อ ไม่มีระบบเล่นบทให้ "
                               "ถ้าคำถามของเขาตรงหรือใกล้กับคำถามในบทพิธี (ไม่ต้องตรงคำ) ให้ตอบด้วยเนื้อหาของคำตอบในบทนั้นแบบสั้น "
                               "ใช้ข้อเท็จจริงและตัวเลขของบทนั้น เรื่องที่ไม่มีในบทตอบสั้นๆ ด้วยคำของคุณเองจากข้อมูลโครงการ "
                               "เป็นภาษาอังกฤษ ห้ามตอบเรื่องความสามารถทั่วไปของหุ่นแทนเนื้อหาของบท "
                               "ถ้าเขากำลังพูดกับแขก เล่ามุก หรือเล่าเรื่องยาวอยู่ และยังไม่ได้ถามคุณ ให้เงียบหรือรับสั้นที่สุด "
                               "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธีเกินกว่าที่ถูกถาม")
FOLLOW_PROMPT_FREE_SCRIPT_TH = ("ระหว่างพิธีนี้คุณคุยสดกับพิธีกร ตอบทุกครั้งที่เขาถามคุณหรือส่งมือให้คุณ โดยไม่ต้องรอให้เรียกชื่อ "
                                "ไม่มีระบบเล่นบทให้ ถ้าคำถามของเขาตรงหรือใกล้กับคำถามในบทพิธีข้างล่าง ให้ตอบด้วยเนื้อหาของคำตอบในบทนั้นแบบสั้น "
                                "เรื่องที่ไม่มีในบทตอบสั้นๆ ด้วยคำของคุณเองจากข้อมูลโครงการ เป็นภาษาอังกฤษ "
                                "ถ้าเขากำลังพูดกับแขก เล่ามุก หรือเล่าเรื่องยาวอยู่ และยังไม่ได้ถามคุณ ให้เงียบหรือรับสั้นที่สุด "
                                "ห้ามเล่าล่วงหน้าไปถึงหัวข้อถัดไปของพิธีเกินกว่าที่ถูกถาม")


def _free_script() -> bool:
    return bool(settings.ceremony_follow_script_prompt)


def live_first() -> bool:
    """Follow mode with CEREMONY_FOLLOW_CUES off: she answers live, nothing is pressed for her."""
    return bool(settings.ceremony_follow_host and not settings.ceremony_follow_cues)


def free_talk() -> bool:
    """CEREMONY_FREE_TALK on top of live first: no script line in her context, no nudge, no joke auto-play."""
    return bool(live_first() and settings.ceremony_free_talk)


def follow_brief_th() -> str:
    if free_talk():
        return FOLLOW_BRIEF_FREE_SCRIPT_TH if _free_script() else FOLLOW_BRIEF_FREE_TH
    if live_first():
        return FOLLOW_BRIEF_LIVE_TH
    return FOLLOW_BRIEF_ALL_TH if settings.ceremony_follow_answer_all else FOLLOW_BRIEF_TH


def follow_prompt_th() -> str:
    if free_talk():
        return FOLLOW_PROMPT_FREE_SCRIPT_TH if _free_script() else FOLLOW_PROMPT_FREE_TH
    if live_first():
        return FOLLOW_PROMPT_LIVE_TH
    return FOLLOW_PROMPT_ALL_TH if settings.ceremony_follow_answer_all else FOLLOW_PROMPT_TH


#: The tools that drive the room's big screen (a film, the screen itself): not from "ตอบสด" -- a window
#: that opens at any moment must not let "Emma, play the video" roll a film over the screen team's
#: show (review 23 Nov). Her words, a search, a picture of hers and the stops stay. registry._dispatch.
ADHOC_NO_TOOLS = frozenset({"event_clip", "room_screen"})
#: ...what the model is told then: no sentence of hers quoted.
ADHOC_TOOL_REFUSAL = ("ช่วงตอบสดนี้ห้ามเปิดหรือปิดคลิปและจอใหญ่ ตอบเป็นคำพูดสั้นๆ แทน "
                      "โดยไม่บอกว่าเปิดหรือปิดให้แล้ว")

#: `session`: the ceremony app's call that claimed the mode (on only while it is also the active
#: call). `replies`: read at the claim. `live`: the open live window ({"cue", "brief",
#: "opened_at", "until"}). `handoff`: the open_app request waiting for its answer. `adhoc_closed`:
#: how the last "ตอบสด" window ended ({"why", "at", "max_s"}), for the page (`adhoc_snapshot`).
STATE: dict = {"session": None, "since": None, "replies": None, "live": None, "handoff": None,
               "adhoc_closed": None, "script_presses": 0, "adhoc_picture": False, "adhoc_recent": False,
               "adhoc_pressed": False}
_tasks: set[asyncio.Task] = set()


def reset() -> None:
    STATE.update(session=None, since=None, replies=None, live=None, handoff=None, adhoc_closed=None,
                 script_presses=0, adhoc_picture=False, adhoc_recent=False, adhoc_pressed=False)
    _SERVED.update(at=0.0, doc=None, map={})


def _spawn(coro) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        coro.close()                                     # no loop (a script): nothing to stop
        return
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---- the mode -----------------------------------------------------------------------------

def _active_call():
    from app import session as session_module

    return session_module._active


def active_session():
    """The ceremony app's call while the mode is on, else None."""
    session = STATE["session"]
    if session is None or _active_call() is not session:
        return None
    return session


def on() -> bool:
    """The Emma Ceremony app is on the robot: its face's call is the active call."""
    return active_session() is not None


def replies() -> str | None:
    """"cues" / "named" while on, None off."""
    return STATE["replies"] if on() else None


def file_replies(doc: dict | None = None) -> str:
    """CEREMONY_FILE's `replies` ("cues" when the file says nothing, or there is no file: a
    ceremony app that cannot read its script is a quiet one, not one that answers the room)."""
    if doc is None:
        from app import event_script

        try:
            doc = event_script.load()
        except Exception:                                # noqa: BLE001 -- the quiet default
            logger.exception("ceremony: the script file")
            doc = None
    value = (doc or {}).get("replies")
    return value if value in REPLIES else "cues"


def claim(session, *, replies: str | None = None) -> bool:
    """A call of the ceremony app's face started (session.run, at robot_face_link.claim). False =
    not one (another profile, nothing done). `replies` overrides the file's (tests, the eval)."""
    if session is None or getattr(session, "profile", None) != PROFILE:
        return False
    old = STATE["session"]
    if old is not None and old is not session:
        _off(old, "replaced")
    STATE.update(session=session, since=time.time(),
                 replies=replies if replies in REPLIES else file_replies(),
                 # a new call: nothing of the last one's ตอบสด picture is on its face (review round 2)
                 adhoc_picture=False, adhoc_recent=False, adhoc_pressed=False)
    turnlog.record("ceremony_app", action="on", replies=STATE["replies"])
    _spawn(_quiet_the_event())
    return True


def release(session, why: str = "ended") -> None:
    """That call is over (its cleanup in session.handle_connection, or a newer call took the
    robot). Another session = nothing done: a superseded call closing late must not end the mode
    of the call that replaced it."""
    if session is None or STATE["session"] is not session:
        return
    _off(session, why)


def _off(session, why: str) -> None:
    STATE.update(session=None, since=None, replies=None, adhoc_picture=False, adhoc_recent=False,
                 adhoc_pressed=False)
    live_close("app_closed")
    _fail_handoff(session, "closed")
    turnlog.record("ceremony_app", action="off", why=str(why or "ended")[:24])


def _ceremony_walk(run) -> bool:
    """A walk the ceremony's running order started (its mark holds the run id): a call that
    re-dials mid-walk (the network) must not stop its own ceremony's walk."""
    try:
        from app import event_order, event_tour

        if event_tour.owns_walk(run):
            return True                                  # the opening's own (card 1 in the ceremony, owner 23 Nov)
        marks = list(event_order.STATE["marks"].values())
        if any(m.get("run") == getattr(run, "id", None) for m in marks):
            return True
        # a walk step still on its way to its run id (event_order._dispatch `walk_pending`): the
        # only walk that can be "starting" then is the one it is starting
        return getattr(run, "state", None) == "starting" and any(m.get("walk_pending") for m in marks)
    except Exception:                                    # noqa: BLE001 -- unknown = the event's: stopped
        return False


async def _quiet_the_event() -> None:
    """The claim: the event host's own machinery stops -- the roam (it would walk on to its next
    stop through the ceremony), walking up to guests (staff switched it on for the after-party),
    a scene's walk under way. Each whatever the others do."""
    from app import event_roam, event_walk, face_approach

    try:
        await event_roam.stop("ceremony_app")
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony: stopping the roam")
    try:
        face_approach._turn_off("ceremony_app")
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony: turning walking up off")
    try:
        run = event_walk.current()
        if run is not None and run.state in ("starting", "walking") and not _ceremony_walk(run):
            await event_walk.stop("replaced")
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony: stopping the event's walk")


def refusal() -> dict:
    """What an order step (or a cue press) answers with the ceremony app not on the robot."""
    return {"ok": False, "error": NOT_APP, "error_th": NOT_APP_TH}


# ---- cue-only, and its one live window ----------------------------------------------------
# Review of the running order, 7 Nov: with her name the only key, she answered live whenever the
# host said "เอ็มม่า" -- nearly every line of his -- and then said the line staff pressed too; her
# live answer ran tools (the film, a story, a gesture); applause cut her line as a barge-in.
# Cue-only closes all of that: the mic goes up as silence (providers `send_audio`, via
# event_host.mic_deaf) except while the robot moves (a spoken "หยุด" must reach the stop tools:
# then every reply that is not a server turn is dropped and every tool but the stops refused),
# and the one live step is a window staff open.

def cue_only() -> bool:
    """A cue-only ceremony is running: she speaks only the lines staff press."""
    return STATE["replies"] == "cues" and on()


def _live_now(now: float) -> dict | None:
    """The open live window, closed here once it is past its end or the app is gone."""
    live = STATE["live"]
    if live is None:
        return None
    if now >= live["until"] or not on():
        live_close("expired" if now >= live["until"] else "ceremony_over")
        return None
    return live


def cue_only_closed() -> bool:
    """cue_only() with no live window open: every reply that is not a server turn is dropped,
    every tool but a stop refused -- and, the robot still, the mic is deaf."""
    return cue_only() and _live_now(time.time()) is None


def live_open(*, cue_id: str, brief_th: str | None, max_s: float | None = None) -> bool:
    """Staff pressed the ceremony's live step (a cue with only a `live_th`, ahead of the host's
    question): until `live_close` or `max_s` (EVENT_LIVE_MAX_S) she listens as in the named-only
    ceremony -- answers when named, tools allowed -- and the cue's brief rides beside tool results
    (`live_note`), the proven channel; as a context note too only with EVENT_CUE_CONTEXT_NOTE on.
    A newer window replaces an open one. False = the ceremony app is not on (nothing opened)."""
    cue = str(cue_id or "").strip()[:48] or "?"
    if not on():
        turnlog.record("event_live", action="refused", cue=cue, why="no_ceremony")
        return False
    span = settings.event_live_max_s if max_s is None else max_s
    try:
        span = float(span)
    except (TypeError, ValueError):
        span = float(settings.event_live_max_s)
    if not math.isfinite(span) or span <= 0:
        span = float(settings.event_live_max_s)
    span = min(span, float(settings.event_ceremony_max_s))
    brief = " ".join(str(brief_th or "").split())[:1200] or None   # (600 until 24 Nov: the follow window's brief is longer)
    if STATE["live"] is not None:
        live_close("replaced")
    now = time.time()
    STATE["live"] = {"cue": cue, "brief": brief, "opened_at": now, "until": now + span}
    STATE["adhoc_recent"] = cue == ADHOC_ID              # whose live answers' pictures are ตอบสด's
    turnlog.record("event_live", action="open", cue=cue, max_s=round(span, 1), brief=brief is not None)
    if settings.event_cue_context_note and brief:
        _spawn(_live_context(cue, _note(STATE["live"])))
    return True


def live_close(why: str) -> None:
    """The live window is over ("ถัดไป", the red stop, the app closing, its end). Nothing open =
    nothing done."""
    live = STATE["live"]
    if live is None:
        return
    STATE["live"] = None
    why = why.strip()[:24] if isinstance(why, str) and why.strip() else "staff"
    now = time.time()
    if live["cue"] == ADHOC_ID:
        # the page says how it ended (its own end above all: the host may still be talking)
        STATE["adhoc_closed"] = {"why": why, "at": min(now, live["until"]),
                                 "max_s": round(live["until"] - live["opened_at"], 1)}
    turnlog.record("event_live", action="close", cue=live["cue"], why=why,
                   open_s=round(max(0.0, min(now, live["until"]) - live["opened_at"]), 1))


def live_state() -> dict | None:
    """The open live window, for the staff page: {"cue", "opened_at", "until" (wall-clock
    seconds), "why": None (it is open)}; None = none open (closed, expired, or no ceremony)."""
    live = _live_now(time.time())
    if live is None:
        return None
    return {"cue": live["cue"], "opened_at": live["opened_at"], "until": live["until"], "why": None}


def _note(live: dict) -> str:
    """The window's note for the model: the order's cue by id, or "ตอบสด"'s own words."""
    if live["cue"] == ADHOC_ID:
        return ADHOC_NOTE % (live["brief"] or "")
    return LIVE_NOTE % (live["cue"], live["brief"] or "")


def live_note() -> str | None:
    """Beside tool results while the live window is open (registry `event_live`)."""
    live = _live_now(time.time())
    if live is None:
        return None
    return _note(live).rstrip(": ")


async def _live_context(cue: str, note: str) -> None:
    session = active_session()
    provider = getattr(session, "provider", None) if session is not None else None
    send = getattr(provider, "send_context_note", None)
    if not callable(send):
        return
    try:
        sent = bool(await send(LIVE_CONTEXT % note))
    except Exception:                                    # noqa: BLE001 -- context, not the ceremony
        logger.exception("ceremony live window: the context note")
        sent = False
    turnlog.record("event_live_note", cue=cue, sent=sent)


# ---- "ตอบสด": the host off-script (owner 23 Nov) --------------------------------------------
# Owner 23 Nov 2026: "วันจริงมิกิอาจจะด้นสดบ้าง ... เขาอาจจะนอกบทแล้วสลับไปมาได้" -> "ตอบสดทำเลย ยังไงก็
# ทำไปก่อนไม่เวิร์คค่อยไปใช่แบบกด". Measured 7 Nov: free live chat 76% on-script, the model picking the
# cue 68%, pressed cues 33/33 -- so STAFF decide when it is live, never the model. The scripted lines
# stay pressed (recorded voice); this is the one live window that is not a step of the running order:
# * opened from the ceremony page's own button at any moment, in either page mode -- not a step, so
#   nothing is added to the order (its digest, its approval) -- with an id of its own (ADHOC_ID) and the
#   fixed ADHOC_BRIEF_TH: never words from the phone;
# * what she hears is the live window's, nothing widened: the named-only door (she answers when the
#   host says her name, or a named exchange 30 s before -- NoiseReplyGate), tools allowed, the brief
#   beside tool results (`live_note`). And nothing said BEFORE the tap: a cue-only ceremony sends
#   silence up until a window opens (providers `send_audio`) -- staff open it as the host goes
#   off-script, before he turns to her; if he named her before the tap, he asks again;
# * back to the script: every press of the running order or a cue closes it first (the staff
#   server's `_back_to_script`: a line pressed over an open window is answered live AND said), the red
#   stop closes it, the app closing does, and its own end (CEREMONY_ADHOC_MAX_S under
#   EVENT_CEREMONY_MAX_S) -- said on the page (`adhoc_snapshot`'s `closed`), the host may still be
#   talking;
# * refused, with the reason staff read (`_adhoc_refusal`): the ceremony app not the robot's call, a
#   ceremony that answers by name anyway ("named"), the order's own live step open, the opening
#   running, the robot walking or moving, a line being said or still heard (or a joke's punchline still
#   to follow), a press in flight. A line is never cut for it: the host waits the seconds her line has
#   left -- a cut line is lost, and the red stop is the way to cut one.

#: How long the page keeps saying how the last window ended (it polls every second).
ADHOC_CLOSED_SHOW_S = 30.0
#: Why "ตอบสด" cannot open now (`_adhoc_refusal`), as the staff phone says it.
ADHOC_REFUSED_TH = {
    "not_ceremony_app": NOT_APP_TH,
    "named": "พิธีนี้ Emma ตอบเมื่อคุณมิกิเรียกชื่ออยู่แล้ว — ไม่ต้องเปิดตอบสด",
    "order_live": "ช่องตอบสดของลำดับพิธีเปิดอยู่ — ใช้ปุ่มของขั้นนั้น",
    "opening": "การ์ดเปิดตัวกำลังทำต่อเนื่องอยู่ — รอให้จบก่อน",
    "moving": "หุ่นกำลังเดินหรือทำท่าอยู่ — รอให้หยุดก่อน",
    "line": "Emma กำลังพูดบทอยู่ — รอให้พูดจบก่อน แล้วค่อยกดตอบสด",
    "busy": "กำลังทำขั้นก่อนหน้าอยู่ — รอสักครู่",
    "unknown": "ตรวจสถานะหุ่นไม่ได้ — ลองกดอีกครั้ง",
}
#: How the last window ended, for the page (the turn log keeps the code). `%s` = its length.
ADHOC_CLOSED_TH = {
    "staff": "ปิดแล้ว — กลับเข้าบท",
    "script": "ปิดแล้ว — กดบทหรือขั้นของลำดับพิธี (กลับเข้าบท)",
    "reset": "ปิดแล้ว — เริ่มลำดับพิธีใหม่",
    "expired": "ปิดเองแล้ว (ครบ %s) — ถ้าคุณมิกิยังด้นสดอยู่ กดตอบสดอีกครั้ง",
    "staff_stopped": "ปิดแล้ว — กดหยุดแดง",
    "stop": "ปิดแล้ว — กดหยุดแดง",
    "cut": "ปิดแล้ว — กดหยุดแดง",
    # the ceremony call ended -- the app closed, or the call re-dialled (the network): either way her
    # window went with it
    "app_closed": "ปิดแล้ว — สายพิธีของหุ่นจบหรือต่อใหม่ ถ้ายังต้องการกดตอบสดอีกครั้ง",
    "ceremony_over": "ปิดแล้ว — สายพิธีของหุ่นจบหรือต่อใหม่ ถ้ายังต้องการกดตอบสดอีกครั้ง",
    "replaced": "ปิดแล้ว — ช่องตอบสดของลำดับพิธีเปิดแทน",
}


def adhoc_max_s() -> float:
    """CEREMONY_ADHOC_MAX_S, never past EVENT_CEREMONY_MAX_S (and a broken value = the default)."""
    try:
        span = float(settings.ceremony_adhoc_max_s)
    except (TypeError, ValueError):
        span = 120.0
    if not math.isfinite(span) or span <= 0:
        span = 120.0
    return min(span, float(settings.event_ceremony_max_s))


def _adhoc_live(now: float) -> dict | None:
    """The open window when it is "ตอบสด"'s (the order's own is not this button's to close)."""
    live = _live_now(now)
    return live if live is not None and live["cue"] == ADHOC_ID else None


def _adhoc_refusal(now: float) -> str | None:
    """Why "ตอบสด" may not open now (an ADHOC_REFUSED_TH key), None = it may. Each probe the one its
    own module answers with; one that raises refuses ("unknown"): an open mic is not the default."""
    if not on():
        return "not_ceremony_app"
    if STATE["replies"] != "cues":
        return "named"
    live = _live_now(now)
    if live is not None and live["cue"] != ADHOC_ID:
        return "order_live"
    if STATE.get("script_presses"):
        # a press of the script is still on its way (`script_press`): it closed the window first, and a
        # tap from another phone in its awaits -- a walk's pose read, a line waiting for her voice --
        # would have opened it again under that press (review 23 Nov)
        return "busy"
    try:
        from app import event_cues, event_host, event_order, event_tour, event_walk

        if event_tour.ceremony_running():
            return "opening"
        if event_walk.walking() or event_host.robot_moving():
            return "moving"
        busy = getattr(event_order, "_line_busy", None)
        if event_cues.line_audible() or (callable(busy) and busy()):
            return "line"
        chain = event_order.STATE.get("chain") or {}
        task = chain.get("task")
        if chain.get("state") == "waiting" and task is not None and not task.done():
            return "line"                                # the joke's punchline still to follow by itself
        if event_order.STATE.get("pressing") is not None:
            return "busy"
    except Exception:                                    # noqa: BLE001
        logger.exception("ceremony: the gates of ตอบสด")
        return "unknown"
    return None


def adhoc_open(*, follow: bool = False, quiet: bool = False) -> dict:
    """The ceremony page's "ตอบสด": the live window with ADHOC_BRIEF_TH, for adhoc_max_s(). Already open
    = nothing moves ({"already": True}: a second phone, a button not redrawn yet). Refused = one
    `event_live` row with why, and the reason for the phone."""
    now = time.time()
    if _adhoc_live(now) is not None:
        return {"ok": True, "open": True, "already": True}
    why = _adhoc_refusal(now)
    if why is not None:
        turnlog.record("event_live", action="refused", cue=ADHOC_ID, why=why)
        return {"ok": False, "error": why, "error_th": ADHOC_REFUSED_TH[why]}
    # `follow` (app/ceremony_follow.py, owner 24 Nov): the window of the whole-ceremony-live mode -- no 2-minute cap
    # (EVENT_CEREMONY_MAX_S is the end), the brief with FOLLOW_BRIEF_TH. `quiet`: a re-open after a line the server
    # played -- the event screen keeps that line's settled slide (no logo in between, owner 18 Nov), the topic
    # slides are made ready without it.
    if follow:
        span, brief = float(settings.event_ceremony_max_s), ADHOC_BRIEF_TH + follow_brief_th()
    else:
        span, brief = adhoc_max_s(), ADHOC_BRIEF_TH
    if settings.ceremony_english_only:
        # (owner 24 Nov "ทำไมยังพูดไทยเอาออกเลย"): the brief's "ตอบเป็นภาษาที่เขาพูดกับคุณ" gives way to English
        brief = brief.replace("ตอบเป็นภาษาที่เขาพูดกับคุณ (พิธีนี้ใช้ภาษาอังกฤษ)",
                              "ตอบเป็นภาษาอังกฤษเสมอ แม้เขาหรือแขกพูดภาษาไทย (ซับไทยบนจอระบบใส่ให้เอง)")
    if not live_open(cue_id=ADHOC_ID, brief_th=brief, max_s=span):
        return {"ok": False, "error": "not_ceremony_app", "error_th": ADHOC_REFUSED_TH["not_ceremony_app"]}
    STATE["adhoc_closed"] = None
    STATE["adhoc_pressed"] = False                       # no line pressed since this window opened
    _motion("on_open_quiet" if quiet else "on_open")     # the event screen: the logo (not when quiet), then the host's topic
    warn = _adhoc_warning()
    return {"ok": True, "open": True, **({"warn_th": warn} if warn else {})}


def adhoc_close(why: str) -> bool:
    """"กลับเข้าบท" ("staff"), a press of the script ("script", "reset"), the red stop ("staff_stopped").
    Only "ตอบสด"'s own window: the order's live step is closed by its own step. True = it was open."""
    if _adhoc_live(time.time()) is None:
        return False
    live_close(why)
    return True


@contextlib.asynccontextmanager
async def script_press(why: str = "script"):
    """A press of the ceremony's script from the staff page (app/event_control_server.py
    `_back_to_script`): "ตอบสด" closes FIRST -- before anything of the press is read or awaited -- and
    cannot open again until the press has answered (`_adhoc_refusal` "busy"). A picture her live
    answer put up is taken off the robot face and the event screen here too (`why` "script": a
    line, a step, a walk -- not a reset). Yields whether a window was open. Closing never stops the
    press: the host is back on the script either way."""
    try:
        closed = adhoc_close(why)
    except Exception:                                    # noqa: BLE001 -- the press still goes
        logger.exception("ceremony: closing ตอบสด for a press")
        closed = False
    STATE["script_presses"] = int(STATE.get("script_presses") or 0) + 1
    try:
        _motion("on_press")                              # its topic slide too, a reset's as well (ceremony_motion)
        if why == "script":
            if STATE.get("adhoc_recent"):
                STATE["adhoc_pressed"] = True            # a late picture of hers now comes down at once
            await _clear_adhoc_picture()
        yield closed
    finally:
        STATE["script_presses"] = max(0, int(STATE.get("script_presses") or 0) - 1)


# ---- her live answer's picture (owner 23 Nov: "the picture stays until the next line is pressed") ----
# present_story, a unit card, an answer picture (ANSWER_IMAGES) inside "ตอบสด" go up on the robot face and
# the event screen like any answer's. Left alone it stayed there under every later line: the face clears
# an old picture at a NEW answer's first sound only after a guest spoke (voice-preview.html: answerOwed,
# set by `speech_started`), and a cue-only ceremony's mic is silent once the window is shut -- so a line
# with no `show` of its own never took it down, and the slides that would cover it on the event screen
# are not sent while their file is a draft. The next press of the script takes it off: the same clear a
# stopped cue's picture gets (session._story_picture {"clear": True} -> tool_result screen "clear" -> the
# face's clearStage, and face_mirror passes it to the event screen). Only her live answer's picture: one
# put up while the window was open and not replaced since -- a cue's own picture (`show`) is never
# taken by this, and a cue pressed with its own `show` still puts it up before its first word.

#: What on the robot face is a picture (voice-preview.html tool_result: embed, unit, story, slide).
_PICTURE_SCREENS = ("story", "unit", "plan")


def note_face_message(session, payload: dict, *, own: bool = False) -> None:
    """A tool_result the ceremony call's robot face is about to show (session._note_ceremony_picture).
    `own` = her own tool's result (the receive loop); else the server's (a cue's `show`, a clear).

    Her own picture belongs to "ตอบสด" while the last live window was "ตอบสด"'s (`adhoc_recent`) -- inside
    it, or AFTER it (review 23 Nov round 2: a search with its answer picture, or a story, still running
    when staff pressed the next line lands 0.5-2 s later; with the window shut every tool but the stops is
    refused, so a picture then can only be her live answer's). Late, after that press = down at once (the
    press it belongs under has passed); late, with no press yet ("กลับเข้าบท", the window's end) = down at
    the next press. A server picture (a cue's own `show`) or any clear replaces it: never taken by this.
    A result that did not draw (`ok` False: show_plan's pending display) is no picture."""
    if session is None or session is not active_session() or not isinstance(payload, dict):
        return
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    if result.get("screen") == "clear":
        STATE["adhoc_picture"] = False
        return
    picture = result.get("ok") is not False and (
        result.get("screen") in _PICTURE_SCREENS or bool(result.get("embed")) or bool(payload.get("slide")))
    if not picture:
        return
    if not own:
        STATE["adhoc_picture"] = False                   # a cue's own picture replaced hers
        return
    live = _live_now(time.time())
    if live is not None and live["cue"] != ADHOC_ID:
        return                                           # the order's own live step: not this button's
    if live is None and not (STATE.get("adhoc_recent") and cue_only()):
        return
    STATE["adhoc_picture"] = True
    if live is None and STATE.get("adhoc_pressed"):
        _spawn(_clear_adhoc_picture())                  # the line was pressed already: not under it


async def _clear_adhoc_picture() -> bool:
    """Her live answer's picture off the face and the event screen, through the path a stopped cue's
    picture takes (session._story_picture {"clear": True}). The mark goes only once the clear has gone out
    (review round 2: a press during a re-dial, a clear that raised, left the picture up unmarked).
    True = a clear was sent."""
    if not STATE.get("adhoc_picture"):
        return False
    session = active_session()
    clear = getattr(session, "_story_picture", None) if session is not None else None
    if not callable(clear):
        return False
    try:
        await clear({"clear": True, "cue": ADHOC_ID})
    except Exception:                                    # noqa: BLE001 -- the press still goes
        logger.exception("ceremony: taking ตอบสด's picture down")
        return False
    STATE["adhoc_picture"] = False
    turnlog.record("event_live", action="picture_cleared", cue=ADHOC_ID)
    return True


def _motion(name: str) -> None:
    """app/ceremony_motion.py's `on_open` / `on_press` (the event screen's topic slide). Never raises."""
    try:
        from app import ceremony_motion

        getattr(ceremony_motion, name)()
    except Exception:                                    # noqa: BLE001 -- the screen, never the press
        logger.exception("ceremony: the event screen's topic slide (%s)", name)


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(round(seconds)), 60)
    return "%d:%02d" % (minutes, secs)


def adhoc_snapshot() -> dict:
    """For the ceremony page (GET /ceremony/state `adhoc`): open or not and until when (wall clock:
    the page counts down against `server_time`), whether it may open now and why not, and how the last
    one ended (ADHOC_CLOSED_SHOW_S). No words of anybody's."""
    now = time.time()
    live = _adhoc_live(now)
    why = None if live is not None else _adhoc_refusal(now)
    closed, shown = STATE["adhoc_closed"], None
    if live is None and closed is not None and 0.0 <= now - closed["at"] <= ADHOC_CLOSED_SHOW_S:
        th = ADHOC_CLOSED_TH.get(closed["why"]) or "ปิดแล้ว"
        shown = {"why": closed["why"], "why_th": th % _clock(closed["max_s"]) if "%s" in th else th,
                 "at": closed["at"]}
    return {"open": live is not None, "opened_at": live["opened_at"] if live else None,
            "until": live["until"] if live else None, "max_s": round(adhoc_max_s(), 1),
            "ready": live is not None or why is None, "why": why,
            "why_th": ADHOC_REFUSED_TH.get(why) if why else None, "closed": shown,
            "warn_th": _adhoc_warning()}


#: The robot face's mic muted (a tap this morning, the held mute): the window opens, she hears nothing.
ADHOC_MIC_TH = "ไมค์ปิดอยู่ที่หน้าหุ่น — Emma ไม่ได้ยินคุณมิกิ (แตะไมค์ที่หน้าจอหุ่นก่อน)"


def _adhoc_warning() -> str | None:
    """What "ตอบสด" cannot see for itself, said on its bar (a warning, not a refusal: the probe is the mode
    card's, and a wrong "muted" must not take the button away on the stage)."""
    if not on():
        return None
    try:
        from app import event_host

        return ADHOC_MIC_TH if event_host._mic_closed() else None
    except Exception:                                    # noqa: BLE001 -- a warning, never the poll's end
        return None


def adhoc_refuses(name: str) -> bool:
    """A tool the open "ตอบสด" window does not run (ADHOC_NO_TOOLS) -- registry._dispatch asks. Only
    "ตอบสด"'s own window: the order's live step keeps every tool it had."""
    return name in ADHOC_NO_TOOLS and _adhoc_live(time.time()) is not None


# ---- which source wins, and what she just said (owner 24 Nov: "กฎลำดับแหล่ง + เห็นบทที่เพิ่งพูด") ------------
# A read-only cross-check (.tmp/ceremony-data-check/README_TH.md) found sources that disagree: the script's "indoor
# facilities (sales estimate)" against the report's total, the script's straight-line distance to
# the beach against a story's driving minutes, one 7-Eleven at two times. Nothing said which one wins, and the notes
# beside tool results carried only a cue's id -- she could not see the number she had just said. Two additions,
# both for her LIVE answers only (the ceremony's prompt and the notes beside its tool results; the sales, condo and
# event prompts are not touched, a test holds them):
# * the order of the sources, as a rule -- no figure in it;
# * the approved text of the last one or two lines she said, beside her tool results while a window is open;
# * how big the common area / the facilities are: as the script says it. The figure is read from the approved cue's
#   own `numbers` row when this runs -- a re-signed script stays the one source; the cue not servable = hand over.

#: the order, in the words she is given (instructions only, nothing she could parrot as an answer)
SOURCE_ORDER_TH = ("ถ้าข้อมูลสองแหล่งขัดกัน (ตัวเลข ระยะทาง เวลา) ให้ถือตามลำดับนี้: สิ่งที่คุณเพิ่งพูดในบทพิธี → "
                   "ข้อมูลโครงการที่อนุมัติ (facts) → รายงานบท 23 → รายงานส่วนอื่นและเรื่องเล่า "
                   "ไม่แน่ใจว่าอันไหนถูก ให้ส่งต่อทีมขายยืนยัน ไม่เลือกตัวเลขเองและไม่พูดทั้งสองตัวเลข")
#: the cue whose approved text answers "how big is the common area / the facilities"
COMMON_AREA_CUE = "s06_l03"
COMMON_AREA_ASK_TH = "ถ้าถูกถามว่าพื้นที่ส่วนกลางหรือ Facility ใหญ่แค่ไหน"
COMMON_AREA_HANDOVER_TH = COMMON_AREA_ASK_TH + " ให้ส่งต่อทีมขายยืนยัน ไม่ตอบตัวเลขเอง"
COMMON_AREA_TH = (COMMON_AREA_ASK_TH + " ตอบแบบบท: Facility ในร่มราว %s ซึ่งเป็นตัวเลขที่ทีมขายประมาณ ไม่ใช่ตัวเลขทางการ · "
                  "ถ้าถูกถามพื้นที่รวมของทั้งโครงการหรือทุกส่วนรวมกัน ให้ส่งต่อทีมขายยืนยัน ไม่ตอบตัวเลขเอง")
#: what she just said, beside her tool results (the approved line, as said: NOTE_SAID's precedent)
SAID_TEXT_TH = "บทพิธีที่คุณเพิ่งพูด (ข้อมูลที่อนุมัติ ใช้ก่อนแหล่งอื่น ห้ามพูดซ้ำ): %s"
SAID_TEXT_CHARS = 420
#: how long the servable map is kept (the check reads number sources; a tool result must not wait for it every time)
#: (review C 24 Nov: at 15 s every second refresh met an expired 30 s source cache -- servable() ~0.15 s on the PC,
#: ~0.6 s on the robot, ON the loop from every tool-result note. On the robot the files change only by a deploy,
#: which restarts the server; 300 s costs nothing real and keeps the stall to once in five minutes.)
SERVED_TTL_S = 300.0
_SERVED: dict = {"at": 0.0, "doc": None, "map": {}}


def _served() -> dict:
    """event_script.servable() for the ceremony file, kept SERVED_TTL_S per file read. {} when it cannot be read."""
    from app import event_script

    try:
        doc = event_script.load()
        now = time.monotonic()
        if _SERVED["doc"] is not doc or now - _SERVED["at"] > SERVED_TTL_S:
            _SERVED.update(at=now, doc=doc, map=event_script.servable(doc) if doc else {})
        return _SERVED["map"]
    except Exception:                                    # noqa: BLE001 -- no rule is better than a broken note
        logger.exception("ceremony: the servable lines")
        return {}


def common_area_th() -> str:
    """The common-area rule: the figure of COMMON_AREA_CUE's own `numbers` row (its `say`), when that cue is
    approved and servable now -- never a figure typed here. Else: hand over to the sales team."""
    cue = _served().get(COMMON_AREA_CUE)
    for row in (cue or {}).get("numbers") or []:
        say = str((row or {}).get("say") or "").strip()
        if say and ("ตารางเมตร" in say or "ตร.ม." in say):
            return COMMON_AREA_TH % say
    return COMMON_AREA_HANDOVER_TH


#: "เอาตามบทเลย เพราะเหมือนจอ led จะผิด" (owner 24 Nov, after the side-by-side): the script's figures win, everywhere in her
#: live answers -- and she always carries the script's approved facts, not only the last line she said.
SCRIPT_WINS_TH = "เรื่องเดียวกับในบท ให้ใช้ตัวเลข/ถ้อยคำตามบทเสมอ แม้ผลค้นหรือเรื่องเล่าจะบอกต่างไป"
SCRIPT_FACTS_TH = ("ข้อเท็จจริงในบทพิธี (อนุมัติแล้ว เป็นข้อมูลอ้างอิง ไม่ใช่ประโยคให้พูดซ้ำและไม่ต้องเล่าล่วงหน้า): %s")
SCRIPT_FACTS_CHARS = 1500
SCRIPT_FACT_CHARS = 170
_FIGURE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
#: a fact is cut to the clause that states the figure: whole sentences are her lines, and the line is the script's to say
_CLAUSE_END = re.compile(r"\s*[,;:]\s+|\s+(?:and|but|so|which|where)\s+", re.I)
#: ...and a clause that talks TO somebody (a suggestion, an offer) is no fact
_SPEECH_ACT = re.compile(r"\b(?:I|I'd|I'll|I'm|me|my|you|your|let's|please|suggest|recommend)\b", re.I)
SCRIPT_FACT_MIN_WORDS = 3
SCRIPT_FACT_SENTENCE_WORDS = 20


def _joke_cues(doc) -> set:
    """The cues of the order's chained jokes (`"chain": true` on the set-up, and the step it presses): no fact of
    theirs rides in her context -- a set-up or a punchline is the host's cue to hear, not data to carry. A chain whose
    second line `continues` the first (questions 1 and 9, owner 24 Nov) is her answer in two lines, no joke."""
    from app import event_script
    from app.ceremony_follow import answer_in_two

    out: set = set()
    for sc in event_script.order(doc) or []:
        steps = sc["steps"]
        for k, st in enumerate(steps):
            if st.get("chain") and st.get("cue"):
                if k + 1 < len(steps) and answer_in_two(steps[k + 1].get("cue")):
                    continue
                out.add(st["cue"])
                if k + 1 < len(steps) and steps[k + 1].get("cue"):
                    out.add(steps[k + 1]["cue"])
    return out


def script_facts_th() -> str | None:
    """The script's approved facts, compact: for every servable cue (not a joke's) each `numbers` row as the sentence of
    the line that states the figure (the English when the file speaks English, else the row's own `say` with its
    approved source quote), de-duplicated, in the script's order. None = no cue is servable (nothing signed): no
    block, and the common-area / source-order rules hand over to the sales team."""
    from app import event_script

    served = _served()
    if not served:
        return None
    doc = event_script.load()
    jokes = _joke_cues(doc)
    en_spoken = event_script.speak(doc) == "en"
    facts: list[str] = []
    for cue in event_script.cues(doc):
        c = served.get(cue["id"])
        if c is None or c["id"] in jokes:
            continue
        for row in c.get("numbers") or []:
            say = str((row or {}).get("say") or "").strip()
            figure = _FIGURE.search(say)
            if not say or figure is None:
                continue
            sentence = None
            if en_spoken:
                found = re.compile(r"(?<![\d,.])%s(?![\d,]|\.\d)" % re.escape(figure.group(0)))
                for part in _SENTENCE_END.split(" ".join(str(c.get("en") or "").split())):
                    if not found.search(part):
                        continue
                    # a short sentence whole (it keeps its subject), a long one cut to the clause with the figure
                    clause = part if len(part.split()) <= SCRIPT_FACT_SENTENCE_WORDS else next(
                        (p for p in _CLAUSE_END.split(part) if found.search(p)), None)
                    if clause:
                        clause = re.sub(r"^(?:and|but|so)\s+", "", clause.strip(" ."), flags=re.I)
                        if _SPEECH_ACT.search(clause) or len(clause.split()) < SCRIPT_FACT_MIN_WORDS:
                            continue
                        sentence = clause
                        break
            quote = " ".join(str((row or {}).get("as") or "").split())
            text = sentence or ("%s (%s)" % (say, quote) if quote and quote != say else None)
            if not text:
                continue                                 # a bare figure with no subject is no fact
            text = text if len(text) <= SCRIPT_FACT_CHARS else text[:SCRIPT_FACT_CHARS].rstrip() + "…"
            if text not in facts:
                facts.append(text)
    if not facts:
        return None
    body = " · ".join(facts)
    return SCRIPT_FACTS_TH % (body if len(body) <= SCRIPT_FACTS_CHARS else body[:SCRIPT_FACTS_CHARS].rstrip() + "…")


def prompt_rules() -> list[str]:
    """The rules for the ceremony's system prompt (event_host.ceremony_block): the source order and the common area."""
    facts = script_facts_th()
    return [SOURCE_ORDER_TH, SCRIPT_WINS_TH] + ([facts] if facts else []) + [common_area_th()]


def recent_cues_th(now: float | None = None) -> str | None:
    """The approved text of the last one or two lines she said whole (event_cues `said_log`, within
    SAID_NOTE_S), as said -- only a cue that is servable now, never a draft, each cut to SAID_TEXT_CHARS."""
    from app import event_cues, event_script

    now = time.monotonic() if now is None else now
    served = _served()
    doc = event_script.load()
    texts = []
    for entry in (event_cues.STATE.get("said_log") or [])[-2:]:
        if now - entry["at"] > event_cues.SAID_NOTE_S:
            continue
        cue = served.get(entry["id"])
        if cue is None or not cue.get("th"):
            continue
        text = " ".join(str(event_script.spoken(cue, doc).get("th") or "").split())
        if text:
            texts.append("«%s»" % (text if len(text) <= SAID_TEXT_CHARS else text[:SAID_TEXT_CHARS].rstrip() + "…"))
    return SAID_TEXT_TH % " ".join(texts) if texts else None


def live_extras() -> str | None:
    """Beside her tool results while a live window is open (event_host.ceremony_note): what she just said, the order of
    the sources, the common area. None = no window open (the cue-only ceremony runs no tool of hers)."""
    if _live_now(time.time()) is None:
        return None
    parts = [recent_cues_th(), SOURCE_ORDER_TH, SCRIPT_WINS_TH, script_facts_th(), common_area_th()]
    return " · ".join(p for p in parts if p)


# ---- the handoff to the Emma Event app ----------------------------------------------------

def _clean_reason(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return " ".join(value.split())[:80]


def _fail_handoff(session, why: str) -> None:
    entry = STATE["handoff"]
    if entry is None or (session is not None and entry["session"] is not session):
        return
    fut = entry["future"]
    if not fut.done():
        try:
            fut.set_result({"ok": False, "reason": why, "ours": True})
        except RuntimeError:                             # its loop has closed (a test before this one)
            pass


def open_app_result(session, event: dict) -> bool:
    """The robot face's answer to `open_app` (session's receive loop). Only the call that was
    asked, only for the app it was asked to open; anything else is nothing. True = it answered
    the handoff waiting now."""
    entry = STATE["handoff"]
    if (entry is None or session is None or entry["session"] is not session
            or not isinstance(event, dict) or event.get("app") != entry["app"]):
        return False
    fut = entry["future"]
    if fut.done():
        return False
    fut.set_result({"ok": event.get("ok") is True, "reason": _clean_reason(event.get("reason"))})
    return True


async def handoff(app: str = "event", *, wait_s: float | None = None) -> dict:
    """The order's handoff step: the ceremony face opens the Emma Event app. {"ok": True, "app"}
    once the face says it did; else {"ok": False, "error", "error_th", "reason"}."""
    if app not in APPS:
        return {"ok": False, "error": "bad_app", "error_th": HANDOFF_FAILED_TH, "reason": None}
    session = active_session()
    if session is None:
        return refusal()
    old = STATE["handoff"]
    if old is not None:
        _fail_handoff(old["session"], "replaced")
    fut = asyncio.get_running_loop().create_future()
    entry = {"session": session, "app": app, "future": fut}
    STATE["handoff"] = entry
    wait = HANDOFF_WAIT_S if wait_s is None else float(wait_s)
    try:
        try:
            await session._send_json({"type": "open_app", "app": app})
        except Exception:                                # noqa: BLE001 -- the phone says so
            logger.exception("ceremony handoff: sending open_app")
            answer = {"ok": False, "reason": "not_sent", "ours": True}
        else:
            try:
                answer = await asyncio.wait_for(asyncio.shield(fut), timeout=wait)
            except asyncio.TimeoutError:
                answer = {"ok": False, "reason": "no_answer", "ours": True}
    finally:
        if STATE["handoff"] is entry:
            STATE["handoff"] = None
    ok = bool(answer.get("ok"))
    reason = answer.get("reason")
    turnlog.record("ceremony_handoff", app=app, ok=ok, reason=None if ok else reason)
    if ok:
        return {"ok": True, "app": app}
    why = HANDOFF_WHY_TH.get(reason, reason) if answer.get("ours") else reason
    return {"ok": False, "error": "handoff_failed", "reason": reason,
            "error_th": HANDOFF_FAILED_TH + (" (%s)" % why if why else "")}


# ---- for the staff pages ------------------------------------------------------------------

def snapshot() -> dict:
    """The ceremony app's mode, for both phones. No words of anybody's."""
    return {"on": on(), "replies": replies(), "since": STATE["since"] if on() else None}
