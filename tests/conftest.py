"""Shared test setup.

The turn log is the important thing here. It defaults to on, which is right
for a robot in a showroom and wrong for a test suite: running the tests wrote
several hundred lines of fixture data into the same file the operator reads to
find out what happened with real visitors.

That is not a cosmetic problem. The whole point of the log is to answer
questions with evidence, and the first time it was read the report said 61
sessions and 33 pricing questions — every one of them from pytest. A record
that mixes real events with invented ones is worse than no record: it looks
authoritative and it is wrong.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_turn_log(monkeypatch):
    """Keep the tests out of data/logs/.

    autouse so it cannot be forgotten. A test that genuinely wants to exercise
    logging should point `turn_log_dir` at tmp_path itself.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "turn_log", False)


@pytest.fixture(autouse=True)
def _fresh_async_state():
    """Give every test its own locks, tasks and audio clock.

    These tests run `asyncio.run()` per test, so **every test is a different
    event loop**, while `display._lock`, `canva_display._lock`, `_reveal_task`
    and `_task` are created once at import and live for the whole session.
    That combination has two failure modes and both of them are hangs, which
    is the worst kind because the stack trace points at the innocent test that
    happened to run next.

    A lock is left `locked` if whatever held it was abandoned when its loop
    closed. Nothing can ever release it again — its owner's loop is gone — so
    the next test to reach `async with _lock` waits forever.

    The audio clock is worse because it looks reasonable. `_audio_until` is a
    plain `time.monotonic()` deadline, so a test that sets a long lead and
    doesn't wait it out leaves it in the future, and the next test's
    `next_slide` sits in `wait_until_heard` for the full
    `SLIDE_TOOL_BUDGET_S` — twenty seconds, which is over pytest's timeout.
    Observed on Windows with Python 3.14 as
    `test_the_tour_waits_for_the_guest_to_hear_the_slide` hanging, having
    passed everywhere else.

    Cheap to reset and impossible to forget, so reset it.
    """
    import asyncio

    from app import display, events
    from app.tools import canva_display

    display._lock = asyncio.Lock()
    canva_display._lock = asyncio.Lock()
    events._lock = asyncio.Lock()

    # A session left registered by an earlier test is the same class of leak
    # as the locks, and it fails in the more embarrassing direction: the next
    # test's announcements are delivered to a provider belonging to a session
    # that no longer exists, and the assertions read as if they had gone
    # nowhere. Cleared on both sides so neither order can carry it.
    from app import session as session_module

    session_module._active = None

    # The suite must not change colour when the machine's .env does — the
    # documented trap ("tests reading machine state") in its newest costume.
    # Turning the owner's Emma profile on in .env turned an OpenAI prompt
    # test red, because the live session built Emma's instructions instead
    # of the receptionist's. Pin the profile and wake knobs to defaults;
    # tests that mean a different value set it themselves.
    from app.config import settings as _settings

    _settings.assistant_profile = "condo"
    _settings.ws_token = ""          # the LAN gate; tests opt in explicitly
    _settings.wake_enabled = False
    # WAKE_ENROLL=true in the machine's .env (an enrollment session) would
    # make every wake test save clips into data/wake_enroll — the machine-
    # state trap with a recorder attached. Tests that mean it set it.
    _settings.wake_enroll = False
    # And WAKE_DEBUG=true in the same .env made an enroll test pass for the
    # wrong reason before this pin existed: the capture tick only ran
    # because debug happened to be on here. The suite must not inherit it.
    _settings.wake_debug = False
    # The shipped defaults, re-measured 2026-08-25 (see app/wake.py): the
    # detection fixtures assert against these exact values.
    _settings.wake_threshold = 0.10
    _settings.wake_boost = 3.0
    # Two more knobs whose *default* is right for the gallery and wrong for a
    # 20-second test timeout. Both were the documented trap — "green on a
    # machine that is missing something" — reaching its payday: they only
    # ever hung on a machine that had everything.
    #
    # embed_provider: with EMBED_PROVIDER=local in .env, one
    # `search_condo_info` call imports sentence-transformers and encodes the
    # whole deck. CI has no such package, so `choose_provider()` returned
    # "off" there and the tests were quick and green;
    # `test_answers_spoken_questions_not_just_keywords` hung here instead.
    # Pinning "off" makes every machine run the search CI actually verified.
    # Tests that mean to exercise semantic search patch `choose_provider`.
    #
    # canva_warm_deck: `_ensure_page()` finishes by walking the whole deck
    # once (WARM_STEP_S 0.55s per page, ~33s for 59 pages) so a later jump
    # lands on a drawn page instead of a blank. Fine at 08:00 in a showroom,
    # fatal inside a test that drives `_ensure_page` for real — which
    # `test_a_fullscreen_failure_does_not_cost_us_the_window` does.
    _settings.embed_provider = "off"
    # EMBED_BUILD=false belongs to the robot (robot.env); a PC .env that
    # copied it would turn every index-building test red.
    _settings.embed_build = True
    # Same family as tool_groups below: the gallery's .env scopes the library
    # to Embassy World (MYDOCS_INCLUDE), which would filter out every temp doc
    # a mydocs test drops in a tmp folder and turn the suite red on that one
    # machine. Blank = every file, what the tests were written against.
    _settings.mydocs_include = ""
    _settings.canva_warm_deck = False
    # And the biggest one of the family, found the hard way: the owner added
    # TOOL_GROUPS to .env to switch on the unit card, and nineteen robot and
    # slide tests went red on a machine where nothing had been edited. The
    # tools those tests exercise simply stopped being registered.
    #
    # Blank = "every group", which is the gallery's own default and the
    # configuration the tests were written against. Tests that mean a subset
    # set it themselves.
    _settings.tool_groups = ""
    # VAD_MODE=local on the owner's machine would make every provider test
    # construct a real Silero detector (and change what send_audio emits).
    # Pin to the mode the tests were written against; VAD tests set "local"
    # themselves with a scripted detector.
    _settings.vad_mode = "gemini"
    # CALL_DEBUG writes call audio to disk; the machine chasing a mic problem
    # has it on in .env, and a session test that fed audio would then drop a
    # wav into the repo's data/. Off in the suite, like every pin here.
    _settings.call_debug = False
    # LISTEN_FAR_PROFILES (the owner's machine sets joke,translator) changes
    # the floor every session opens with and what /health tells the page.
    # Blank = no split, the chain every older test was written against.
    _settings.listen_far_profiles = frozenset()
    _settings.vad_min_rms_far = 0.0
    # Speaker focus (VAD_FOCUS_RATIO) at its code default, whatever this
    # machine's .env says — the real-detector tests measure turn ends.
    _settings.vad_focus_ratio = 0.30
    _settings.transcribe_mode = "SMART"
    # The live inventory link would send every unit test's show_unit call to
    # the real Supabase — network in unit tests, and results that change
    # when the sales team sells a room. Same family as every pin above.
    _settings.inventory_url = ""
    _settings.inventory_key = ""
    _settings.units_show_price = False
    # Promotions off by default (test rows in the sales system, 2026-10-06);
    # tests that exercise the promotion paths turn it on themselves.
    _settings.units_promotions = False
    # Answer pictures without the slides group (2026-10-11) are on in the
    # robot's env; the suite tests both settings explicitly.
    _settings.answer_images = False
    # The motion stories (app/stories.py): the robot's env will turn them on,
    # and then every robot-face test would see present_story and its prompt
    # line -- the snapshots would change colour by .env. Tests that mean the
    # stories turn them on and point the catalog at their own tmp folder.
    _settings.stories_enabled = False
    _stories_mod = __import__("sys").modules.get("app.stories")
    if _stories_mod is not None:
        _stories_mod.reset()
    _settings.searxng_url = ""       # เครื่องที่มี SearXNG ต้องไม่เปลี่ยนสี suite   # นโยบายราคา: default ปิด
    # MULTI_SESSION on the office test server would un-supersede every
    # takeover test and strip the tool groups the robot/slide tests need —
    # the TOOL_GROUPS trap again, one knob over. Tests that mean the shared
    # mode set it themselves.
    _settings.multi_session = False
    # STANDING_LINE=true on the robot's machine holds a summoned session open
    # forever, which would turn every summoned-session lifecycle test (the
    # 30s abandon, idle timeout) green or red by .env. Tests that mean the
    # standing line set it themselves.
    _settings.standing_line = False
    # HOLD_SPEECH=true on the robot changes the ready event every voice test
    # reads. Same rule: the suite must not change colour by .env.
    _settings.hold_speech = False
    # Listening focus (docs/listening-focus-2026-10-09.md): the robot's .env
    # will turn these on, and every VAD/session/prompt test would then ask a
    # camera nobody has — denied "no camera", red on that one machine. Code
    # defaults; focus tests set what they mean. The module's memory (faces
    # seen, ordinals) is per process, so it is wiped like the locks above.
    _settings.focus_enabled = False
    _settings.focus_require_face = True
    _settings.focus_face_min_px = 90
    _settings.focus_yaw_max_deg = 35.0
    _settings.focus_hold_s = 1.5
    _settings.focus_speaker_labels = False
    # Engagement (in-call wake word, invitation, full mode, attention cue,
    # goodbye): the robot's env will turn these on, and every session test
    # would then grow a watcher task or a KWS load. Code defaults here.
    _settings.focus_wake = False
    _settings.focus_wake_threshold = 0.10
    _settings.focus_wake_window_s = 10.0
    _settings.focus_invite = False
    _settings.focus_invite_every_s = 60.0
    _settings.focus_invite_denied_n = 0
    _settings.focus_engage_mode = "semi"
    _settings.focus_attention = False
    _settings.focus_goodbye = False
    _settings.focus_goodbye_after_s = 8.0
    _settings.asd_enabled = False
    _settings.voiceprint_enabled = False
    _settings.doa_enabled = False
    # The array as the call's ears (app/array_audio.py) and the GTCRN
    # enhancer: the robot's .env turns these on; every session test would
    # then route audio through a tap or a model. Code defaults here.
    _settings.array_audio = False
    _settings.array_audio_beam = "ds"
    _settings.array_audio_unsteered = "mic0"
    _settings.array_audio_gain = 1.0
    _settings.array_audio_stale_ms = 120
    _settings.array_audio_echo_tail_s = 0.25
    _settings.array_capture8 = False
    _settings.array_capture8_keep_min = 10.0
    _settings.enhance_enabled = False
    _array_mod = __import__("sys").modules.get("app.array_audio")
    if _array_mod is not None:
        _array_mod.reset_state()
    _focus_mod = __import__("sys").modules.get("app.focus")
    if _focus_mod is not None:
        _focus_mod._forget_everything()
    # The in-call ear's spotter is built for one FOCUS_WAKE_THRESHOLD; a test
    # that changes the bar must not inherit the previous test's model.
    _call_wake_mod = __import__("sys").modules.get("app.call_wake")
    if _call_wake_mod is not None:
        _call_wake_mod.reset()
    # The web stage turns `open_in_browser` and `play_youtube` into "put it
    # on the screen" instead of `os.startfile`. A test that leaves it on
    # rewires four other tests that never mentioned it — and because
    # monkeypatch restores whatever it found at setup, one leak is enough to
    # make the failure depend on test order. Pinned here, like the profile
    # and the LAN gate, for the same reason: the suite must not change
    # colour because of a value some other test set.
    _settings.web_stage = False
    # The IR hub, same family as robot_chassis_url below. The owner's PC has
    # the Broadlink on its LAN, `broadlink` installed and both JSON files in
    # data/ — so any test that reached broadlink_ir.send without the stub
    # would have switched the real lights. And since 2026-10-09 the AC tool's
    # declaration depends on which units have learned codes: once the Samsung
    # codes are learned on this machine, every AC test written for one unit
    # would turn into a "which AC?" refusal here and stay green on CI. Blank
    # = no devices file (the original AC alone) and no codes/hub file
    # (available() is False, send() is "mock"). Tests that mean a hub or a
    # second unit point these at their own tmp files.
    _settings.ir_devices_file = ""
    _settings.ir_codes_file = ""
    _settings.broadlink_device_file = ""
    # A machine that has been pointed at the robot's navigation board would
    # otherwise send every movement test's command down a real socket to real
    # motors, and `available()` would flip to True in tests written for a
    # robot that is not there. Same family as `inventory_url` above, with a
    # heavier object on the end of it. Tests that mean the chassis path set
    # the URL themselves and patch the transport.
    _settings.robot_chassis_url = ""
    from app import robot_chassis

    robot_chassis.reset_state()
    # And the arm, for a reason the chassis pin does not cover: this one
    # shells out to adb, so a developer machine with a robot plugged in could
    # run `adb shell printf ... > /dev/ttyUSB10` from a unit test. Two
    # switches off, and the module's memory of what it last commanded cleared
    # so no test inherits another test's idea of where a joint is.
    _settings.robot_arm_enabled = False
    _settings.robot_arm_port = ""
    _settings.robot_arm_motion_enabled = False
    _settings.robot_greeting_gesture_enabled = False
    from app import robot_arm

    robot_arm.reset_state()
    # Body by voice + robot-face navigation (docs/emma-body-nav-design-
    # 2026-10-08.md §2h): the robot machine's .env will turn these on, and a
    # suite that changed colour with it — or sent a body_command from a unit
    # test — is the machine-state trap again. Code defaults, every test.
    # robot_nav_places_file is blank so no test reads the tracked floor plan
    # by accident; tests pass their fixture explicitly.
    _settings.robot_body_enabled = False
    _settings.speech_gestures = False
    _settings.robot_nav_routes_enabled = False
    _settings.robot_nav_panel = "off"
    _settings.robot_nav_pin = ""
    _settings.robot_nav_voice = "off"
    _settings.robot_face_token = ""
    _settings.turn_idle_tracks_tools = False
    _settings.robot_nav_places_file = ""
    _settings.announce_settle_s = 0.0
    _settings.robot_heard_max_age_s = 10.0
    _settings.robot_body_require_heard = True
    _settings.robot_nav_require_heard = True
    _settings.robot_nav_voice_unverified = False
    _settings.robot_nav_status_stale_s = 20.0
    from app import robot_face_link

    robot_face_link.reset_state()
    # The tool modules of P1/P2 register tools on import, so they are reset
    # only if something already imported them — the calc rule.
    import sys as _sys_modules

    for _name in ("app.robot_body", "app.robot_nav", "app.speech_gestures"):
        _module = _sys_modules.modules.get(_name)
        if _module is not None and hasattr(_module, "reset_state"):
            _module.reset_state()
    display._reveal_task = None
    canva_display._task = None
    display._audio_lead_ms = 0.0
    display._audio_until = 0.0
    # Subtitle state is module-level too, and its queue holds a *task* plus
    # monotonic deadlines — both of the leak shapes this fixture exists for.
    display.reset_subtitle()

    # Reminder state is module-level too: a watcher task belongs to a dead
    # loop the moment its test ends, and a cached item list from one test's
    # tmp_path poisons the next. Only touched if something imported it —
    # importing here would register its tools into every test's registry.
    import sys as _sys

    _rem = _sys.modules.get("app.tools.reminders")
    if _rem is not None:
        _rem.reset()
    _mem = _sys.modules.get("app.memory_store")
    if _mem is not None:
        _mem.reset()
    _docs = _sys.modules.get("app.tools.mydocs")
    if _docs is not None:
        _docs.reset()
    # The embed_provider="off" pin above closes the front door, and a
    # back door stayed open: a SemanticIndex loaded from this machine's
    # embeddings.npz embeds queries with the provider recorded in the cache
    # (deliberately — vectors from two spaces must never be compared), so
    # `similarities()` reaches `_local_encoder()` regardless of the setting.
    # On a machine with sentence-transformers installed that import can take
    # longer than the 20s test timeout, and with random test ordering it
    # only sometimes does — the worst kind of red. Stub the encoder to fail
    # fast; slide_search already catches that and finishes lexical-only,
    # which is the configuration the tests were written against. Tests that
    # mean to exercise the encoder patch it themselves (test_retrieval).
    _ret = _sys.modules.get("app.tools.retrieval")
    if _ret is not None:
        _ret._local_encoder = lambda: None
    _slide_search = _sys.modules.get("app.tools.slide_search")
    if _slide_search is not None:
        # One failed embed latches semantic off "for the rest of this run" —
        # right in production, but a latch that survives into the next test
        # makes results depend on ordering. Fresh per test.
        if getattr(_slide_search, "_index", None) is not None:
            _slide_search._index._embedding_failed = False

    _calc = _sys.modules.get("app.tools.calc")
    if _calc is not None:
        _calc.reset()
    _wake = _sys.modules.get("app.wake")
    if _wake is not None:
        _wake._STANDBY.clear()
    yield
    # Drop, don't cancel: by now the loop these belong to is already closed,
    # so there is nothing left to cancel them with. Holding the reference is
    # what would carry a dead task into the next loop.
    display._reveal_task = None
    canva_display._task = None
    session_module._active = None
    # Second line of defence for the per-connection tool gate: a leaked
    # session_profile would hand the next test somebody else's tool list, and
    # that failure reads as "unrelated test broke" — the shape this repo has
    # been bitten by with module-level asyncio state.
    from app.tools import registry as _registry

    _registry.session_profile.set(None)
    from app import heard as _heard
    from app import robot_face_link as _face_link

    _heard.bind_session(None)
    _face_link.reset_state()


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch, request):
    """Running the tests must never open a browser window.

    It did. On 2026-08-11 `pytest` on the developer's Windows machine put a
    **fullscreen kiosk Chrome** on screen showing
    `DNS_PROBE_FINISHED_NXDOMAIN` for `canva.test` — the fake URL from
    `test_canva_display.py`'s own fixture. `canva_kiosk` is true by default, so
    it had no address bar and no close button.

    The cause is worth writing down, because it is the mirror image of a
    mistake this file already knows about.

    `test_ensure_page_actually_drops_the_dead_handle` deliberately drives the
    real `_ensure_page`. Its fake shutdown sets `_page = None`, execution falls
    through to the launch, and on a machine where `playwright` is genuinely
    installed that launch is genuinely a browser. On CI, where it isn't, the
    import fails and `_ensure_page` returns None — so the test passed for a
    reason that had nothing to do with what it was testing, and the damage only
    ever appeared on the one machine nobody runs CI on.

    That is the same shape as `_pretend_playwright_is_installed` in
    `test_canva_display.py`, only inverted: there a missing package made a test
    vacuous, here it made a test *harmless*.

    So the guard goes here rather than in one test. Anything that reaches an
    actual launch fails loudly instead of taking over the screen, and a test
    that means to exercise launching patches `_launch_browser` itself — its own
    monkeypatch runs after this one and wins.

    Two tests drive `_launch_browser` on purpose, with a fake playwright
    underneath. They opt out with `@pytest.mark.allow_browser_launch`, which
    has to be written deliberately and is greppable.
    """
    if request.node.get_closest_marker("allow_browser_launch"):
        return

    from app.tools import canva_display, webstage

    async def refuse(_args):
        raise AssertionError(
            "a test tried to launch a real browser. Patch `_launch_browser` in "
            "the test if that is intended — see tests/conftest.py"
        )

    # Every module that can open a window, by name. The guard is per-module
    # on purpose (a shared launcher would be one patch point, but then the
    # module the guard did not name is the one that gets away — the exact
    # shape of the bug this fixture exists for). A test below asserts the
    # list is complete, so a third window cannot be added quietly.
    for module in (canva_display, webstage):
        monkeypatch.setattr(module, "_launch_browser", refuse)
    monkeypatch.setattr(webstage, "_page", None)
    monkeypatch.setattr(webstage, "_task", None)
    monkeypatch.setattr(webstage, "_wanted", None)

@pytest.fixture(autouse=True)
def _no_real_face_models(monkeypatch, request):
    """The tests must never load the 280MB face pack.

    Row two of the table in CLAUDE.md, arriving a third time. `buffalo_l`
    lives under `data/faces/`, which is gitignored — so CI never has it and
    every test here is green there, while the owner's machine, the only one
    that matters, spends seconds of ONNX loading inside a 20s timeout the
    first time any test touches `app.faces`.

    Same treatment as `_local_encoder`: stub the loader to return None. Every
    caller already handles "no models on this machine" because a showroom
    without them has to keep working. A test that means to exercise real
    recognition opts out with `@pytest.mark.allow_face_models`.
    """
    if request.node.get_closest_marker("allow_face_models"):
        return
    from app import faces
    from app.config import settings

    def refuse(det_size: int = 640):
        return None

    monkeypatch.setattr(faces, "_analyzer", refuse)
    # And the entrance camera stays shut. `_analyzer` already stops the
    # watcher from loading, but the switch is what the startup hook reads,
    # and a suite whose colour depends on the machine's .env is the thing
    # this file exists to prevent.
    monkeypatch.setattr(settings, "face_enabled", False)
    # The face knobs, pinned to the code defaults. The owner's .env holds
    # doorway-measured values (FACE_MIN_PX=50 among them), and the first
    # time it did, two enrolment tests turned red on this machine while CI
    # stayed green — the exact two-colour suite this fixture exists to
    # prevent. A test that wants another value patches it itself.
    monkeypatch.setattr(settings, "face_min_px", 110)
    # A greeter loop that reconnects forever never ends; tests hand it a
    # camera with N frames and expect the run to return when they are gone.
    monkeypatch.setattr(settings, "face_camera_retry_s", 0.0)
    monkeypatch.setattr(settings, "face_camera_source", "local")
    monkeypatch.setattr(settings, "face_threshold", 0.50)
    monkeypatch.setattr(settings, "face_confirm_frames", 3)
    # The robot machine's .env turns the arrival wait on; the per-frame
    # rule the rest of the suite was written against is the code default.
    monkeypatch.setattr(settings, "face_track_wait_s", 0.0)
    monkeypatch.setattr(settings, "face_cooldown_s", 600.0)
    monkeypatch.setattr(settings, "face_greet_strangers", True)
    # The "probably known" hold, the closer look, the turn and the
    # named-only handshake (9 Oct): off in code, maybe on in a robot .env.
    monkeypatch.setattr(settings, "face_probable_wait_s", 0.0)
    monkeypatch.setattr(settings, "face_probable_min_px", 36)
    monkeypatch.setattr(settings, "face_zoom_enabled", False)
    monkeypatch.setattr(settings, "face_zoom_photo", False)
    monkeypatch.setattr(settings, "face_turn_to_check", False)
    monkeypatch.setattr(settings, "face_turn_max_deg", 30.0)
    monkeypatch.setattr(settings, "face_handshake_named_only", False)


@pytest.fixture(autouse=True)
def _no_machine_unit_plans(monkeypatch):
    """The per-unit plan crops are a generated, gitignored folder: present on
    the machine that ran scripts/build_unit_plans.py, absent on CI. A card
    test must not gain a `floorplan` because of which machine runs it.
    tests/test_unit_plans.py points the folder at tmp itself."""
    from app import unit_plans

    unit_plans.reset()
    monkeypatch.setattr(unit_plans, "_folder", lambda: None)
    yield
    unit_plans.reset()


@pytest.fixture(autouse=True)
def _no_machine_project_report(monkeypatch, tmp_path):
    """The approved project report's chapters live in data/personal-docs/
    (gitignored): present on the owner's PC and the robot, absent on CI. A
    knowledge test must not gain report passages because of which machine
    runs it. tests/test_project_report.py builds its own folder + manifest."""
    from app import project_report

    project_report.reset()
    monkeypatch.setattr(project_report, "manifest_path",
                        lambda project_id=None: tmp_path / "no_project_report_approval.json")
    yield
    project_report.reset()


@pytest.fixture(autouse=True)
def _no_machine_array_captures(monkeypatch, tmp_path):
    """ARRAY_CAPTURE8 test files live in the machine's data/array_capture8/,
    and main.py's startup hook now sweeps that folder whatever the switches
    say — every TestClient startup would reach the real one (the robot's,
    if the suite runs there). tmp, like the unit plans above;
    tests/test_array_audio.py points it at its own tmp too."""
    from app import array_audio

    monkeypatch.setattr(array_audio, "_capture_dir", lambda: tmp_path / "array_capture8")


@pytest.fixture(autouse=True)
def _forget_what_was_heard():
    """"What the guest last said" is module state, and it now decides whether
    a tool acts. A test that leaves "ปิดสไลด์" behind arms the next one.
    `forget()` with no argument also clears the per-session captions,
    activity marks and spent captions of the body/nav guards."""
    from app import heard
    heard.forget()
    yield
    heard.forget()


@pytest.fixture(autouse=True)
def _no_real_tts(monkeypatch, tmp_path):
    """No test spends money, hits the network, or writes into the voice cache.

    `app/voice.py` renders a sentence with the API when the cache misses, and
    the cache is a real directory under `data/faces/`. Both halves of that
    belong to the machine, not to the suite: the same argument as the browser
    windows and the 280MB face pack. The cache is pointed at tmp and the
    renderer is stubbed to fail, which is the path every caller must already
    survive — the station beeps instead of speaking.
    """
    from app import voice

    def refuse(text: str, model: str):
        raise RuntimeError("no tts in tests")

    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    monkeypatch.setattr(voice, "_render", refuse)
    monkeypatch.setattr(voice, "_render_local",
                        lambda text: (_ for _ in ()).throw(RuntimeError("no tts")))
    monkeypatch.setattr(voice, "_local", None)
    # And the provider itself is pinned: the suite must not change colour
    # because the machine's .env chose a different voice.
    from app.config import settings as _settings
    monkeypatch.setattr(_settings, "tts_provider", "gemini")
    # The "no renders left today" latch is module state and correct in
    # production; carried between tests it makes the result depend on the
    # order they ran in. Exactly why conftest resets `_embedding_failed`.
    monkeypatch.setattr(voice, "_spent", set())
