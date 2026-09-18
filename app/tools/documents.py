"""Print a document the sales team prepared in advance.

The robot does not *make* documents. It picks one out of a folder somebody
else filled and approved, and sends it to a printer. That distinction is the
entire safety story here.

The first version of this idea was "print the contract", and it had no safe
form: a contract carries prices that are still blank in `condo_facts.json`,
nobody has signed off the content (`approved_by` is empty), and the robot
cannot verify who is standing in front of it. Generating one would have moved
liability from a person onto a machine that mishears.

Choosing from a fixed catalogue removes all of that. The team writes the PDF,
the team owns what it says, and the robot's only job is "the guest asked for
the brochure — print the brochure".

Two guards on top of that, both from things already seen in the logs:

- **Whitelist, never a path.** The tool takes a name and looks it up. It never
  takes a filename from the model, so there is no way to reach a file outside
  the folder, however the request is phrased.
- **A copy limit.** A guest was transcribed saying "อะไรนะ 50" — mishearing is
  routine here, and paper does not come back. Copies are capped, and anything
  above one has to be asked for explicitly.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import settings
from app.tools.registry import tool

logger = logging.getLogger("condo_voice.documents")

def _dir() -> Path:
    return Path(settings.documents_dir).expanduser()


def load_catalogue() -> list[dict]:
    """Documents the team has made available, from catalogue.json.

    A manifest rather than a directory listing: dropping a PDF into the
    folder is not enough. In a customer session every document must carry
    source-level project and disclosure approval; no chunk-level guessing.
    """
    path = _dir() / "catalogue.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw["documents"] if isinstance(raw, dict) else raw
    except Exception:
        logger.info("no document catalogue at %s — printing is unavailable", path)
        return []

    from app.knowledge_policy import evaluate_claim
    ready = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if settings.assistant_profile == "condo":
            decision = evaluate_claim(entry, settings.project_id)
            if (entry.get("source_id") != "printable_documents"
                    or entry.get("approval_unit") != "entire_document"
                    or entry.get("mixed_status") is True
                    or not decision.allowed):
                logger.info("document policy %s", decision.trace())
                continue
        file = _dir() / (entry.get("file") or "")
        # Resolve and confirm it is really inside the folder. A catalogue is
        # data, and data can be edited by anyone with the folder open.
        try:
            inside = file.resolve().is_relative_to(_dir().resolve())
        except Exception:
            inside = False
        if not inside or file.suffix.lower() != ".pdf":
            logger.warning("catalogue entry %r points outside the documents folder — ignored",
                           entry.get("name"))
            continue
        if not file.exists():
            logger.warning("catalogue lists %r but %s is missing", entry.get("name"), file)
            continue
        ready.append({"name": entry.get("name", ""),
                      "about": entry.get("about", ""),
                      "aliases": entry.get("aliases", []),
                      "source_id": entry.get("source_id"),
                      "project_id": entry.get("project_id"),
                      "_path": file})

    logger.info("documents available to print: %s",
                ", ".join(e.get("name", "?") for e in ready) or "(none)")
    return ready


def reload_catalogue() -> None:
    """Compatibility hook; the small catalogue is checked afresh each call."""


def find(request: str) -> dict | None:
    """Match what the guest asked for against the catalogue.

    Deliberately simple. The catalogue is a handful of entries with names the
    team chose, so token overlap is enough — and being *unable* to match is the
    correct outcome for anything unfamiliar. A fuzzy match that reaches for the
    nearest document would print the wrong thing, and the wrong thing has
    already come out of the printer by the time anyone notices.
    """
    from app.tools.retrieval import robust_tokens

    wanted = set(robust_tokens(request or ""))
    if not wanted:
        return None

    best, best_score = None, 0
    for entry in load_catalogue():
        words = set(robust_tokens(entry.get("name", "")))
        for alias in entry.get("aliases", []):
            words |= set(robust_tokens(alias))
        score = len(wanted & words)
        if score > best_score:
            best, best_score = entry, score
    return best


#: Where SumatraPDF actually installs itself. The per-user location is the
#: default in recent versions, and it is not the one anybody guesses.
_SUMATRA_GUESSES = [
    ("LOCALAPPDATA", r"SumatraPDF\SumatraPDF.exe"),
    ("LOCALAPPDATA", r"Programs\SumatraPDF\SumatraPDF.exe"),
    ("PROGRAMFILES", r"SumatraPDF\SumatraPDF.exe"),
    ("PROGRAMFILES(X86)", r"SumatraPDF\SumatraPDF.exe"),
]


def find_pdf_helper() -> str:
    r"""The configured PDF printer, or one found in the usual places.

    Searching is here because the first attempt at this failed on a path: the
    documentation example said `C:\tools\SumatraPDF.exe`, that is not where
    the installer puts it, and the result was "ไม่พบโปรแกรมสำหรับสั่งพิมพ์" on
    a machine where the program was installed and running. Asking somebody to
    locate an executable is a step that will be got wrong again on the next
    machine, so don't ask.
    """
    configured = (settings.pdf_print_exe or "").strip()
    if configured:
        return configured if Path(configured).exists() else ""

    import os

    # Read the variables directly rather than via `expandvars`, which only
    # understands %VAR% on Windows and leaves the literal text everywhere else
    # — which made this silently find nothing under test.
    for variable, tail in _SUMATRA_GUESSES:
        root = os.environ.get(variable)
        if not root:
            continue
        candidate = Path(root) / tail
        if candidate.exists():
            logger.info("found a PDF printing helper at %s", candidate)
            return str(candidate)
    found = shutil.which("SumatraPDF") or shutil.which("sumatrapdf")
    return found or ""


def send_to_printer(path: Path, copies: int) -> tuple[bool, str]:
    """Hand the file to the OS print queue. Returns (worked, detail).

    Never raises. A printer that is out of paper, off, or absent is a normal
    Tuesday in a sales gallery, and the robot has to be able to say so out loud
    rather than fall over.
    """
    if not settings.print_enabled:
        return False, "การปริ้นถูกปิดไว้ (PRINT_ENABLED=false)"

    printer = (settings.printer_name or "").strip()
    # Absolute, always. `Start-Process` resolves a relative path against the
    # PowerShell process's own directory, not ours, and when it cannot find
    # the file Windows reports it as "No application is associated with the
    # specified file" — an error about file associations that is really an
    # error about the path, which sent the first debugging attempt in
    # completely the wrong direction.
    path = path.resolve()
    try:
        # A dedicated PDF printer, if one is configured. Preferred over the
        # shell verb because it does not care what this Windows install has
        # associated with .pdf — and on the gallery machine that association
        # is simply absent, so `PrintTo` fails with "No application is
        # associated" no matter which printer is named.
        #
        # A kiosk PC gets reimaged, handed between people, and set up by
        # whoever is free. Depending on a file association there is depending
        # on something nobody owns. SumatraPDF is ~10MB, portable, needs no
        # install, and prints silently:
        #   SumatraPDF.exe -print-to "Brother ..." -silent file.pdf
        helper = find_pdf_helper()
        if helper:
            base = [helper]
            base += ["-print-to", printer] if printer else ["-print-to-default"]
            base += ["-silent", str(path)]
            for _ in range(copies):
                subprocess.run(base, check=True, capture_output=True, timeout=60)
        elif sys.platform.startswith("win"):
            # `PrintTo` names the printer; plain `Print` uses whatever Windows
            # currently considers default — and "Let Windows manage my default
            # printer" makes that *the last one used*. On the gallery machine
            # the installed list includes "Microsoft Print to PDF", which does not
            # print at all: it opens a Save-As dialog and waits for a human.
            # The robot would report success while a modal sat on the screen.
            #
            # An earlier version of this function read `printer` and then never
            # used it on Windows, so PRINTER_NAME was silently ignored on the
            # only platform this actually runs on.
            if printer:
                inner = f'Start-Process -FilePath "{path}" -Verb PrintTo -ArgumentList "{printer}"'
            else:
                inner = f'Start-Process -FilePath "{path}" -Verb Print'
            args = ["powershell", "-NoProfile", "-Command", inner]
            for _ in range(copies):
                subprocess.run(args, check=True, capture_output=True, timeout=30)
        else:
            base = ["lp", "-n", str(copies)]
            if printer:
                base += ["-d", printer]
            if shutil.which("lp") is None:
                return False, "ไม่พบคำสั่งพิมพ์ในระบบ"
            subprocess.run(base + [str(path)], check=True, capture_output=True, timeout=30)
    except FileNotFoundError:
        configured = (settings.pdf_print_exe or "").strip()
        if configured:
            return False, (
                "ตั้ง PDF_PRINT_EXE ไว้ที่ %s แต่ไม่มีไฟล์นั้นอยู่จริง — "
                "หา path ที่ถูกต้องด้วย: Get-Command SumatraPDF.exe หรือดูใน "
                "%%LOCALAPPDATA%%\\SumatraPDF\\" % configured
            )
        return False, "ไม่พบโปรแกรมสำหรับสั่งพิมพ์"
    except subprocess.TimeoutExpired:
        return False, "สั่งพิมพ์แล้วไม่ตอบกลับ"
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()
        # Windows says "no application is associated" for two quite different
        # situations, and the operator needs to know which one they have.
        if "No application is associated" in detail:
            if not printer:
                return False, (
                    "Windows ไม่รู้จะใช้โปรแกรมไหนพิมพ์ PDF — ยังไม่ได้ตั้ง PRINTER_NAME "
                    "ใน .env ให้ใส่ชื่อเครื่องพิมพ์ให้ตรงกับใน Settings แล้วลองใหม่"
                )
            return False, (
                "Windows ไม่มีโปรแกรมที่ผูกกับไฟล์ PDF สำหรับสั่งพิมพ์ "
                "แก้ได้สองทาง: (1) ตั้งโปรแกรมเริ่มต้นของไฟล์ .pdf ใน Windows "
                "(2) ตั้ง PDF_PRINT_EXE ให้ชี้ไปที่ SumatraPDF.exe ซึ่งไม่ต้องพึ่ง "
                "การผูกไฟล์ — ทางที่สองเหมาะกับเครื่องที่ตั้งไว้ในห้องขายมากกว่า"
            )
        return False, detail or "เครื่องพิมพ์ปฏิเสธงาน"
    except Exception as exc:
        return False, str(exc)
    return True, "ส่งเข้าคิวพิมพ์แล้ว"


@tool(
    name="print_document",
    description=(
        "สั่งพิมพ์เอกสารที่ทีมขายเตรียมไว้ ใช้เมื่อลูกค้าขอเอกสาร เช่น ปริ้นโบรชัวร์ "
        "ขอใบจอง ขอเอกสารโครงการ พิมพ์ผังห้องให้หน่อย — "
        "พิมพ์ได้เฉพาะเอกสารที่มีในรายการเท่านั้น ถ้าไม่มีให้บอกลูกค้าตรงๆ"
    ),
    parameters={
        "type": "object",
        "properties": {
            "document": {
                "type": "string",
                "description": "ชื่อเอกสารที่ลูกค้าขอ เช่น 'โบรชัวร์', 'ใบจอง', 'ผังห้อง'",
            },
            "copies": {
                "type": "integer",
                "description": "จำนวนชุด ค่าปกติคือ 1 ใส่มากกว่านี้เฉพาะเมื่อลูกค้าระบุชัดเจน",
            },
        },
        "required": ["document"],
    },
    tags=["documents"],
    # Paper does not come back. Not reversible, unlike every other tool here.
    confirm=True,
    exclusive=True,
    # Spooling a job takes a few seconds and the guest is standing there
    # waiting for the sound of the printer.
    paced=True,
)
async def print_document(document: str, copies: int = 1) -> dict:
    from app.tool_io import run_blocking

    return await run_blocking(_print_document, document, copies)


def _print_document(document: str, copies: int = 1) -> dict:
    available = load_catalogue()
    if not available:
        return {
            "ok": False, "error": "no documents",
            "instruction": "ตอนนี้ยังไม่มีเอกสารให้พิมพ์ ให้บอกลูกค้าตรงๆ แล้วแนะนำให้ติดต่อฝ่ายขาย",
        }

    entry = find(document)
    if entry is None:
        names = ", ".join(e.get("name", "") for e in available)
        return {
            "ok": False, "error": "not in catalogue", "available": names,
            "instruction": (
                "ไม่มีเอกสารนี้ในรายการ ห้ามพิมพ์อย่างอื่นแทน "
                "ให้บอกลูกค้าว่ามีเอกสารอะไรบ้าง: " + names
            ),
        }

    # Mishearings become quantities here. "อะไรนะ 50" appeared in a real
    # transcript, and if that had landed on this argument it would have been
    # fifty sheets of paper before anyone could say anything.
    try:
        wanted = int(copies)
    except (TypeError, ValueError):
        wanted = 1
    capped = max(1, min(wanted, settings.print_max_copies))
    if capped != wanted:
        logger.warning("asked for %s copies of %r — capped at %d",
                       copies, entry.get("name"), capped)

    from app import turnlog

    worked, detail = send_to_printer(entry["_path"], capped)
    turnlog.record("print", document=entry.get("name"), copies=capped,
                   worked=worked, detail=detail)
    logger.info("print %r x%d: %s", entry.get("name"), capped, detail)

    if not worked:
        return {
            "ok": False, "error": "printer", "detail": detail,
            "instruction": (
                "พิมพ์ไม่สำเร็จ ห้ามบอกลูกค้าว่าพิมพ์ให้แล้ว "
                "ให้บอกตรงๆ ว่าเครื่องพิมพ์มีปัญหา แล้วแนะนำให้ติดต่อเจ้าหน้าที่"
            ),
        }

    out = {"ok": True, "document": entry.get("name"), "copies": capped}
    if capped != wanted:
        out["capped_from"] = wanted
        out["instruction"] = (
            "พิมพ์ให้ %d ชุด (จำกัดไว้ที่ %d ชุดต่อครั้ง) "
            "ให้บอกลูกค้าตามจริงว่าพิมพ์กี่ชุด ถ้าต้องการมากกว่านี้ให้ติดต่อเจ้าหน้าที่"
            % (capped, settings.print_max_copies)
        )
    else:
        out["instruction"] = "พิมพ์แล้ว บอกลูกค้าสั้นๆ ว่ากำลังออกมาจากเครื่องพิมพ์"
    return out


@tool(
    name="list_documents",
    description="บอกว่ามีเอกสารอะไรให้พิมพ์บ้าง ใช้เมื่อลูกค้าถามว่ามีเอกสารอะไร",
    parameters={"type": "object", "properties": {}},
    tags=["documents"],
)
def list_documents() -> dict:
    available = load_catalogue()
    if not available:
        return {"ok": True, "documents": [],
                "instruction": "ยังไม่มีเอกสารให้พิมพ์ ให้บอกลูกค้าตรงๆ"}
    return {
        "ok": True,
        "documents": [
            {"name": e.get("name"), "about": e.get("about", "")} for e in available
        ],
        "instruction": "บอกรายชื่อเอกสารสั้นๆ ห้ามเพิ่มเอกสารที่ไม่มีในรายการ",
    }
