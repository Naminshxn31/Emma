import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import eval_search as ev


def test_denominator_includes_refusals_and_no_hits():
    rows = ev.evaluate_questions([("missing", "expected"), ("!missing", None),
                                  ("price", None), ("!price", None)], lambda _: [], lambda q: q == "price")
    assert [r["status"] for r in rows] == ["fail", "pass", "fail", "pass"]
    assert ev.result_counts(rows) == {"total": 4, "pass": 2, "fail": 2, "skipped": 0, "accuracy": .5}


def test_search_error_not_correct_rejection():
    def broken(_):
        raise TimeoutError("private service url")
    rows = ev.evaluate_questions([("!question", None)], broken, lambda _: False)
    assert ev.result_counts(rows)["accuracy"] is None
    assert rows[0]["status"] == "skipped" and rows[0]["reason"] == "TimeoutError"


def test_wrong_top_fails_even_if_second_is_right():
    def hit(title):
        return SimpleNamespace(slide={"title_th": title}, found=True, similarity=.9, coverage=.6, standout=2)
    rows = ev.evaluate_questions([("gym", "BIOGENESIS")], lambda _: [hit("Pool"), hit("BIOGENESIS")], lambda _: False)
    assert rows[0]["status"] == "fail"


def test_manifest_complete_group_disjoint():
    questions = ev.load_questions(None)
    manifest = json.loads(Path("data/eval/splits-v1.json").read_text(encoding="utf-8"))
    train, test = (ev.select_split(questions, s, manifest) for s in ("train", "test"))
    assert len(train) == 104 and len(test) == 22
    assert not set(train) & set(test) and set(train) | set(test) == set(questions)
    groups = lambda split: {e["group"] for e in manifest["cases"].values() if e["split"] == split}
    assert not groups("train") & groups("test")
    with pytest.raises(ValueError, match="does not match"):
        ev.select_split(questions+[("unreviewed", None)], "train", manifest)


def test_manifest_rejects_group_leakage():
    manifest = {"cases": {ev.question_id("one"): {"group": "pool", "split": "train"},
                          ev.question_id("two"): {"group": "pool", "split": "test"}}}
    with pytest.raises(ValueError, match="crosses"):
        ev.select_split([("one", None), ("two", None)], "train", manifest)


def test_incomplete_train_cannot_suggest_thresholds(tmp_path, monkeypatch, capsys):
    from app.tools import slide_search, slides
    from app.config import settings
    import sys

    questions = tmp_path / "questions.txt"
    questions.write_text("one\n!two\n", encoding="utf-8")
    manifest = tmp_path / "split.json"
    manifest.write_text(json.dumps({"version": "synthetic", "cases": {
        ev.question_id(q): {"group": q, "split": "train"} for q in ("one", "!two")}}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["eval_search", "--file", str(questions), "--manifest", str(manifest)])
    monkeypatch.setattr(slides, "load_slides", lambda: [{"id": "synthetic"}])
    monkeypatch.setattr(slide_search, "get_index", lambda _: SimpleNamespace(semantic_enabled=False))
    monkeypatch.setattr(settings, "search_reranker_model", "")
    monkeypatch.setattr(ev, "_best_pair", lambda _: pytest.fail("must not calibrate incomplete data"))
    assert ev.main() == 1
    output = capsys.readouterr().out
    assert "2 skipped / 2 total" in output and "Calibration disabled" in output
    assert "Suggested settings" not in output
