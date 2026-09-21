"""A standalone TEXT chatbot for testing Emma's knowledge and answers.

The production Emma is speech-to-speech over Gemini Live; you cannot type at it
and read back what it said, which is exactly what you need to check whether an
answer is grounded, mixes projects, or invents a facility. This module is that
missing test surface: the *same* system prompt (`build_instructions`, condo
profile) and the *same* retrieval (`search_my_documents`, `search_condo_info`)
as the real robot, wired to a LOCAL model (Ollama) and a chat page.

Two honesty rails, because a test tool that flatters is worse than none:

- **It is not the production model.** It runs a local Ollama model
  (`qwen3:8b`) — chosen after the Gemini free-tier rate limit stopped the
  chatbot mid-test — not the production Live model or even its family. Treat it
  as a knowledge/behaviour probe, not a byte-exact preview. One upside: a local
  model has no Gemini training memory of "Embassy World", so what it says about
  the project comes from the retrieved documents or nowhere, which is exactly
  what makes an ungrounded answer easy to spot.
- **It shows its sources.** Every reply comes back with the documents the
  retrieval actually handed the model. A specific the reply states that appears
  in no source is the model inventing it — visible instead of argued.

Run it separately from `app.main`, on its own port, so nothing here touches the
sales-gallery server or its `.env`:

    .venv/Scripts/python.exe -m app.emma_chat            # http://127.0.0.1:8020
    .venv/Scripts/python.exe -m app.emma_chat --port 8021
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

logger = logging.getLogger("condo_voice.emma_chat")

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"

#: A LOCAL model via Ollama, not Gemini. Switched off the API on 2026-09-14
#: after the free-tier rate limit stopped the chatbot mid-test — the owner
#: wanted to hammer it without burning quota. It is neither the production Live
#: model nor its family, and it does not carry Gemini's training memory of
#: "Embassy World" (which was the source of the invented "155 m lagoon"); the
#: page says which model answered so no one mistakes it for production.
OLLAMA_URL = "http://localhost:11434/api/chat"
LOCAL_MODEL = "qwen3:8b"

#: How many retrieved chunks and prior turns to carry. Small on purpose: this
#: is a probe, and a huge context hides which document an answer leaned on.
MAX_CHUNKS = 4
MAX_HISTORY = 10
CHUNK_CHARS = 600


def _slide_text(s: object) -> str:
    """Pull readable text out of a slide hit without assuming its schema."""
    if isinstance(s, dict):
        for k in ("script_th", "script", "text", "caption_th", "caption", "title", "ask_th"):
            if s.get(k):
                return str(s[k])
        return json.dumps(s, ensure_ascii=False)[:CHUNK_CHARS]
    return str(s)[:CHUNK_CHARS]


def emma_reply(message: str, history: list[dict]) -> dict:
    """One turn: retrieve, ask the text model as Emma, report what it drew on."""
    from app.prompts import build_instructions
    from app.tools.mydocs import search_my_documents
    from app.tools.knowledge import search_condo_info

    sysinst = build_instructions(project_name="Embassy World", profile="condo")

    sources: list[str] = []
    retrieved: list[dict] = []
    ctx_parts: list[str] = []
    commercial = False

    # --- the library (mydocs): the corpus the owner is testing ---
    try:
        rd = search_my_documents(message)
    except Exception as exc:  # a broken index must not 500 the page
        rd = {"error": str(exc), "results": []}
    if rd.get("commercial_question"):
        commercial = True
    for x in rd.get("results", [])[:MAX_CHUNKS]:
        f = x.get("file", "?")
        t = (x.get("text") or "")[:CHUNK_CHARS]
        sources.append(f)
        retrieved.append({"tool": "search_my_documents", "file": f, "text": t})
        ctx_parts.append(f"[{f}]\n{t}")

    # --- the slides (condo_info): the approved deck narration ---
    try:
        rc = search_condo_info(message)
    except Exception as exc:
        rc = {"error": str(exc), "results": []}
    if rc.get("commercial_question"):
        commercial = True
    for s in rc.get("results", [])[:MAX_CHUNKS]:
        t = _slide_text(s)[:CHUNK_CHARS]
        label = "สไลด์"
        sources.append(label)
        retrieved.append({"tool": "search_condo_info", "file": label, "text": t})
        ctx_parts.append(f"[{label}]\n{t}")

    ctx = "\n\n".join(ctx_parts) or "(ไม่พบข้อมูลที่เกี่ยวข้องในคลัง)"
    note = ("\n[หมายเหตุระบบ: คำถามนี้เกี่ยวกับราคา/การเงิน ตัวเลขในคลังไม่มีผู้อนุมัติ "
            "ห้ามหยิบมาตอบ ให้แนะนำติดต่อฝ่ายขาย]" if commercial else "")

    messages = [{"role": "system", "content": sysinst}]
    for h in history[-MAX_HISTORY:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": str(h.get("text", ""))})
    user_text = f"[ผลค้นจากคลังเอกสาร]:\n{ctx}{note}\n\n[ลูกค้าถาม]: {message}"
    messages.append({"role": "user", "content": user_text})
    reply = _ollama_chat(messages)

    # de-dup sources, keep first-seen order
    uniq = list(dict.fromkeys(sources))
    return {"reply": reply, "sources": uniq, "retrieved": retrieved,
            "commercial": commercial, "model": LOCAL_MODEL}


def _ollama_chat(messages: list[dict]) -> str:
    """One chat completion from the local Ollama server.

    `think` is off so qwen3 answers directly instead of emitting a reasoning
    block; a stray `<think>` is still stripped in case an older server ignores
    the flag. Nothing here reaches the network beyond localhost, which is the
    whole point — no key, no quota, no rate limit.
    """
    import re
    import urllib.request

    body = json.dumps({
        "model": LOCAL_MODEL, "messages": messages, "stream": False,
        "think": False, "options": {"temperature": 0.4},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
    except Exception as exc:
        raise RuntimeError(
            "เรียก Ollama ที่ %s ไม่ได้ (%s) — เปิด Ollama แล้วมีโมเดล %s หรือยัง? "
            "(`ollama run %s`)" % (OLLAMA_URL, exc, LOCAL_MODEL, LOCAL_MODEL)) from exc
    text = (data.get("message", {}).get("content") or "").strip()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def emma_greeting() -> str:
    """The opening line the production session sends on connect, generated the
    same way — the GREETING instruction on top of Emma's prompt — so the owner
    can read and tune the real first sentence instead of guessing at it."""
    from app.prompts import build_instructions, greeting_for

    sysinst = build_instructions(project_name="Embassy World", profile="condo")
    messages = [
        {"role": "system", "content": sysinst},
        {"role": "user", "content": greeting_for("condo")
         + "\n\n(เริ่มบทสนทนา: ทักทายลูกค้าตอนนี้เลย พูดคำทักทายอย่างเดียว)"},
    ]
    return _ollama_chat(messages)


def create_app() -> FastAPI:
    app = FastAPI(title="Emma Chat Test")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(CLIENT / "emma-chat.html",
                            headers={"Cache-Control": "no-store"})

    @app.get("/greeting")
    async def greeting() -> JSONResponse:
        try:
            return JSONResponse({"ok": True, "reply": emma_greeting()})
        except Exception as exc:
            logger.exception("greeting failed")
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                status_code=500)

    @app.post("/chat")
    async def chat(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = str((body or {}).get("message", "")).strip()
        history = (body or {}).get("history") or []
        if not message:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        try:
            out = emma_reply(message, history if isinstance(history, list) else [])
            return JSONResponse({"ok": True, **out})
        except Exception as exc:
            logger.exception("emma_chat failed")
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                status_code=500)

    return app


def _open_when_up(url: str, delay: float = 1.8) -> None:
    def go() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Emma text chatbot (knowledge test)")
    parser.add_argument("--port", type=int, default=8020)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print(f"Emma chat test (local model {LOCAL_MODEL} via Ollama) — open {url}")
    print("NOTE: local model, not the production Live model — a probe, not a byte-exact preview.")
    print(f"      needs Ollama running with {LOCAL_MODEL} ({OLLAMA_URL}).")
    if not args.no_open:
        _open_when_up(url)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
