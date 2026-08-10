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
