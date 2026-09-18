"""Tests for printing.

This is the first tool in the project that does something irreversible. Every
other one changes a picture, a light, or a state dict — all of which can be
put back. Paper cannot, and it comes out in front of a customer.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config import settings


@pytest.fixture
def catalogue(monkeypatch, tmp_path):
    """A folder with two real files and a manifest naming them."""
    from app.tools import documents

    (tmp_path / "brochure.pdf").write_bytes(b"%PDF-1.4 brochure")
    (tmp_path / "booking.pdf").write_bytes(b"%PDF-1.4 booking")
    approved = {
        "source_id": "printable_documents", "project_id": settings.project_id,
        "approval_status": "approved", "approved_by": "test_reviewer",
        "approved_at": "2026-01-01T00:00:00+07:00",
        "effective_at": "2026-01-01T00:00:00+07:00",
        "disclosure_scope": "customer", "content_state": "existing",
        "approval_unit": "entire_document",
    }
    (tmp_path / "catalogue.json").write_text(json.dumps({"documents": [
        {"name": "โบรชัวร์โครงการ", "file": "brochure.pdf",
         "aliases": ["โบรชัวร์", "brochure"], **approved},
        {"name": "ใบจอง", "file": "booking.pdf", "aliases": ["ฟอร์มจอง"],
         **approved},
    ]}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    documents.reload_catalogue()
    yield tmp_path
    documents.reload_catalogue()


def run(coro):
    return asyncio.run(coro)


# ==================== only what the team put there ====================


def test_it_prints_the_document_that_was_asked_for(catalogue, monkeypatch):
    from app.tools import documents

    sent = []
    monkeypatch.setattr(documents, "send_to_printer",
                        lambda path, copies: (sent.append((path.name, copies)), (True, "ok"))[1])

    out = run(documents.print_document("ขอโบรชัวร์หน่อย"))
    assert out["ok"] is True
    assert sent == [("brochure.pdf", 1)]


def test_an_unknown_document_prints_nothing_at_all(catalogue, monkeypatch):
    """The tempting failure is to print the nearest thing. Whatever comes out
    of the printer is out; a guest who asked for a price list and received a
    brochure has been given a wrong answer on paper, with the project's logo
    on it. Refusing is the correct outcome."""
    from app.tools import documents

    sent = []
    monkeypatch.setattr(documents, "send_to_printer",
                        lambda path, copies: (sent.append(path.name), (True, "ok"))[1])

    out = run(documents.print_document("ตารางผ่อนดาวน์"))
    assert out["ok"] is False
    assert sent == [], "printed something the guest did not ask for"
    assert "โบรชัวร์โครงการ" in out["available"]


def test_a_catalogue_entry_cannot_reach_outside_its_folder(monkeypatch, tmp_path):
    """The catalogue is data, and data is editable by anyone with the folder
    open. A `file` of "../../.env" must not become a print job."""
    from app.tools import documents

    secret = tmp_path / "secret.txt"
    secret.write_text("api keys", encoding="utf-8")
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / "catalogue.json").write_text(json.dumps({"documents": [
        {"name": "ของลับ", "file": "../secret.txt"},
    ]}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(settings, "documents_dir", str(docs))
    documents.reload_catalogue()
    assert documents.load_catalogue() == [], "escaped the documents folder"
    documents.reload_catalogue()


def test_a_listed_file_that_is_missing_is_dropped(monkeypatch, tmp_path):
    """Better to say "I don't have that" than to fail at the printer with the
    guest already waiting for the sound."""
    from app.tools import documents

    (tmp_path / "catalogue.json").write_text(json.dumps({"documents": [
        {"name": "โบรชัวร์", "file": "not-there.pdf"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(settings, "documents_dir", str(tmp_path))
    documents.reload_catalogue()
    assert documents.load_catalogue() == []
    documents.reload_catalogue()


# ==================== paper does not come back ====================


def test_copies_are_capped(catalogue, monkeypatch):
    """A guest was transcribed saying "อะไรนะ 50". Mishearing is routine here,
    and on this argument a mishearing becomes fifty sheets of paper before
    anybody can say anything."""
    from app.tools import documents

    sent = []
    monkeypatch.setattr(documents, "send_to_printer",
                        lambda path, copies: (sent.append(copies), (True, "ok"))[1])
    monkeypatch.setattr(settings, "print_max_copies", 3)

    out = run(documents.print_document("โบรชัวร์", copies=50))
    assert sent == [3], "printed %s copies" % sent
    assert out["capped_from"] == 50
    assert "3" in out["instruction"], "the guest is not told how many actually printed"


def test_a_nonsense_copy_count_falls_back_to_one(catalogue, monkeypatch):
    from app.tools import documents

    sent = []
    monkeypatch.setattr(documents, "send_to_printer",
                        lambda path, copies: (sent.append(copies), (True, "ok"))[1])

    run(documents.print_document("โบรชัวร์", copies="ห้าสิบ"))
    assert sent == [1]


def test_zero_or_negative_still_prints_one(catalogue, monkeypatch):
    from app.tools import documents

    sent = []
    monkeypatch.setattr(documents, "send_to_printer",
                        lambda path, copies: (sent.append(copies), (True, "ok"))[1])

    run(documents.print_document("โบรชัวร์", copies=0))
    run(documents.print_document("โบรชัวร์", copies=-5))
    assert sent == [1, 1]


# ==================== a printer that isn't working ====================


def test_a_failed_print_is_never_reported_as_success(catalogue, monkeypatch):
    """The worst outcome is the robot saying "พิมพ์ให้แล้วค่ะ" while the tray
    stays empty — the guest waits, then finds out, then trusts nothing else it
    said. Out of paper is a normal Tuesday in a sales gallery."""
    from app.tools import documents

    monkeypatch.setattr(documents, "send_to_printer",
                        lambda path, copies: (False, "out of paper"))

    out = run(documents.print_document("โบรชัวร์"))
    assert out["ok"] is False
    assert "ห้ามบอกลูกค้าว่าพิมพ์ให้แล้ว" in out["instruction"]


def test_printing_disabled_does_not_raise(catalogue, monkeypatch):
    from app.tools import documents

    monkeypatch.setattr(settings, "print_enabled", False)
    worked, detail = documents.send_to_printer(catalogue / "brochure.pdf", 1)
    assert worked is False and "PRINT_ENABLED" in detail


def test_printing_is_marked_as_needing_confirmation():
    """Every other tool in this project is reversible — a picture, a light, a
    state dict. This one is not, and the registry should say so."""
    from app.tools import load_tools, registry  # noqa: F401

    load_tools()
    entry = registry.get("print_document")
    assert entry is not None and entry.confirm is True


def test_the_configured_printer_is_actually_used(monkeypatch, tmp_path):
    """PRINTER_NAME was read and then ignored on Windows — the only platform
    this runs on. It always used the current default, and the gallery machine
    has "Let Windows manage my default printer" switched on, which makes the
    default *the last printer used*. One of the installed printers is
    "Microsoft Print to PDF", which does not print: it opens a Save-As dialog
    and waits for a human. The robot would have announced success to a guest
    while a modal sat on a screen nobody was looking at.
    """
    import subprocess

    from app.tools import documents

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(documents.sys, "platform", "win32")
    monkeypatch.setattr(settings, "print_enabled", True)
    monkeypatch.setattr(settings, "printer_name", "Brother DCP-L3560CDW series Printer")
    # Pin the helper off at the *function*, not the setting. Emptying
    # PDF_PRINT_EXE isn't enough: `find_pdf_helper()` then goes looking through
    # %LOCALAPPDATA% and friends and finds the SumatraPDF that is genuinely
    # installed on the gallery PC, so the helper branch ran anyway and never
    # produced a `PrintTo`. On CI nothing is found, the PowerShell branch ran,
    # and the test passed — reading the developer's filesystem rather than the
    # code. Twice: pinning the setting alone was the first, insufficient fix.
    # The helper branch has its own test.
    monkeypatch.setattr(documents, "find_pdf_helper", lambda: "")
    monkeypatch.setattr(documents.subprocess, "run", fake_run)

    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"%PDF")
    worked, _ = documents.send_to_printer(doc, 1)

    assert worked is True
    command = " ".join(calls[0])
    assert "PrintTo" in command, "used the default printer instead of the named one"
    assert "Brother DCP-L3560CDW series Printer" in command


def test_the_file_is_handed_over_as_an_absolute_path(monkeypatch, tmp_path):
    """Windows reported "No application is associated with the specified file"
    for what was really a path problem.

    `Start-Process` resolves a relative path against the PowerShell process's
    own directory rather than ours, so `data\\documents\\brochure.pdf` reached
    it as something that did not exist — and the error Windows chose to raise
    described file associations, which sent the first debugging attempt in
    completely the wrong direction.
    """
    import subprocess

    from app.tools import documents

    calls = []
    monkeypatch.setattr(documents.sys, "platform", "win32")
    monkeypatch.setattr(settings, "print_enabled", True)
    monkeypatch.setattr(settings, "printer_name", "Brother")
    monkeypatch.setattr(documents.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd),
                                           subprocess.CompletedProcess(cmd, 0))[1])

    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"%PDF")

    # Relative, the way it arrives from the catalogue when DOCUMENTS_DIR is
    # the default "data/documents". Passing an already-absolute path here
    # would let the test pass with the fix removed, which it did once.
    monkeypatch.chdir(tmp_path)
    relative = Path("x.pdf")
    assert not relative.is_absolute()
    documents.send_to_printer(relative, 1)

    command = " ".join(calls[0])
    assert str(doc.resolve()) in command, (
        "handed over %r — Start-Process resolves that against its own "
        "directory, not ours" % command
    )


def test_a_missing_pdf_association_is_explained_not_echoed(monkeypatch, tmp_path):
    """Raw PowerShell stack traces are not something an operator in a sales
    gallery can act on. The message has to say which of the two causes it is:
    no PRINTER_NAME set, or genuinely no PDF handler installed."""
    import subprocess

    from app.tools import documents

    def blow_up(cmd, **kw):
        raise subprocess.CalledProcessError(
            1, cmd, stderr=b"Start-Process : This command cannot be run due to the "
                           b"error: No application is associated with the specified file")

    monkeypatch.setattr(documents.sys, "platform", "win32")
    monkeypatch.setattr(settings, "print_enabled", True)
    monkeypatch.setattr(documents.subprocess, "run", blow_up)

    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"%PDF")

    monkeypatch.setattr(settings, "printer_name", "")
    worked, detail = documents.send_to_printer(doc, 1)
    assert worked is False
    assert "PRINTER_NAME" in detail, "did not point at the unset setting"

    monkeypatch.setattr(settings, "printer_name", "Brother")
    _, detail = documents.send_to_printer(doc, 1)
    assert "PDF" in detail and "PRINTER_NAME" not in detail


def test_a_configured_pdf_helper_bypasses_windows_associations(monkeypatch, tmp_path):
    """The gallery machine has no application associated with .pdf, so the
    shell verb fails whatever printer is named — the error is about the file
    type, not the printer.

    A kiosk PC gets reimaged and set up by whoever is free, so depending on a
    file association is depending on something nobody owns. A bundled printer
    executable removes the dependency entirely.
    """
    import subprocess

    from app.tools import documents

    calls = []
    monkeypatch.setattr(documents.sys, "platform", "win32")
    monkeypatch.setattr(settings, "print_enabled", True)
    monkeypatch.setattr(settings, "printer_name", "Brother DCP-L3560CDW series Printer")
    helper = tmp_path / "SumatraPDF.exe"
    helper.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "pdf_print_exe", str(helper))
    monkeypatch.setattr(documents.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd),
                                           subprocess.CompletedProcess(cmd, 0))[1])

    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"%PDF")
    worked, _ = documents.send_to_printer(doc, 2)

    assert worked is True
    assert len(calls) == 2, "asked for two copies, sent %d jobs" % len(calls)
    assert calls[0][0] == str(helper)
    assert "-print-to" in calls[0] and "Brother DCP-L3560CDW series Printer" in calls[0]
    assert "-silent" in calls[0], "a dialog on a kiosk screen is a stuck robot"
    assert "powershell" not in " ".join(calls[0]).lower()


def test_the_helper_falls_back_to_the_default_printer(monkeypatch, tmp_path):
    import subprocess

    from app.tools import documents

    calls = []
    monkeypatch.setattr(settings, "print_enabled", True)
    monkeypatch.setattr(settings, "printer_name", "")
    # A helper that actually exists — a configured path that does not is a
    # different case, and `find_pdf_helper` deliberately refuses it.
    helper = tmp_path / "sumatra"
    helper.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "pdf_print_exe", str(helper))
    monkeypatch.setattr(documents.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd),
                                           subprocess.CompletedProcess(cmd, 0))[1])

    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"%PDF")
    documents.send_to_printer(doc, 1)
    assert "-print-to-default" in calls[0]


def test_the_pdf_helper_is_found_without_being_configured(monkeypatch, tmp_path):
    """The first attempt at this failed on a path.

    The example in the docs said `C:\\tools\\SumatraPDF.exe`; the installer
    puts it under %LOCALAPPDATA%. The result was "ไม่พบโปรแกรมสำหรับสั่งพิมพ์"
    on a machine where the program was installed and visibly running. Asking
    somebody to locate an executable is a step that gets got wrong again on
    the next machine, so it looks in the usual places itself.
    """
    from app.tools import documents

    installed = tmp_path / "SumatraPDF" / "SumatraPDF.exe"
    installed.parent.mkdir()
    installed.write_text("", encoding="utf-8")

    monkeypatch.setattr(settings, "pdf_print_exe", "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    # The real table uses Windows separators because that is the only place
    # it runs; the test has to build the tail the way this platform spells it.
    monkeypatch.setattr(documents, "_SUMATRA_GUESSES",
                        [("LOCALAPPDATA", str(Path("SumatraPDF") / "SumatraPDF.exe"))])

    assert documents.find_pdf_helper() == str(installed)


def test_a_configured_path_that_does_not_exist_says_so(monkeypatch, tmp_path):
    """Silently falling back to a search would hide a typo in .env, and the
    operator would keep believing the setting they wrote is the one in use."""
    import subprocess

    from app.tools import documents

    monkeypatch.setattr(settings, "pdf_print_exe", r"C:\tools\SumatraPDF.exe")
    assert documents.find_pdf_helper() == ""

    monkeypatch.setattr(settings, "print_enabled", True)
    monkeypatch.setattr(documents.sys, "platform", "win32")

    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(documents.subprocess, "run", missing)
    doc = tmp_path / "x.pdf"
    doc.write_bytes(b"%PDF")
    _, detail = documents.send_to_printer(doc, 1)
    assert r"C:\tools\SumatraPDF.exe" in detail, "did not name the path that was wrong"
