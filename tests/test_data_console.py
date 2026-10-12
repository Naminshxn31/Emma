"""The owner's approval desk at `/data-console`.

What is actually being tested here is a safety gate, so the tests are written
from the gate's point of view rather than the page's:

* the verdict is read back through the **real** `evaluate_claim`, never by
  checking that the console wrote the strings it said it would write. This
  repo has been burned by measuring one layer away from the thing that
  decides ("eval looks at hits[0], the tool scans every hit"), and an
  approval console that writes six fields nobody re-evaluates is exactly
  that bug with a person's name attached;
* every test runs against a temp copy of `data/`. The owner's real files are
  the ones he presses the button on, and a suite one bug away from stamping
  them would be worse than no suite;
* the copy is then **normalised to an explicit draft baseline** before any
  test looks at it. The first version of this file copied the live files and
  asserted their current state ("project_facts starts denied", "exactly four
  blank facts"), which bound the suite's colour to mutable owner data: the
  first real approval would have turned nine tests red with no code change,
  and it did exactly that for five minutes during review. Same rule as
  `ชุดเทสต์ต้องไม่เปลี่ยนสีตาม .env ของเครื่อง`, with a data file in place of
  the .env. Counts and blank-fact lists are derived from the copied file, so
  they still have teeth against a regenerated deck without being a snapshot
  of one afternoon's data;
* nothing here invents project data. No price, no room size, no promotion,
  no opening hour appears in any fixture — the blank facts are exercised by
  proving they stay blank.
"""
from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import data_console
from app import main as main_module
from app.config import settings
from app.knowledge_policy import evaluate_claim
from app.main import app


REPO = Path(__file__).resolve().parents[1]
PROJECT = "embassy_world"
TOKEN = "test-token-1234"

FACTS = "data/projects/embassy_world/facts/condo_facts.json"
RULES = "data/projects/embassy_world/presentations/sales_context.json"
CATALOG = "data/projects/embassy_world/presentations/slides/index.json"
QA = "data/projects/embassy_world/sales/qa_script.json"
#: The markdown copy the console regenerates. Read from the module so a
#: rename cannot leave this suite watching a path nothing writes any more.
DOC = data_console.QA_EXPORT_RELATIVE
REGISTRY = "data/registry/source_registry.json"

#: Every repository file this console is able to write, fingerprinted at
#: import time — before a single test has run. The last test in this file
#: compares them again. A missing `DATA_ROOT` monkeypatch anywhere, or an
#: export path that resolves against the repo instead of the sandbox, stamps
#: or rewrites the owner's real data, and that is the one failure mode this
#: suite must never have: these are the files he presses the button on.
_WATCHED = (REGISTRY, FACTS, RULES, CATALOG, QA, DOC, data_console.AUDIT_RELATIVE)
_REAL_STATE = {
    relative: ((REPO / relative).stat().st_mtime_ns if (REPO / relative).is_file() else None)
    for relative in _WATCHED
}

#: The closed position, written into the copy so that every "still denied"
#: assertion below is asserting the fixture's own baseline rather than
#: whatever the owner last pressed a button on. `preliminary_concept` is the
#: pre-approval caveat setting the revoke test needs to see restored.
DRAFT_BASELINE = {
    "approval_status": "draft",
    "approved_by": "",
    "approved_at": None,
    "effective_at": None,
    "disclosure_scope": "internal",
    "content_state": "preliminary_concept",
}


def _normalise(path: Path, *, strip_images: bool = False) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(DRAFT_BASELINE)
    payload.pop(data_console.STATE_SNAPSHOT, None)
    if strip_images:
        for image in payload.get("images", []):
            if isinstance(image, dict):
                for field in data_console.APPROVAL_FIELDS:
                    image.pop(field, None)
                image.pop(data_console.STATE_SNAPSHOT, None)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


@pytest.fixture
def sandbox(tmp_path, monkeypatch) -> Path:
    """A throwaway copy of exactly the files the console reads and writes,
    with the approval decision reset to draft so the suite measures the
    console rather than today's state of the owner's data."""
    for relative in (REGISTRY, FACTS, RULES, CATALOG, QA, DOC):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)
    _normalise(tmp_path / FACTS)
    _normalise(tmp_path / RULES)
    _normalise(tmp_path / CATALOG, strip_images=True)
    _normalise(tmp_path / QA)
    monkeypatch.setattr(data_console, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(settings, "ws_token", TOKEN)
    monkeypatch.setattr(settings, "project_id", PROJECT)
    return tmp_path


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _load(sandbox: Path, relative: str) -> dict:
    return json.loads((sandbox / relative).read_text(encoding="utf-8"))


def _decision(sandbox: Path, relative: str):
    """The runtime's own answer, from the file as it now stands on disk."""
    return evaluate_claim(_load(sandbox, relative), PROJECT)


def _verdict(sandbox: Path, relative: str) -> bool:
    return _decision(sandbox, relative).allowed


def _audit_lines(sandbox: Path) -> list[dict]:
    path = sandbox / data_console.AUDIT_RELATIVE
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _approve(client: TestClient, source_id: str, **overrides) -> dict:
    body = {"token": TOKEN, "source_id": source_id, "by": "คุณทดสอบ ผู้รับผิดชอบ",
            "content_state": "preliminary_concept", "confirm": True}
    body.update(overrides)
    return client.post("/data-console/approve", json=body)


def _facts_body(sandbox: Path, label: str, value) -> dict:
    """The whole fact list as the page sends it, with one entry changed."""
    return {"token": TOKEN, "source_id": "project_facts", "by": "คุณทดสอบ ผู้รับผิดชอบ",
            "facts": [{"label": item["label"],
                       "value": value if item["label"] == label else item["value"]}
                      for item in _load(sandbox, FACTS)["facts"]]}


# ------------------------------------------------------------------- the gate

def test_without_a_token_nothing_is_readable(sandbox, client):
    response = client.get("/data-console/sources")
    assert response.status_code == 403
    assert response.json()["ok"] is False


def test_a_wrong_token_cannot_approve(sandbox, client):
    response = _approve(client, "project_facts", token="not-the-token")
    assert response.status_code == 403
    assert _verdict(sandbox, FACTS) is False
    assert _audit_lines(sandbox) == []


def test_a_write_cannot_authenticate_through_the_query_string(sandbox, client):
    """WS_TOKEN also unlocks /ws, which drives the robot and opens programs
    on this machine, and uvicorn logs every request line verbatim. A read may
    carry it in the URL the way /console already does; a write may not, so
    the body is the only channel the write routes accept."""
    before = (sandbox / FACTS).read_bytes()

    refused = client.post("/data-console/approve?token=" + TOKEN,
                          json={"source_id": "project_facts", "by": "คุณทดสอบ",
                                "content_state": "existing", "confirm": True})

    assert refused.status_code == 403
    assert (sandbox / FACTS).read_bytes() == before
    assert _audit_lines(sandbox) == []
    # And the page does not put it there either: only a GET gets a token in
    # its URL, which is what the comment beside api() claims.
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")
    assert "opts.method" in page and "token=' + encodeURIComponent(TOKEN)" in page


def test_an_unset_ws_token_closes_the_console_instead_of_opening_it(
        sandbox, client, monkeypatch):
    """The repo has shipped a safety flag defaulting to the unsafe side twice.

    Elsewhere a blank WS_TOKEN means "no gate configured, let it through".
    Here that would put an approve button on every browser on the LAN, so
    blank must read as closed — and say which knob to set.
    """
    monkeypatch.setattr(settings, "ws_token", "")

    read = client.get("/data-console/sources")
    write = client.post("/data-console/approve",
                        json={"source_id": "project_facts", "by": "ใครก็ได้",
                              "content_state": "existing", "confirm": True})

    assert read.status_code == 503 and write.status_code == 503
    assert "WS_TOKEN" in read.json()["error"]
    assert _verdict(sandbox, FACTS) is False


def test_a_cross_site_request_is_never_handed_the_token(sandbox, client):
    """`/data-console` bootstraps a local navigation with a redirect that
    carries WS_TOKEN in its Location header. The CORS middleware here is
    permissive, so without the Sec-Fetch-Site check any page open in the
    browser could fetch this route and read the LAN control token out of the
    redirect. This is the highest-consequence line in the feature."""
    response = client.get("/data-console", headers={"sec-fetch-site": "cross-site"},
                          follow_redirects=False)

    assert response.status_code == 200
    assert TOKEN not in response.headers.get("location", "")


def test_a_request_from_another_machine_is_never_handed_the_token(sandbox, client):
    """The other half of the same guard, and the half a Sec-Fetch-Site check
    alone does not cover: a plain navigation carries no Sec-Fetch-Site at
    all, so the client address is what separates the owner's own browser
    from a machine on the LAN. TestClient's client host is "testclient",
    which is not a loopback address — exactly the case this must refuse."""
    response = client.get("/data-console", follow_redirects=False)

    assert response.status_code == 200
    assert TOKEN not in response.headers.get("location", "")


def test_a_local_navigation_is_handed_the_token(sandbox, client, monkeypatch):
    """The other half: the owner double-clicking a shortcut on this machine
    gets a working link without typing the token."""
    monkeypatch.setattr(main_module, "_LOOPBACK_HOSTS",
                        main_module._LOOPBACK_HOSTS | {"testclient"})

    response = client.get("/data-console", follow_redirects=False)

    assert response.status_code == 302
    assert TOKEN in response.headers["location"]


# ------------------------------------------------------------------ the list

def test_the_list_shows_every_registry_source_with_its_live_reason(sandbox, client):
    data = client.get("/data-console/sources", params={"token": TOKEN}).json()

    registry = json.loads((sandbox / REGISTRY).read_text(encoding="utf-8"))
    assert len(data["sources"]) == len(registry["sources"])
    facts = next(row for row in data["sources"] if row["id"] == "project_facts")
    assert facts["editable"] is True
    assert facts["verdict"]["allowed"] is False
    # The exact policy reason, not a generic "blocked": this page exists to
    # answer "why is Emma silent".
    assert facts["verdict"]["reason"] == "not_approved"
    assert facts["verdict"]["reason_th"]


def test_the_catalog_count_is_the_file_and_not_a_number_typed_here(sandbox, client):
    """Counts are derived from the copied deck, so regenerating the deck
    changes them without breaking the suite — but a console that counted
    anything other than the records in the file still fails."""
    images = _load(sandbox, CATALOG)["images"]
    expected = Counter(str(item.get("type") or "ไม่ระบุ") for item in images)

    data = client.get("/data-console/sources", params={"token": TOKEN}).json()
    catalog = next(row for row in data["sources"] if row["id"] == "slide_catalog")

    assert catalog["counts"]["total"] == len(images) > 0
    assert catalog["counts"]["types"] == dict(expected)


def test_a_non_curated_source_has_no_approve_path(sandbox, client):
    listed = client.get("/data-console/sources", params={"token": TOKEN}).json()
    sample = next(row for row in listed["sources"] if row["id"] == "sample_unit_inventory")
    assert sample["editable"] is False
    assert sample["readonly_th"]

    refused = _approve(client, "sample_unit_inventory")
    assert refused.status_code == 400
    assert "ไม่ได้อยู่ในรายการ" in refused.json()["error"]
    assert client.get("/data-console/source/sample_unit_inventory",
                      params={"token": TOKEN}).status_code == 400


def test_a_source_from_another_project_is_refused(sandbox, client, monkeypatch):
    monkeypatch.setattr(settings, "project_id", "embassy_life")
    refused = _approve(client, "project_facts")
    assert refused.status_code == 400
    assert "embassy_world" in refused.json()["error"]
    assert _verdict(sandbox, FACTS) is False


# --------------------------------------------------------------- approving

def test_approving_flips_the_real_policy_verdict(sandbox, client):
    assert _verdict(sandbox, FACTS) is False

    response = _approve(client, "project_facts")

    assert response.status_code == 200
    assert response.json()["verdict"]["allowed"] is True
    # Read back through evaluate_claim on the file that is now on disk.
    assert _verdict(sandbox, FACTS) is True
    stored = _load(sandbox, FACTS)
    assert stored["approval_status"] == "approved"
    assert stored["approved_by"] == "คุณทดสอบ ผู้รับผิดชอบ"
    assert stored["disclosure_scope"] == "customer"
    assert stored["content_state"] == "preliminary_concept"
    assert stored["approved_at"] and stored["effective_at"]


def test_approval_never_turns_a_concept_into_a_finished_building(sandbox, client):
    body = _approve(client, "project_facts").json()
    assert "แนวคิดเบื้องต้น" in body["caveat_th"]


def test_the_server_stamps_its_own_timezone_aware_clock(sandbox, client):
    from datetime import datetime, timezone

    _approve(client, "project_facts")

    stored = _load(sandbox, FACTS)
    for field in ("approved_at", "effective_at"):
        moment = datetime.fromisoformat(stored[field])
        assert moment.tzinfo is not None, f"{field} has no timezone offset"
        assert moment <= datetime.now(timezone.utc), f"{field} is in the future"


def test_the_page_cannot_supply_its_own_timestamp_or_scope(sandbox, client):
    """Anything the page sends for those fields is ignored, not trusted."""
    _approve(client, "project_facts",
             approved_at="2099-01-01T00:00:00+07:00",
             effective_at="2099-01-01T00:00:00+07:00",
             approval_status="approved", disclosure_scope="customer")

    stored = _load(sandbox, FACTS)
    assert not stored["approved_at"].startswith("2099")
    assert _verdict(sandbox, FACTS) is True


#: A real, minimal content change for each editor that accepts one, so the
#: test below is a save that genuinely lands rather than a refusal that
#: happens to leave the approval alone. `_qa_body` lives in the Q&A section
#: at the foot of this file; it is resolved when the test runs.
def _content_change(sandbox: Path, editor: str):
    if editor == "facts":
        first = _load(sandbox, FACTS)["facts"][0]["label"]
        return FACTS, _facts_body(sandbox, first, "ข้อความที่เพิ่งพิมพ์เข้าไป")
    if editor == "rules":
        stored = _load(sandbox, RULES)
        key = next(name for name in stored if name.endswith("_rule"))
        return RULES, {"token": TOKEN, "source_id": "project_sales_context",
                       "by": "คุณทดสอบ ผู้รับผิดชอบ",
                       "rules": [{"key": key, "text": stored[key] + " ทดสอบ"}]}

    def change(body):
        body["entries"][0]["answer_now"] = "ข้อความที่เพิ่งพิมพ์เข้าไป"

    return QA, _qa_body(sandbox, change)


@pytest.mark.parametrize("editor", ["facts", "rules", "qa"])
def test_the_page_cannot_stamp_its_own_approval_through_an_edit(sandbox, client, editor):
    """The /edit half of the property the test above covers for /approve.

    `edit()` rebuilds the record from the file and re-keys only the fields
    its own editor understands, so approval keys arriving in the body are
    never read. That is a property of how the function happens to be
    written, not of a check it performs — dropping one line into it makes
    the whole suite pass while anyone holding WS_TOKEN can approve in a
    stranger's name through the save button, past the confirmation
    checkbox, past CONTENT_STATES, past clean_name's refusal and past the
    server's own clock, with the audit line still reading "edit". Every
    editor is covered because the next one added to CURATED is where it
    would come back.
    """
    relative, body = _content_change(sandbox, editor)
    before = _load(sandbox, relative)
    body.update({
        "approval_status": "approved",
        "approved_by": "คนที่ไม่เคยกดอนุมัติ",
        "approved_at": "2099-01-01T00:00:00+07:00",
        "effective_at": "2099-01-01T00:00:00+07:00",
        "disclosure_scope": "customer",
        # Not junk: a state the policy accepts, and the one that removes the
        # "แนวคิดเบื้องต้น" caveat Emma is otherwise forced to say.
        "content_state": "existing",
    })
    if editor == "qa":
        # The catalogue proves an approval reaches inside a list of records,
        # so an edit has to prove it cannot be smuggled in the same way.
        body["entries"][0].update({field: "approved" for field in data_console.APPROVAL_FIELDS})

    response = client.post("/data-console/edit", json=body)

    assert response.status_code == 200
    assert response.json()["changed"] > 0
    after = _load(sandbox, relative)
    assert after != before, "the content edit never landed, so nothing was proved"
    for field in data_console.APPROVAL_FIELDS:
        assert after[field] == before[field], field
    assert data_console.STATE_SNAPSHOT not in after
    for entry in after.get("entries", []):
        assert [field for field in data_console.APPROVAL_FIELDS if field in entry] == []
    # Read back through the real decision function, on the file as it now
    # stands: the six fields matching is the mechanism, this is the property.
    assert evaluate_claim(after, PROJECT).allowed is False
    assert response.json()["verdict"]["allowed"] is False
    assert _audit_lines(sandbox)[-1]["action"] == "edit"


@pytest.mark.parametrize("route,extra", [
    ("/data-console/approve", {"content_state": "existing", "confirm": True}),
    ("/data-console/revoke", {"confirm": True}),
    ("/data-console/edit", {"facts": [{"label": "ผู้พัฒนา", "value": "ข้อความใหม่"}]}),
])
def test_an_empty_approver_is_refused_by_every_write_route(sandbox, client, route, extra):
    """`approved_by` names the human who is responsible, and "who last touched
    this" is exactly what the revoke and edit lines record too — an unnamed
    revoke is an un-approval nobody can be asked about. The refusal is the
    server's, not the page's."""
    before = (sandbox / FACTS).read_bytes()

    for name in ("", "   ", None):
        body = {"token": TOKEN, "source_id": "project_facts", "by": name}
        body.update(extra)
        response = client.post(route, json=body)
        assert response.status_code == 400, route
        assert "ชื่อผู้รับผิดชอบ" in response.json()["error"]

    assert (sandbox / FACTS).read_bytes() == before
    assert _verdict(sandbox, FACTS) is False
    assert _audit_lines(sandbox) == []


def test_a_content_state_outside_the_policy_list_is_refused(sandbox, client):
    response = _approve(client, "project_facts", content_state="finished")
    assert response.status_code == 400
    assert _verdict(sandbox, FACTS) is False


def test_approval_needs_an_explicit_confirmation(sandbox, client):
    response = _approve(client, "project_facts", confirm=False)
    assert response.status_code == 400
    assert _verdict(sandbox, FACTS) is False


def test_the_content_states_arrive_in_thai_with_the_sentence_they_impose(sandbox, client):
    """The one field that decides whether Emma must say "เป็นแนวคิดเบื้องต้น"
    was a dropdown of raw English enum values on a Thai-only screen."""
    detail = client.get("/data-console/source/project_facts",
                        params={"token": TOKEN}).json()
    states = {row["value"]: row for row in detail["content_states"]}

    assert set(states) == set(data_console.CONTENT_STATES)
    assert all(row["label_th"] and row["label_th"] != row["value"] for row in states.values())
    assert "แนวคิดเบื้องต้น" in states["preliminary_concept"]["caveat_th"]
    assert states["existing"]["caveat_th"] == ""


def test_the_page_does_not_preselect_a_content_state(sandbox, client):
    """Pre-selecting the file's value made one approval as "existing" the
    suggested default for every approval after it, which is how a caveat
    disappears without anyone choosing to remove it."""
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")
    options = page.split('<select id="state">')[1].split("'</select>'")[0]
    assert "selected" not in options
    # Nothing in the dropdown may be derived from what the file already says.
    assert "approval" not in options and "content_state" not in options


# ------------------------------------------------------------- revoking

def test_revoking_puts_the_verdict_back_without_deleting_content(sandbox, client):
    _approve(client, "project_facts")
    assert _verdict(sandbox, FACTS) is True
    facts_before = _load(sandbox, FACTS)["facts"]

    response = client.post("/data-console/revoke",
                           json={"token": TOKEN, "source_id": "project_facts",
                                 "by": "คุณทดสอบ ผู้รับผิดชอบ", "confirm": True})

    assert response.status_code == 200
    assert _verdict(sandbox, FACTS) is False
    stored = _load(sandbox, FACTS)
    assert stored["approval_status"] == "draft"
    assert stored["disclosure_scope"] == "internal"
    assert stored["facts"] == facts_before


def test_revoking_puts_the_caveat_setting_back_where_it_found_it(sandbox, client):
    """Revoke used to clear the five permission fields and leave
    `content_state` at whatever the last approver picked. Approving once as
    "existing" therefore deleted the "แนวคิดเบื้องต้น" setting for good, and
    the undo that claims to remove the permission and never the content had
    quietly overwritten the one field that governs what Emma must say."""
    assert _load(sandbox, FACTS)["content_state"] == "preliminary_concept"
    _approve(client, "project_facts", content_state="existing")
    assert _load(sandbox, FACTS)["content_state"] == "existing"

    client.post("/data-console/revoke",
                json={"token": TOKEN, "source_id": "project_facts",
                      "by": "คุณทดสอบ", "confirm": True})

    stored = _load(sandbox, FACTS)
    assert stored["content_state"] == "preliminary_concept"
    assert data_console.STATE_SNAPSHOT not in stored


def test_revoking_needs_an_explicit_confirmation(sandbox, client):
    _approve(client, "project_facts")
    before = (sandbox / FACTS).read_bytes()

    response = client.post("/data-console/revoke",
                           json={"token": TOKEN, "source_id": "project_facts",
                                 "by": "คุณทดสอบ"})

    assert response.status_code == 400
    assert (sandbox / FACTS).read_bytes() == before
    assert _verdict(sandbox, FACTS) is True


# ------------------------------------------------------------- slide catalog

def test_the_catalog_is_shown_in_full_before_it_can_be_signed(sandbox, client):
    """A signature on a summary is a blind one, so the detail carries every
    caption rather than a sample of them."""
    total = len(_load(sandbox, CATALOG)["images"])
    detail = client.get("/data-console/source/slide_catalog",
                        params={"token": TOKEN}).json()
    assert detail["counts"]["total"] == total
    assert len(detail["images"]) == total
    assert detail["images"][0]["title_th"]
    assert detail["images"][0]["summary_th"]


def test_approving_the_catalog_reaches_every_image_not_just_the_file(sandbox, client):
    """`presentation_policy` asks the policy about each image record, so a
    top-level-only stamp would show green here and stay silent at runtime."""
    catalog = _load(sandbox, CATALOG)
    total = len(catalog["images"])
    assert all(not evaluate_claim(image, PROJECT).allowed for image in catalog["images"])

    _approve(client, "slide_catalog", content_state="preliminary_concept")

    catalog = _load(sandbox, CATALOG)
    assert evaluate_claim(catalog, PROJECT).allowed is True
    assert all(evaluate_claim(image, PROJECT).allowed for image in catalog["images"])
    assert len(catalog["images"]) == total


# ---------------------------------------------------------------- editing

def test_editing_facts_keeps_every_other_key_in_the_file(sandbox, client):
    before = _load(sandbox, FACTS)

    response = client.post("/data-console/edit",
                           json=_facts_body(sandbox, "ผู้พัฒนา", "Empire Group เจ้าของโครงการ"))

    assert response.status_code == 200
    after = _load(sandbox, FACTS)
    for key in ("_comment", "map", "disclaimer", "source_id", "project_id",
                "approval_status", "disclosure_scope", "content_state", "expires_at"):
        assert after[key] == before[key], key
    assert after["facts"][0]["value"] == "Empire Group เจ้าของโครงการ"
    assert (sandbox / (FACTS + ".bak")).is_file()


def test_editing_approved_content_withdraws_the_approval_in_the_same_write(
        sandbox, client):
    """The approve-then-swap hole. Editing an approved file used to leave the
    six approval fields alone, so new text went out to customers signed by
    the person who approved the *old* text, at the old timestamp — the
    `script_approved` shape again, a safety flag landing on the unsafe side
    because nothing decided it should. The property is the policy verdict
    across the edit, not which fields moved."""
    _approve(client, "project_facts")
    assert _decision(sandbox, FACTS).allowed is True

    response = client.post("/data-console/edit",
                           json=_facts_body(sandbox, "จุดเด่น", "ข้อความที่เพิ่งพิมพ์เข้าไป"))

    assert response.status_code == 200
    assert response.json()["demoted"] is True
    assert response.json()["verdict"]["allowed"] is False
    decision = _decision(sandbox, FACTS)
    assert decision.allowed is False
    assert decision.reason == "not_approved"
    stored = _load(sandbox, FACTS)
    assert stored["approved_by"] == ""
    assert stored["approved_at"] is None
    # The new text is in the file, it simply may not be spoken yet.
    assert any(item["value"] == "ข้อความที่เพิ่งพิมพ์เข้าไป" for item in stored["facts"])
    line = _audit_lines(sandbox)[-1]
    assert line["action"] == "edit_demoted"
    assert {change["field"] for change in line["changes"]} >= {"approval_status", "approved_by"}


def test_editing_a_draft_does_not_pretend_to_withdraw_anything(sandbox, client):
    response = client.post("/data-console/edit",
                           json=_facts_body(sandbox, "จุดเด่น", "ข้อความใหม่"))
    assert response.json()["demoted"] is False
    assert _audit_lines(sandbox)[-1]["action"] == "edit"


def test_saving_the_same_text_twice_writes_nothing_and_is_not_an_error(sandbox, client):
    """Pressing save twice is what a tired person does. Answering it in the
    red box the real failures use reads as "your edit was lost"."""
    body = _facts_body(sandbox, "จุดเด่น", "ข้อความใหม่")
    assert client.post("/data-console/edit", json=body).status_code == 200
    before = (sandbox / FACTS).read_bytes()
    lines = len(_audit_lines(sandbox))

    again = client.post("/data-console/edit", json=body)

    assert again.status_code == 200
    assert again.json()["changed"] == 0 and again.json()["unchanged"] is True
    assert (sandbox / FACTS).read_bytes() == before
    assert len(_audit_lines(sandbox)) == lines


def test_a_blank_box_never_becomes_an_approved_answer_of_nothing(sandbox, client):
    """The console must never manufacture a price, a size, a promotion or an
    opening hour, and "" is not the same as "ยังไม่มีข้อมูล": an empty string
    is a value Emma would read out. Whitespace in any box stores `null`."""
    before = _load(sandbox, FACTS)
    blank = [item["label"] for item in before["facts"] if item["value"] is None]

    client.post("/data-console/edit", json={
        "token": TOKEN, "source_id": "project_facts", "by": "คุณทดสอบ ผู้รับผิดชอบ",
        "facts": [{"label": item["label"], "value": "   "} for item in before["facts"]]
              + [{"label": "หัวข้อใหม่", "value": "เนื้อหาที่คนกรอกเอง"}],
    })

    after = {item["label"]: item["value"] for item in _load(sandbox, FACTS)["facts"]}
    assert all(value is None for label, value in after.items() if label != "หัวข้อใหม่")
    # Whatever was blank in the owner's file is still blank, whichever those
    # four happen to be on the day this runs.
    for label in blank:
        assert after[label] is None, label
    assert after["หัวข้อใหม่"] == "เนื้อหาที่คนกรอกเอง"


def test_a_dash_is_a_blank_box_not_an_answer(sandbox, client):
    """2026-09-29: the owner blanked price, room types and promotions by
    typing "-" and approved; the prompt then carried "ราคาเริ่มต้น: -" as a
    speakable fact. A box with no letter or digit in any script stores
    `null`, while a real value that merely contains punctuation survives."""
    before = _load(sandbox, FACTS)
    labels = [item["label"] for item in before["facts"]]

    client.post("/data-console/edit", json={
        "token": TOKEN, "source_id": "project_facts", "by": "คุณทดสอบ ผู้รับผิดชอบ",
        "facts": [{"label": labels[0], "value": "-"},
                  {"label": labels[1], "value": " — "},
                  {"label": labels[2], "value": "..."},
                  {"label": "หัวข้อใหม่", "value": "- เปิด 10 โมง"}],
    })

    after = {item["label"]: item["value"] for item in _load(sandbox, FACTS)["facts"]}
    assert after[labels[0]] is None and after[labels[1]] is None and after[labels[2]] is None
    assert after["หัวข้อใหม่"] == "- เปิด 10 โมง"


#: Two lines of the suite's own, so nothing below depends on which facts the
#: owner's file holds, or in what order, on the day it runs.
REAL, CONCEPT = "ทดสอบของจริง", "ทดสอบแนวคิด"


def _state_body(concept_state=None, value="ยังเป็นแนวคิด") -> dict:
    """The page's body. `concept_state=None` = a page with no state box."""
    rows = [{"label": REAL, "value": "มีอยู่จริงวันนี้"},
            {"label": CONCEPT, "value": value}]
    if concept_state is not None:
        rows[0]["content_state"] = ""
        rows[1]["content_state"] = concept_state
    return {"token": TOKEN, "source_id": "project_facts", "by": "คุณทดสอบ ผู้รับผิดชอบ",
            "facts": rows}


def _stored(sandbox: Path) -> dict:
    return {item["label"]: item for item in _load(sandbox, FACTS)["facts"]}


def test_a_line_can_carry_its_own_content_state_into_the_prompt(sandbox, client):
    """One file holds what is real today (the developer, the address) beside
    what is still a concept (the facility list). A file-wide state could only
    say "all concept" or "all finished" (2026-09-29)."""
    from app.prompts import load_facts

    client.post("/data-console/edit", json=_state_body("preliminary_concept"))
    shown = client.get("/data-console/source/project_facts", params={"token": TOKEN}).json()
    assert {row["label"]: row["content_state"] for row in shown["facts"]} == {
        REAL: "", CONCEPT: "preliminary_concept"}

    _approve(client, "project_facts", content_state="existing")
    rendered = load_facts(str(sandbox / FACTS)).splitlines()
    caveat = "แนวคิดเบื้องต้น"
    assert caveat in next(line for line in rendered if line.startswith(f"- {CONCEPT}:"))
    assert caveat not in next(line for line in rendered if line.startswith(f"- {REAL}:"))


def test_a_page_without_the_state_box_cannot_strip_a_line_caveat(sandbox, client):
    """The owner's open tab predates the box. A row that does not send the key
    keeps the file's state; reading it as "none" would let save quietly drop
    "แนวคิดเบื้องต้น" from a line nobody touched."""
    client.post("/data-console/edit", json=_state_body("preliminary_concept"))

    client.post("/data-console/edit", json=_state_body(None, value="แก้จากหน้าเก่า"))

    assert _stored(sandbox)[CONCEPT]["value"] == "แก้จากหน้าเก่า"
    assert _stored(sandbox)[CONCEPT]["content_state"] == "preliminary_concept"


def test_a_line_state_outside_the_policy_list_is_refused(sandbox, client):
    client.post("/data-console/edit", json=_state_body("preliminary_concept"))
    before = (sandbox / FACTS).read_bytes()
    response = client.post("/data-console/edit", json=_state_body("finished"))
    assert response.status_code == 400
    assert (sandbox / FACTS).read_bytes() == before


def test_changing_only_a_line_state_withdraws_the_approval(sandbox, client):
    """Dropping a line's caveat is a stronger claim than the one that was
    signed, so it is an edit like any other: the approval goes, and the audit
    line names the state that moved."""
    client.post("/data-console/edit", json=_state_body("preliminary_concept"))
    _approve(client, "project_facts", content_state="existing")
    assert _verdict(sandbox, FACTS) is True

    response = client.post("/data-console/edit", json=_state_body(""))

    assert response.json()["demoted"] is True
    assert _verdict(sandbox, FACTS) is False
    assert "content_state" not in _stored(sandbox)[CONCEPT]
    fields = {change["field"] for change in _audit_lines(sandbox)[-1]["changes"]}
    assert f"facts/{CONCEPT}/content_state" in fields


def test_saving_a_file_that_already_holds_a_dash_is_not_an_edit(sandbox, client):
    """A "-" already in an approved file renders as blank. Opening the page and
    pressing save untouched must not count "-" -> null as a change: that
    would withdraw an approval nobody changed, in that person's name."""
    data = _load(sandbox, FACTS)
    data["facts"] = [{"label": REAL, "value": "มีอยู่จริงวันนี้"},
                     {"label": "ราคาเริ่มต้น", "value": "-"}]
    (sandbox / FACTS).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    _approve(client, "project_facts", content_state="existing")
    shown = client.get("/data-console/source/project_facts", params={"token": TOKEN}).json()
    assert {row["label"]: row["blank"] for row in shown["facts"]} == {
        REAL: False, "ราคาเริ่มต้น": True}, "the page's ยังว่าง badge and the prompt disagree"

    response = client.post("/data-console/edit", json={
        "token": TOKEN, "source_id": "project_facts", "by": "คุณทดสอบ ผู้รับผิดชอบ",
        "facts": [{"label": row["label"], "value": row["value"] or ""}
                  for row in shown["facts"]]})

    assert response.json().get("unchanged") is True
    assert _verdict(sandbox, FACTS) is True


def test_the_page_sends_and_watches_each_line_state():
    """A box the save does not send is a box that silently does nothing, and
    one the unsaved-check ignores lets approve sign the file's old state."""
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")
    values = page.split("function editorValues")[1].split("function unsavedFields")[0]
    assert "f-state" in values and "content_state" in values
    unsaved = page.split("function unsavedFields")[1].split("function markClean")[0]
    assert "content_state" in unsaved
    clean = page.split("function markClean")[1].split("function showDirty")[0]
    assert "content_state" in clean
    # No box -> no key, so the server keeps the line's state; "" would clear it.
    assert "if (state) row.content_state" in values
    # The page is served fresh from disk while the API is whatever the server
    # loaded at start (2026-09-29: new page, 24-Sep server). Boxes appear
    # only when the API really sends per-line states.
    editor = page.split("function factsEditor")[1].split("function rulesEditor")[0]
    assert "'content_state' in f" in editor and "STATE.perLine ?" in editor


def test_editing_rules_rewrites_only_the_rule_it_was_given(sandbox, client):
    before = _load(sandbox, RULES)

    response = client.post("/data-console/edit", json={
        "token": TOKEN, "source_id": "project_sales_context", "by": "คุณทดสอบ",
        "rules": [{"key": "scope_rule", "text": before["scope_rule"] + " ทดสอบ"}],
    })

    assert response.status_code == 200
    after = _load(sandbox, RULES)
    assert after["story_rule"] == before["story_rule"]
    assert after["scope_rule"].endswith("ทดสอบ")
    assert after["review_status"] == before["review_status"]


def test_the_catalog_text_cannot_be_retyped_at_the_sales_desk(sandbox, client):
    response = client.post("/data-console/edit", json={
        "token": TOKEN, "source_id": "slide_catalog", "by": "คุณทดสอบ",
        "rules": [{"key": "scope_rule", "text": "x"}],
    })
    assert response.status_code == 400


# ------------------------------------------------------------- audit trail

def test_every_write_appends_exactly_one_audit_line_carrying_the_name(sandbox, client):
    assert _audit_lines(sandbox) == []

    _approve(client, "project_facts", by="สมชาย ผู้จัดการฝ่ายขาย")
    assert len(_audit_lines(sandbox)) == 1

    client.post("/data-console/revoke",
                json={"token": TOKEN, "source_id": "project_facts",
                      "by": "สมหญิง ผู้ดูแลข้อมูล", "confirm": True})
    lines = _audit_lines(sandbox)
    assert len(lines) == 2

    approve_line, revoke_line = lines
    assert approve_line["action"] == "approve"
    assert approve_line["by"] == "สมชาย ผู้จัดการฝ่ายขาย"
    assert revoke_line["action"] == "revoke"
    assert revoke_line["by"] == "สมหญิง ผู้ดูแลข้อมูล"
    changed = {change["field"] for change in approve_line["changes"]}
    assert {"approval_status", "approved_by", "disclosure_scope"} <= changed
    moved = next(c for c in approve_line["changes"] if c["field"] == "approval_status")
    assert (moved["from"], moved["to"]) == ("draft", "approved")
    assert all("token" not in json.dumps(line, ensure_ascii=False) for line in lines)


def test_an_edit_records_which_fact_moved_and_from_what(sandbox, client):
    client.post("/data-console/edit",
                json=_facts_body(sandbox, "จุดเด่น", "ข้อความใหม่"))

    line = _audit_lines(sandbox)[-1]
    assert line["action"] == "edit"
    change = next(c for c in line["changes"] if c["field"] == "facts/จุดเด่น")
    assert change["to"] == "ข้อความใหม่"
    assert change["from"] and change["from"] != change["to"]


def test_the_audit_endpoint_reads_back_what_was_written(sandbox, client):
    _approve(client, "project_facts", by="สมชาย ผู้จัดการฝ่ายขาย")
    body = client.get("/data-console/audit", params={"token": TOKEN}).json()
    assert body["entries"][-1]["by"] == "สมชาย ผู้จัดการฝ่ายขาย"


def test_a_busy_trail_does_not_erase_one_sources_history(sandbox, client):
    """Filtering the last N lines in the browser told a twice-approved source
    it had no history at all, as soon as N writes to other sources had
    happened since. On a page about accountability that is the worst possible
    lie, so the filter is the server's."""
    _approve(client, "project_facts", by="สมชาย ผู้จัดการฝ่ายขาย")
    rules_text = _load(sandbox, RULES)["scope_rule"]
    for index in range(5):
        client.post("/data-console/edit", json={
            "token": TOKEN, "source_id": "project_sales_context", "by": "คนอื่น",
            "rules": [{"key": "scope_rule", "text": rules_text + " ครั้งที่ %d" % index}],
        })

    body = client.get("/data-console/audit",
                      params={"token": TOKEN, "limit": 3,
                              "source_id": "project_facts"}).json()

    assert [entry["source_id"] for entry in body["entries"]] == ["project_facts"]
    assert body["entries"][0]["by"] == "สมชาย ผู้จัดการฝ่ายขาย"


# ------------------------------------------------------------------ the page

def test_the_page_is_served_and_never_carries_a_default_name(sandbox, client):
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")
    assert client.get("/data-console", params={"token": TOKEN}).status_code == 200
    # A remembered approver is nobody's signature.
    assert "localStorage" not in page
    assert 'id="who" autocomplete="off"' in page


def test_the_page_refuses_to_approve_text_its_boxes_no_longer_show(sandbox, client):
    """Typing into a fact and pressing อนุมัติ without pressing บันทึก used to
    approve the text already in the file, wipe the box in the re-render, and
    turn the page green.

    This reads the page source, which proves the guard is wired at all three
    places that can discard typing — it cannot prove the comparison is
    correct. The server-side half of the same property (an edit withdraws the
    approval) is tested for real above, so the worst case this misses is a
    refusal that does not fire, not an approval of text nobody saw.
    """
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")
    approve_handler = page.split("$('approveBtn').onclick")[1].split("const revoke")[0]

    assert "unsavedFields()" in approve_handler
    assert 'กด "บันทึกเนื้อหา" ก่อนอนุมัติ' in approve_handler
    assert "return;" in approve_handler
    # Switching source and closing the tab discard typing just as quietly.
    assert "unsavedFields()" in page.split("async function openSource")[1][:400]
    assert "beforeunload" in page


# ------------------------------------------------------------- the Q&A script
#
# The answers in this file are what a robot says to somebody who is deciding
# whether to buy a flat, so they are governed by the same machinery as the
# rest: a typed human name, one audit line per write, an edit withdrawing the
# approval it invalidates. What is new is the markdown copy in `docs/`, and
# the only property that matters about it is that it cannot drift — it is
# output, rewritten from the JSON by every successful write.

QA_SOURCE = "project_qa_script"


def _qa_stored(sandbox: Path) -> dict:
    return _load(sandbox, QA)


def _qa_body(sandbox: Path, mutate=None, **overrides) -> dict:
    """The whole script as the page sends it, after `mutate` has changed it."""
    stored = json.loads(json.dumps(_qa_stored(sandbox)))
    body = {"rules": stored["rules"], "entries": stored["entries"],
            "decisions": stored["decisions"]}
    if mutate is not None:
        mutate(body)
    body.update({"token": TOKEN, "source_id": QA_SOURCE,
                 "by": "คุณทดสอบ ผู้รับผิดชอบ"})
    body.update(overrides)
    return body


def _qa_save(client: TestClient, sandbox: Path, mutate=None, **overrides):
    return client.post("/data-console/edit",
                       json=_qa_body(sandbox, mutate, **overrides))


def _doc(sandbox: Path) -> str:
    return (sandbox / DOC).read_text(encoding="utf-8")


def test_the_qa_script_is_listed_as_editable_with_the_policys_own_verdict(
        sandbox, client):
    data = client.get("/data-console/sources", params={"token": TOKEN}).json()
    row = next(item for item in data["sources"] if item["id"] == QA_SOURCE)

    assert row["editable"] is True
    assert row["editor"] == "qa"
    assert row["what_th"]
    # The live reason, read back through evaluate_claim on the file itself —
    # not a string this console decided to print.
    assert row["verdict"]["allowed"] is False
    assert row["verdict"]["reason"] == _decision(sandbox, QA).reason == "not_approved"


def test_the_qa_detail_returns_every_situation_the_file_holds(sandbox, client):
    """A reviewer signs the whole script or none of it, so the detail carries
    all of it: every situation, both answers, and the notes that say what the
    answer must not do."""
    stored = _qa_stored(sandbox)

    detail = client.get("/data-console/source/" + QA_SOURCE,
                        params={"token": TOKEN}).json()

    assert [item["id"] for item in detail["entries"]] == \
        [item["id"] for item in stored["entries"]]
    assert len(detail["entries"]) == len(stored["entries"]) > 0
    assert detail["rules"] == stored["rules"]
    assert detail["decisions"] == stored["decisions"]
    for sent, source in zip(detail["entries"], stored["entries"]):
        assert sent["title"] == source["title"]
        assert sent["answer_now"] == source["answer_now"]
        assert sent["answer_after"] == source["answer_after"]
        assert [note["text"] for note in sent["notes"]] == \
            [note["text"] for note in source["notes"]]


def test_editing_one_answer_leaves_every_other_situation_and_key_alone(
        sandbox, client):
    before = _qa_stored(sandbox)
    target = before["entries"][2]["id"]

    def change(body):
        body["entries"][2]["answer_now"] = "ข้อความที่เพิ่งพิมพ์เข้าไป"

    response = _qa_save(client, sandbox, change)

    assert response.status_code == 200
    after = _qa_stored(sandbox)
    assert after["entries"][2]["answer_now"] == "ข้อความที่เพิ่งพิมพ์เข้าไป"
    assert after["entries"][2]["answer_after"] == before["entries"][2]["answer_after"]
    for index, entry in enumerate(after["entries"]):
        if index != 2:
            assert entry == before["entries"][index], entry["id"]
    for field in data_console.APPROVAL_FIELDS + ("source_id", "project_id", "title"):
        assert after[field] == before[field], field
    assert after["rules"] == before["rules"]
    line = _audit_lines(sandbox)[-1]
    assert line["changes"] == [{"field": "entries/%s/answer_now" % target,
                                "from": before["entries"][2]["answer_now"],
                                "to": "ข้อความที่เพิ่งพิมพ์เข้าไป"}]


def test_a_blank_answer_is_stored_blank_and_never_given_a_placeholder(
        sandbox, client):
    """Same rule as the four empty facts: an answer nobody has written is not
    an answer, and a helpful-looking default here is a sentence a robot could
    be made to say to a buyer over somebody's signature."""
    def blank(body):
        body["entries"][4]["answer_after"] = "   "

    _qa_save(client, sandbox, blank)

    stored = _qa_stored(sandbox)
    assert stored["entries"][4]["answer_after"] == ""
    assert stored["entries"][4]["answer_now"], "the other column was not touched"
    # And the document shows the gap rather than filling it in.
    assert "| หลังอนุมัติ |  |" in _doc(sandbox)


def test_adding_a_situation_gets_a_server_minted_id_and_names_who_added_it(
        sandbox, client):
    before = _qa_stored(sandbox)

    def add(body):
        body["entries"].append({
            "id": "", "title": "ลูกค้าถามเรื่องที่จอดรถ",
            "guest_says": ["มีที่จอดรถไหม", "  "],
            "answer_now": "", "answer_after": "", "notes": [],
        })

    response = _qa_save(client, sandbox, add, by="สมชาย ผู้จัดการฝ่ายขาย")

    assert response.status_code == 200
    stored = _qa_stored(sandbox)
    assert len(stored["entries"]) == len(before["entries"]) + 1
    new = stored["entries"][-1]
    assert new["id"] and new["id"] not in {item["id"] for item in before["entries"]}
    assert new["title"] == "ลูกค้าถามเรื่องที่จอดรถ"
    # The empty line the page sends for an unfilled box is dropped; the
    # unanswered columns stay unanswered.
    assert new["guest_says"] == ["มีที่จอดรถไหม"]
    assert new["answer_now"] == "" and new["answer_after"] == ""
    line = _audit_lines(sandbox)[-1]
    assert line["by"] == "สมชาย ผู้จัดการฝ่ายขาย"
    assert {"field": "entries/+" + new["id"], "from": None,
            "to": "ลูกค้าถามเรื่องที่จอดรถ"} in line["changes"]
    assert "ลูกค้าถามเรื่องที่จอดรถ" in _doc(sandbox)


def test_deleting_a_situation_records_which_one_went_and_who_removed_it(
        sandbox, client):
    before = _qa_stored(sandbox)
    victim = before["entries"][3]

    def drop(body):
        del body["entries"][3]

    response = _qa_save(client, sandbox, drop, by="สมหญิง ผู้ดูแลข้อมูล")

    assert response.status_code == 200
    stored = _qa_stored(sandbox)
    assert victim["id"] not in {item["id"] for item in stored["entries"]}
    assert len(stored["entries"]) == len(before["entries"]) - 1
    line = _audit_lines(sandbox)[-1]
    assert line["by"] == "สมหญิง ผู้ดูแลข้อมูล"
    # The title, not only the id: "entries/-q04" answers nothing when
    # somebody asks later which answer disappeared.
    assert {"field": "entries/-" + victim["id"], "from": victim["title"],
            "to": None} in line["changes"]
    assert victim["title"] not in _doc(sandbox)


def test_reordering_the_script_is_saved_and_shows_up_in_the_trail(sandbox, client):
    before = [item["id"] for item in _qa_stored(sandbox)["entries"]]

    def swap(body):
        body["entries"][0], body["entries"][1] = body["entries"][1], body["entries"][0]

    _qa_save(client, sandbox, swap)

    after = [item["id"] for item in _qa_stored(sandbox)["entries"]]
    assert after[:2] == [before[1], before[0]] and sorted(after) == sorted(before)
    assert any(change["field"] == "entries/order"
               for change in _audit_lines(sandbox)[-1]["changes"])
    document = _doc(sandbox)
    assert document.index("## 1. ") < document.index("## 2. ")


def test_a_situation_without_a_title_is_refused_and_nothing_is_written(
        sandbox, client):
    before = (sandbox / QA).read_bytes()

    def blank_title(body):
        body["entries"][0]["title"] = "   "

    response = _qa_save(client, sandbox, blank_title)

    assert response.status_code == 400
    assert (sandbox / QA).read_bytes() == before
    assert _audit_lines(sandbox) == []


@pytest.mark.parametrize("name", ["", "   ", None])
def test_an_unnamed_person_cannot_change_the_script(sandbox, client, name):
    before = (sandbox / QA).read_bytes()

    response = _qa_save(client, sandbox, by=name)

    assert response.status_code == 400
    assert "ชื่อผู้รับผิดชอบ" in response.json()["error"]
    assert (sandbox / QA).read_bytes() == before
    assert _audit_lines(sandbox) == []


def test_approving_the_script_flips_the_real_verdict_and_revoke_flips_it_back(
        sandbox, client):
    assert _verdict(sandbox, QA) is False
    kept = [item["id"] for item in _qa_stored(sandbox)["entries"]]

    approved = _approve(client, QA_SOURCE)

    assert approved.status_code == 200
    assert _verdict(sandbox, QA) is True
    assert _qa_stored(sandbox)["approved_by"] == "คุณทดสอบ ผู้รับผิดชอบ"
    # The document is output, so it may not keep announcing "ร่าง ยังไม่อนุมัติ"
    # once the file says otherwise.
    assert "อนุมัติแล้ว โดย คุณทดสอบ ผู้รับผิดชอบ" in _doc(sandbox)

    client.post("/data-console/revoke",
                json={"token": TOKEN, "source_id": QA_SOURCE,
                      "by": "คุณทดสอบ", "confirm": True})

    assert _verdict(sandbox, QA) is False
    assert "**ร่าง ยังไม่อนุมัติ**" in _doc(sandbox)
    # Revoking removes the permission, never the content.
    assert [item["id"] for item in _qa_stored(sandbox)["entries"]] == kept


def test_editing_an_approved_script_withdraws_the_approval_in_the_same_write(
        sandbox, client):
    """The approve-then-swap hole, on the file that holds the sentences a
    robot says to a buyer: new wording must never go out signed by the person
    who read the old wording."""
    _approve(client, QA_SOURCE)
    assert _decision(sandbox, QA).allowed is True

    def change(body):
        body["entries"][0]["answer_now"] = "คำตอบที่เพิ่งเปลี่ยนหลังอนุมัติ"

    response = _qa_save(client, sandbox, change)

    assert response.status_code == 200
    assert response.json()["demoted"] is True
    decision = _decision(sandbox, QA)
    assert decision.allowed is False and decision.reason == "not_approved"
    stored = _qa_stored(sandbox)
    assert stored["approved_by"] == "" and stored["approved_at"] is None
    # The new text is in the file; it simply may not be spoken yet.
    assert stored["entries"][0]["answer_now"] == "คำตอบที่เพิ่งเปลี่ยนหลังอนุมัติ"
    assert _audit_lines(sandbox)[-1]["action"] == "edit_demoted"
    assert "**ร่าง ยังไม่อนุมัติ**" in _doc(sandbox)


def test_every_save_rewrites_the_markdown_and_says_it_is_generated(
        sandbox, client):
    """The repo copy is output. Two editable copies of the same sentences
    drift, and the one that drifts is the one nobody opened — so the document
    is rebuilt from the JSON by every successful write and carries a header
    saying where it came from."""
    before = _doc(sandbox)

    def change(body):
        body["entries"][1]["answer_after"] = "ประโยคใหม่ที่เพิ่งตกลงกันวันนี้"
        body["rules"][0] = body["rules"][0] + " (แก้แล้ว)"

    _qa_save(client, sandbox, change)

    document = _doc(sandbox)
    assert document != before
    assert "qa_script.json" in document.splitlines()[0]
    assert "ห้ามแก้ไฟล์นี้ด้วยมือ" in document
    assert "ประโยคใหม่ที่เพิ่งตกลงกันวันนี้" in document
    assert "(แก้แล้ว)" in document
    # Every situation still reaches the document, in the file's own order.
    for entry in _qa_stored(sandbox)["entries"]:
        assert entry["title"] in document
    assert document.count("| ตอนนี้ |") == len(_qa_stored(sandbox)["entries"])


def test_what_the_detail_hands_the_page_saves_back_as_no_change(sandbox, client):
    """Round-trip through the two endpoints the page actually uses.

    This is the one that catches a field the detail forgets to send: the page
    posts back what it was handed, so anything missing from the detail is
    deleted from the file by the first save, and the operator sees a
    successful green save. Saving the file to itself must be a no-op.
    """
    detail = client.get("/data-console/source/" + QA_SOURCE,
                        params={"token": TOKEN}).json()
    stamp = (sandbox / DOC).stat().st_mtime_ns

    response = client.post("/data-console/edit", json={
        "token": TOKEN, "source_id": QA_SOURCE, "by": "คุณทดสอบ ผู้รับผิดชอบ",
        "rules": detail["rules"], "entries": detail["entries"],
        "decisions": detail["decisions"]})

    assert response.status_code == 200
    assert response.json()["changed"] == 0 and response.json()["unchanged"] is True
    assert _audit_lines(sandbox) == []
    # Nothing changed means nothing was written, the document included.
    assert (sandbox / DOC).stat().st_mtime_ns == stamp
    assert not (sandbox / (QA + ".bak")).exists()


def test_the_page_puts_the_two_answers_side_by_side_and_guards_the_boxes(
        sandbox, client):
    """The draft is a document a salesperson reads top to bottom: the
    situation, what the guest says, then the two answers next to each other.
    And the qa boxes have to be inside the same unsaved-changes guard as the
    others, or pressing อนุมัติ signs the text in the file rather than the
    text on the screen — the bug that guard was written for."""
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")
    card = page.split("function qaEntryCard")[1].split("function qaEditor")[0]

    assert "q-title" in card and "q-says" in card
    assert card.index("qa-grid") < card.index("q-now") < card.index("q-after")
    assert "qaEditorValues()" in page.split("function editorValues")[1].split("}\n")[0] \
        or "qaEditorValues()" in page.split("function editorValues")[1][:600]
    # One approve/revoke implementation, not a second copy for this editor.
    assert page.count("/data-console/approve") == 1
    assert page.count("/data-console/revoke") == 1


def test_every_delete_button_on_the_script_asks_before_it_removes_anything(
        sandbox, client):
    """Deleting a situation asked; deleting a rule, a decision or a note did
    not, and those three buttons sit directly under the box being read.

    One click removed a prompt rule with no confirm and no undo — the only
    way back was to leave the source and answer a confirm that throws away
    every other unsaved edit on the page.

    The invariant, rather than a string match on today's wording: inside
    `wireQa` every `.remove()` is reached through `confirmDelete`. A fourth
    delete button added without one makes the counts disagree.
    """
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")
    wire = page.split("function wireQa")[1].split("function renderDetail")[0]

    assert wire.count(".remove()") == 3, "a delete path was added or removed"
    assert wire.count("confirmDelete(") == wire.count(".remove()")
    # And the question names what is going, so it can be checked against
    # what the finger landed on.
    helper = page.split("function confirmDelete")[1].split("function wireQa")[0]
    assert "slice(0, 40)" in helper and "confirm(" in helper


def test_an_answer_box_is_as_tall_as_the_answer_in_it(sandbox, client):
    """A fixed three-line box cut long answers horizontally through the
    glyphs, which reads as a broken renderer rather than as "scroll me" — on
    the page whose job is to be read straight down before it is signed.

    Source-read, like the guard test above: it proves the sizing is wired at
    both places that put text in a box (first render, and every keystroke
    after), not that the arithmetic is right. The floor stays in the CSS.
    """
    page = (REPO / "client" / "data-console.html").read_text(encoding="utf-8")

    assert "scrollHeight" in page.split("function autoSize")[1][:400]
    assert "autoSizeAll($('detail'))" in page.split("function renderDetail")[1]
    listener = page.split("$('detail').addEventListener('input'")[1][:200]
    assert "autoSize(event.target)" in listener
    assert "min-height:78px" in page, "the floor is still the CSS's job"


def test_the_owners_real_files_are_untouched_by_this_whole_suite(sandbox):
    """Last test in the file, and the only one that looks outside the sandbox.

    Everything above runs against a temp copy with `DATA_ROOT` monkeypatched.
    If any write path resolves against the repository instead — the markdown
    export is the newest way to get that wrong, because `docs/` is not under
    `data/` and looks like a fixed location — the owner's real data gets
    stamped or rewritten by a test run. Mtimes catch that whichever file it
    is and whichever test did it.
    """
    for relative in _WATCHED:
        path = REPO / relative
        now = path.stat().st_mtime_ns if path.is_file() else None
        assert now == _REAL_STATE[relative], (
            f"{relative} changed while this file was running: a write path "
            "resolved against the repository instead of the sandbox "
            "(or another process on this machine is editing it)")
