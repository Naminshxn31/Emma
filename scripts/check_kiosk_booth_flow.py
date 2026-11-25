"""Check-in kiosk -> Emma's face-scan booth -> back, in a real Chromium (23 Nov 2026; docs/emma-scan-booth-rehearsal.md).

Covers: the question before the pass, NO / 20 s -> the pass, the booth in English first and the Thai switch,
the registered name in the box, back to THIS guest's pass, a guest who walked off (#booth=idle) or no word
(reload / history) -> no pass shown, a booth closed after the question -> the pass, the name never in a URL.

    # 1) the check-in site's stand-in for Supabase (embassy-world-agent-preview, `npm ci` once):
    node test/mock-supabase.mjs 8095
    # 2) a TEST booth on 18771 (never the real one on 8771: kiosk.js is served with FACE_URL pointing here):
    GEMINI_API_KEY= BOOTH_RETURN_URL=http://localhost:8095/kiosk.html GUEST_SYNC_URL=         python scripts/booth_app.py --no-open --port 18771
    # 3)
    python scripts/check_kiosk_booth_flow.py <folder for screenshots>

A fresh stand-in each run (it remembers who checked in). Nothing is saved: /look is answered "all poses
done" and every visit to the booth ends by cancel.
"""

import asyncio
import json
import sys
import urllib.parse
from pathlib import Path

from playwright.async_api import async_playwright, expect

BASE = "http://localhost:8095/"
BOOTH = "http://127.0.0.1:18771/"
EV = "ew-agent-preview-2026"
CFG = ('window.EW_CONFIG={eventId:"%s",eventStart:"2026-11-30T10:00:00+07:00",supabaseUrl:"http://localhost:8095",'
       'supabaseKey:"sb_publishable_test",siteUrl:"",address:"",mapsUrl:"https://maps.example.com",privacyUrl:""};' % EV)
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
log, errors, urls = [], [], []


def ok(cond, what):
    log.append(("PASS " if cond else "FAIL ") + what)
    if not cond:
        raise AssertionError(what)


async def main():
    async with async_playwright() as pw:
        b = await pw.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                           "--disable-webgl"])
        c = await b.new_context(viewport={"width": 1280, "height": 800}, permissions=["camera"], locale="th-TH",
                                timezone_id="Asia/Bangkok")

        async def cfg(route):
            await route.fulfill(status=200, content_type="application/javascript", body=CFG)
        await c.route("**/assets/js/config.js", cfg)

        async def test_booth(route):
            resp = await route.fetch()
            body = (await resp.text()).replace('FACE_URL = "http://127.0.0.1:8771/"', 'FACE_URL = "%s"' % BOOTH)
            assert BOOTH in body
            await route.fulfill(response=resp, body=body)
        await c.route("**/assets/js/kiosk.js", test_booth)

        async def done_look(route):
            await route.fulfill(status=200, content_type="application/json", body='{"ok":true,"step":null,"box":null,"steps":[]}')
        await c.route(BOOTH + "look", done_look)

        p = await c.new_page()
        p.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
        p.on("framenavigated", lambda f: urls.append(f.url) if f == p.main_frame else None)
        try:
            await p.goto(BASE + "kiosk.html")
            await expect(p.locator("#s-setup")).to_be_visible(timeout=10000)
            await p.fill("#s-email", "kiosk@test.local"); await p.fill("#s-password", "password123"); await p.click("#setupBtn")
            await expect(p.locator("#signedIn")).to_be_visible(timeout=8000)
            await p.fill("#s-kiosk", "Reception 1")
            await p.click("#setupBtn")

            async def find(tel):
                await expect(p.locator("#s-home")).to_be_visible(timeout=10000)
                for ch in tel:
                    await p.click(f"#pad [data-k='{ch}']")
                await p.click("#findBtn")
                await expect(p.locator("#s-found")).to_be_visible(timeout=8000)
                await p.click("#confirmBtn")

            # ---- switch off: straight to the pass, as always ----
            await find("0826153748")
            await expect(p.locator("#s-welcome")).to_be_visible(timeout=10000)
            ok(await p.locator("section#s-face").is_hidden(), "off: confirm goes straight to the pass (WELCOME)")
            await p.click("#doneBtn")

            # ---- staff tick the switch ----
            await p.focus("#gear"); await p.keyboard.press("Enter")
            await expect(p.locator("#s-booth")).to_be_visible(timeout=5000)
            await p.check("#s-booth")
            await expect(p.locator("#setupOk")).to_contain_text("ตู้สแกนหน้าตอบแล้ว", timeout=8000)
            await p.click("#setupBtn")

            # ---- booth down: the pass, nobody asked ----
            async def refuse(route):
                await route.abort("connectionrefused")
            await p.route(BOOTH + "status", refuse)
            await find("0849182736")
            await expect(p.locator("#s-welcome")).to_be_visible(timeout=10000)
            ok(await p.locator("section#s-face").is_hidden(), "booth not running: the pass, nobody is asked")
            await p.unroute(BOOTH + "status")
            await p.click("#doneBtn")

            # ---- on: the question BEFORE the pass ----
            await find("0837261594")
            await expect(p.locator("section#s-face")).to_be_visible(timeout=10000)
            ok(await p.locator("#s-welcome").is_hidden(), "on: the question comes BEFORE the pass")
            await p.wait_for_timeout(1500)
            await p.screenshot(path=str(OUT / "1_face_question.png"))
            await p.click("#faceNo")
            await expect(p.locator("#s-welcome")).to_be_visible(timeout=5000)
            ok("Anan" in await p.text_content("#wName"), "NO THANKS: this guest's pass (WELCOME)")
            await p.click("#doneBtn")
            await find("0837261594")
            await expect(p.locator("section#s-face")).to_be_visible(timeout=10000)
            await p.wait_for_timeout(21000)
            ok(await p.locator("#s-welcome").is_visible() and "Anan" in await p.text_content("#wName"),
               "20 s untouched: the pass, not HOME (the guest still needs it)")
            await p.click("#doneBtn")

            # ---- YES: the booth in English, the switch to Thai, and back to this guest's pass ----
            await find("0837261594")
            await expect(p.locator("section#s-face")).to_be_visible(timeout=10000)
            await p.click("#faceBtn")
            await expect(p.locator("#sNotice")).to_be_visible(timeout=10000)
            ok(p.url == BOOTH, "the booth opened (%s)" % p.url)
            notice = await p.inner_text("#noticeText")
            ok(notice.startswith("Let Emma remember you") and "ลงทะเบียน" not in notice, "the notice is in English first")
            ok(await p.text_content("#yes") == "I agree" and await p.get_attribute("#langs [data-lang='en']", "aria-pressed") == "true",
               "buttons in English, EN marked")
            await p.screenshot(path=str(OUT / "2_booth_notice_en.png"))
            await p.click("#langs [data-lang='th']")
            await expect(p.locator("#noticeText")).to_contain_text("ให้ Emma จำคุณได้", timeout=5000)
            ok(await p.text_content("#yes") == "ยินยอม" and "Let Emma" not in await p.inner_text("#noticeText"),
               "ไทย: the notice and the buttons in Thai")
            await p.click("#langs [data-lang='en']")
            await expect(p.locator("#noticeText")).to_contain_text("Let Emma remember you", timeout=5000)
            await p.click("#yes")
            await expect(p.locator("#sName")).to_be_visible(timeout=10000)
            ok(await p.input_value("#name") == "Anan Wongsa" and await p.text_content("#save") == "Save",
               "the name screen in English, the registered name in the box")
            await p.click("#dropName")
            await expect(p.locator("#s-welcome")).to_be_visible(timeout=10000)
            ok(p.url.startswith(BASE + "kiosk.html") and "Anan" in await p.text_content("#wName"),
               "back from the booth: THIS guest's pass, at once")
            ok(await p.evaluate("sessionStorage.getItem('ew-kiosk-after-face')") is None, "the pass held for the trip is gone")
            enc = {"Anan", urllib.parse.quote("Anan Wongsa")}
            ok(not any(any(e in u for e in enc) for u in urls), "the name was never in any address")
            await p.click("#doneBtn")

            # ---- the next guest at the booth starts in English again ----
            await find("0813572468")
            await expect(p.locator("section#s-face")).to_be_visible(timeout=10000)
            await p.click("#faceBtn")
            await expect(p.locator("#sNotice")).to_be_visible(timeout=10000)
            ok((await p.inner_text("#noticeText")).startswith("Let Emma remember you"), "the next guest: English again")
            await p.click("#no")
            await expect(p.locator("#s-welcome")).to_be_visible(timeout=10000)
            ok("Somchai" in await p.text_content("#wName") or "สมชาย" in await p.text_content("#wName"),
               "No thanks at the booth: back to THIS guest's pass")
            await p.click("#doneBtn")

            # ---- no BOOTH_RETURN_URL: back by history, the pass still shows ----
            async def no_back(route):
                resp = await route.fetch()
                body = json.loads(await resp.text())
                body["return_url"] = None
                await route.fulfill(response=resp, body=json.dumps(body))
            await p.route(BOOTH + "status", no_back)
            await find("0826153748")
            await expect(p.locator("section#s-face")).to_be_visible(timeout=10000)
            await p.click("#faceBtn")
            await expect(p.locator("#sNotice")).to_be_visible(timeout=10000)
            await p.click("#no")
            await p.wait_for_timeout(2500)
            ok(await p.locator("#s-welcome").is_hidden() and await p.evaluate("sessionStorage.getItem('ew-kiosk-after-face')") is None,
               "no BOOTH_RETURN_URL (no word how the guest left): no pass shown, the held pass dropped")
            await p.unroute(BOOTH + "status")

            # ---- the booth says the guest walked off: their pass is never shown to the next person ----
            held = ('{"t": %d, "opt": {}, "g": {"name": "Malee", "surname": "Suksai", "agent": "x", "passNo": "AG-0002",'
                    ' "token": "t", "arrived": true}}')
            await p.evaluate("t => sessionStorage.setItem('ew-kiosk-after-face', t)", held % int(__import__("time").time() * 1000))
            await p.goto("about:blank")
            await p.goto(BASE + "kiosk.html#booth=idle")
            await expect(p.locator("#s-home")).to_be_visible(timeout=10000)
            await p.wait_for_timeout(800)
            ok(await p.locator("#s-welcome").is_hidden() and await p.evaluate("sessionStorage.getItem('ew-kiosk-after-face')") is None,
               "#booth=idle: HOME, the walked-off guest's pass dropped")
            await p.evaluate("t => sessionStorage.setItem('ew-kiosk-after-face', t)", held % int(__import__("time").time() * 1000))
            await p.goto("about:blank")
            await p.goto(BASE + "kiosk.html")
            await expect(p.locator("#s-home")).to_be_visible(timeout=10000)
            await p.wait_for_timeout(800)
            ok(await p.locator("#s-welcome").is_hidden(), "a plain reload with a pass held: not shown")
            await p.evaluate("t => sessionStorage.setItem('ew-kiosk-after-face', t)", held % int(__import__("time").time() * 1000))
            await p.goto("about:blank")
            await p.goto(BASE + "kiosk.html#booth=done")
            await expect(p.locator("#s-welcome")).to_be_visible(timeout=10000)
            ok("Malee" in await p.text_content("#wName") and "#" not in p.url, "#booth=done: the pass, the marker off the address bar")
            await p.click("#doneBtn")
            old = held.replace("%d", str(int(__import__("time").time() * 1000) - 6 * 60 * 1000))
            await p.evaluate("t => sessionStorage.setItem('ew-kiosk-after-face', t)", old)
            await p.goto("about:blank")
            await p.goto(BASE + "kiosk.html#booth=done")
            await expect(p.locator("#s-home")).to_be_visible(timeout=10000)
            await p.wait_for_timeout(800)
            ok(await p.locator("#s-welcome").is_hidden(), "a pass held more than 5 minutes: not shown")

            # ---- YES after the booth closed: the pass, not a dead page ----
            await find("0849182736")
            await expect(p.locator("section#s-face")).to_be_visible(timeout=10000)
            await p.route(BOOTH + "status", refuse)
            await p.click("#faceBtn")
            await expect(p.locator("#s-welcome")).to_be_visible(timeout=10000)
            ok(p.url.startswith(BASE) and "Kanya" in await p.text_content("#wName"),
               "YES with the booth closed since the question: this guest's pass, no navigation")
            await p.unroute(BOOTH + "status")
        finally:
            print("\n".join(log))
            print("page errors:", errors or "none")
            await b.close()


asyncio.run(main())
