"""
Deciding whether to say a name out loud — the layer between a camera frame
and the robot opening its mouth.

`app/faces.py` answers "who is nearest, and how near". That is a number.
This module turns numbers into one of three things, and refusing is the
common case:

    known      somebody enrolled, confidently, standing close enough
    stranger   a person is here, nobody we know
    nothing    no face worth acting on

Everything below exists because a greeting is irreversible. The robot
saying "สวัสดีคุณต้า" to somebody who is not ต้า cannot be taken back by a
later frame, and the person it happened to is standing right there. So the
defaults are all in the direction of staying quiet.

Why a decision needs more than one frame
----------------------------------------
A single frame is a bad witness: motion blur, a head turning, someone
walking behind the subject. `CONFIRM_FRAMES` makes the same answer arrive
several times in a row before it counts. This is the same shape as
`min_silence_duration` in the VAD — a detector that fires on one sample
fires on noise.

Why a decision has to expire
----------------------------
Somebody who stands at the desk for ten minutes is one arrival, not six
hundred. The cooldown is per person, and there is a separate one for
strangers, because otherwise a busy Saturday is a robot greeting the room
once a second.

Why distance is a threshold too
-------------------------------
`MIN_FACE_PX` is not about accuracy. A face 40 pixels wide is somebody
across the lobby who has not come to talk to us, and greeting them by name
from six metres away is worse than saying nothing — it is also the moment
the recognition is least reliable, so the two reasons point the same way.

What this module deliberately does not do
-----------------------------------------
It does not talk to the model, hold a socket, or know what a greeting says.
It returns a decision. `events.announce` is the one road to the model and
that stays true here (see its docstring — a camera was the next caller it
was written for).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app import faces
from app.config import settings

logger = logging.getLogger("condo_voice.facewatch")

#: A frame counts as "the greeted person is still here" from this score up,
#: even though it takes FACE_THRESHOLD to *say* the name. Two different
#: errors with two different prices: greeting the wrong person is a voice
#: that cannot be taken back, so naming demands the full threshold and
#: three consecutive frames — but mistakenly believing somebody stayed only
#: keeps the robot quiet, which is the cheap direction.
#:
#: 0.42 for the auraface pack: derived (threshold 0.50 minus the dip the
#: doorway walk showed on the old pack), NOT yet measured — the buffalo
#: numbers (dips 0.40-0.43, impostors <=0.342, floor 0.38) died with the
#: pack switch. Worst case of it being wrong is a quieter robot; walk the
#: doorway with check-face-range.cmd and set it from the printed scores.
SOFT_PRESENCE = 0.42


@dataclass
class Sighting:
    """Somebody the robot has decided is worth speaking to."""

    kind: str  #: "known" or "stranger"
    name: str | None
    score: float
    meta: dict = field(default_factory=dict)

    @property
    def group(self) -> str:
        return self.meta.get("group", "")


class Watcher:
    """Frames in, sightings out. One per camera.

    Holds the gallery and the short-term memory that keeps a greeting from
    repeating. Deliberately not a module-level singleton: `MULTI_SESSION`
    exists in this project because module state shared across conversations
    turned into two guests' budgets in one spreadsheet, and "who did we just
    greet" is exactly that kind of state.
    """

    def __init__(self, gallery: faces.Gallery | None = None,
                 threshold: float | None = None,
                 confirm_frames: int | None = None,
                 cooldown_s: float | None = None,
                 min_face_px: int | None = None,
                 margin: float | None = None,
                 name_when_alone: bool | None = None):
        self.gallery = gallery
        self.threshold = settings.face_threshold if threshold is None else threshold
        self.margin = settings.face_margin if margin is None else margin
        self.name_when_alone = (settings.face_name_when_alone
                                if name_when_alone is None else name_when_alone)
        #: Why the last confirmed face was greeted without its name, by
        #: name, so the log line is written once per person per reason and
        #: not thirty times a second.
        self._unnamed_logged: dict[str, str] = {}
        self.confirm_frames = (settings.face_confirm_frames
                               if confirm_frames is None else confirm_frames)
        self.cooldown_s = settings.face_cooldown_s if cooldown_s is None else cooldown_s
        self.min_face_px = settings.face_min_px if min_face_px is None else min_face_px

        self._streak_key: str | None = None
        self._streak = 0
        self._last_greeted: dict[str, float] = {}
        #: When somebody the robot might actually greet was last in frame —
        #: set before the confirmation completes, so the greeter can start
        #: dialling while the decision is still being made (confirming
        #: takes ~1.2s and the Gemini dial 2-3s; in sequence the robot
        #: greeted people's backs). "Might actually greet" is the important
        #: half: the first version fired on *any* face big enough, and the
        #: owner sitting at the desk — greeted an hour ago, deep in cooldown
        #: — opened a fresh Gemini session every fifteen seconds for nobody.
        #: The exact quota leak the wake word exists to prevent. So this is
        #: a candidate that is not suppressed, seen at least twice.
        self.someone_at: float | None = None
        #: The faces of strangers already greeted, so "a stranger" is not
        #: one person. With a single shared cooldown, greeting anybody
        #: unknown silenced every *other* unknown visitor for its whole
        #: length - measured 2026-08-31: a garbage frame scoring 0.062 was
        #: greeted as a stranger at 09:18, another at 09:31, and the real
        #: visitor who walked in minutes later got nothing at all. The
        #: vector is already in hand at that point; remembering it is what
        #: lets a *different* face through while the same face stays quiet.
        self._strangers: list[dict] = []

    # -- loading ---------------------------------------------------------

    @classmethod
    def from_settings(cls) -> "Watcher | None":
        """A watcher with the enrolled gallery, or None if there isn't one.

        None is a working state, not an error: a machine with no models and
        no gallery runs the rest of the showroom exactly as before. That is
        the same promise `FACE_ENABLED=false` makes, kept one layer down so
        a half-configured machine cannot half-work.
        """
        if not faces.available():
            logger.info("face models not installed — recognition off")
            return None
        path = Path(settings.face_gallery).expanduser()
        if not path.is_file():
            logger.info("no gallery at %s — recognition off", path)
            return None
        try:
            gallery = faces.Gallery.load(path)
        except ValueError as exc:
            # A gallery built by another model. Loud and off, never ranked.
            logger.warning("%s", exc)
            return None
        logger.info("face gallery: %d enrolled, threshold %.2f",
                    len(gallery), settings.face_threshold)
        return cls(gallery)

    # -- the decision ----------------------------------------------------

    def see(self, frame: np.ndarray, now: float | None = None) -> Sighting | None:
        """One camera frame in; a decision or None.

        Only the nearest face is considered. A queue at the desk is not a
        list of people to greet in turn — it is one person being spoken to
        and others waiting, and the robot addressing the second one is the
        barge-in bug wearing a face.
        """
        now = time.monotonic() if now is None else now
        # Locate, not detect: the embedding is the expensive half and both
        # tests below can rule a frame out without it. A doorway is empty
        # most of the time, and describing nobody costs the same as
        # describing somebody.
        found = faces.locate(frame)
        if not found:
            self._streak_key, self._streak = None, 0
            return None

        face = found[0]
        x1, _, x2, _ = face.bbox
        if (x2 - x1) < self.min_face_px:
            # Somebody across the room. Not a visitor yet, and the frame
            # where they are smallest is the frame we trust least.
            self._streak_key, self._streak = None, 0
            return None

        if face.vec is None and faces.describe(frame, face) is None:
            return None

        name, score, meta = None, -1.0, {}
        best = None
        unnamed = None
        if self.gallery is not None and len(self.gallery):
            i, score, runner_up = self.gallery.match2(face.vec)
            best = self.gallery.names[i]
            if score >= self.threshold:
                name, meta = best, self.gallery.meta[i]
                unnamed = self._why_not_name(name, score, runner_up, found)
                if unnamed:
                    # Recognised, but not to be named: greeted like a
                    # stranger, with the reason carried on the sighting for
                    # the turn log. The rest of this function treats the
                    # face as unknown — including remembering it, so the
                    # anonymous greeting is not repeated either.
                    if self._unnamed_logged.get(name) != unnamed:
                        logger.info("face: %s recognised (%.3f) but not named — %s",
                                    name, score, unnamed)
                        self._unnamed_logged[name] = unnamed
                    name, meta = None, {"unnamed": unnamed, "would_be": best}

        # Strangers greeted longer ago than the cooldown are new arrivals
        # the next time they appear, so their memory expires with it.
        self._strangers = [e for e in self._strangers
                           if (now - e["at"]) < self.cooldown_s]

        # An unknown face is compared against the strangers already greeted
        # - the same bar that names a colleague decides "this is the person
        # I greeted five minutes ago". A match is identification, so it is
        # presence (one frame suffices) and it refreshes that stranger's
        # own clock, nobody else's.
        stranger_seen = None
        if name is None:
            for entry in self._strangers:
                if float(np.dot(face.vec, entry["vec"])) >= self.threshold:
                    stranger_seen = entry
                    entry["at"] = now
                    break

        key = (f"name:{name}" if name
               else f"stranger:{id(stranger_seen)}" if stranger_seen is not None
               else "stranger:new")

        # Could this frame end in a greeting? Decided before any clock is
        # refreshed below, because refreshing is what makes "already greeted"
        # look like "just now".
        if name:
            last = self._last_greeted.get(key)
            greetable = last is None or (now - last) >= self.cooldown_s
        else:
            greetable = stranger_seen is None

        # "Still here" is decided by a *single* frame, while greeting takes
        # several - the two mistakes cost differently. Greeting the wrong
        # person is a voice that cannot be taken back; wrongly believing
        # somebody stayed only keeps the robot quiet. Without this, a person
        # whose score flickers around the threshold at distance broke the
        # confirmation streak often enough that their clock was never
        # refreshed, and the robot re-greeted somebody who had been standing
        # there the whole time. A weak frame of the right face (>=
        # SOFT_PRESENCE, below the naming bar) counts as presence too.
        #
        # Only a clock still inside its cooldown is pushed along: presence
        # extends a suppression, it must never resurrect an expired one -
        # or the first frame of somebody coming back from lunch would reset
        # the very clock that says they were away. And a frame nobody can
        # identify - under SOFT_PRESENCE, matching no remembered stranger -
        # refreshes nothing: it is not evidence that anybody in particular
        # is still here, and treating it as such is how garbage frames kept
        # real visitors silenced.
        if name is None and best is not None and score >= SOFT_PRESENCE:
            stamp = self._last_greeted.get(f"name:{best}")
            if stamp is not None and (now - stamp) < self.cooldown_s:
                self._last_greeted[f"name:{best}"] = now
        elif name is not None:
            stamp = self._last_greeted.get(key)
            if stamp is not None and (now - stamp) < self.cooldown_s:
                self._last_greeted[key] = now

        if key == self._streak_key:
            self._streak += 1
        else:
            self._streak_key, self._streak = key, 1
        if greetable and self._streak >= 2:
            self.someone_at = now
        # A stranger has to hold the frame twice as long as a colleague.
        # The gallery can vouch for a colleague; for a stranger the only
        # witness is persistence - and the 0.062-scoring garbage frame that
        # was greeted this morning would not have survived six frames.
        needed = self.confirm_frames if name else self.confirm_frames * 2
        if self._streak < needed:
            return None

        if name:
            # The clock restarts on every confirmed sighting, not only on
            # the greeting. Counting from the greeting re-greeted somebody
            # who had simply stayed - measured on the first working run,
            # the same person twice in one conversation. A new arrival is
            # someone who was *away* for cooldown_s.
            last = self._last_greeted.get(key)
            self._last_greeted[key] = now
            if last is not None and (now - last) < self.cooldown_s:
                return None
            # A colleague who was just greeted as a stranger has been
            # greeted. Walking in, the first confirmable thing about a
            # person is that they are a person — approach frames score
            # under the naming bar (measured: 0.33-0.38 on the way in,
            # 0.57 only at the desk) — so the anonymous greeting fires
            # first, and following it seconds later with a named one is
            # the robot interrupting its own conversation to add a name.
            # The remembered stranger face answers "was that already you".
            for entry in list(self._strangers):
                if float(np.dot(face.vec, entry["vec"])) >= self.threshold:
                    self._strangers.remove(entry)
                    return None
            return Sighting(kind="known", name=name, score=float(score),
                            meta=dict(meta))

        if stranger_seen is not None:
            return None                    # greeted already; clock refreshed

        self._strangers.append({"vec": np.array(face.vec, copy=True), "at": now})
        # A remembered face costs 2KB; forgetting the oldest is cheaper
        # than comparing against a whole Saturday.
        del self._strangers[:-16]
        return Sighting(kind="stranger", name=None, score=float(score),
                        meta=dict(meta))

    def _why_not_name(self, name: str, score: float, runner_up: float,
                      found: list) -> str | None:
        """None when the name may be spoken; otherwise the reason it may not.

        Three reasons, from the outside review of 2026-09-01, each of which
        turns a name into a plain greeting rather than into silence — a
        greeting without a name is never wrong, a wrong name is:

        - **margin**: the runner-up (a different person) is within
          `margin` of the best. Close second = the frame where the nearest
          is most likely the wrong one.
        - **company**: more than one usable face in the frame. The model
          is told one name and addresses everybody by it, and there is no
          speaker identification to say who answered.
        - **consent**: no record that this person agreed (only when
          FACE_REQUIRE_CONSENT is on), or a record that says they withdrew
          or that it expired (always). See app/consent.py.
        """
        if runner_up >= 0 and (score - runner_up) < self.margin:
            return "margin (runner-up %.3f within %.2f)" % (runner_up, self.margin)
        if self.name_when_alone:
            # Company is somebody standing *with* them: a second face of
            # comparable size. min_face_px alone (50px on the owner's
            # doorway setup) let a poster, a screen, or a person across
            # the room unname the one at the desk — measured 2026-09-01,
            # the owner alone in frame, "company (2 faces in frame)".
            primary = max(found, key=lambda f: f.bbox[2] - f.bbox[0])
            bar = max(self.min_face_px, 0.6 * (primary.bbox[2] - primary.bbox[0]))
            usable = sum(1 for f in found if (f.bbox[2] - f.bbox[0]) >= bar)
            if usable > 1:
                return "company (%d faces in frame)" % usable
        from app import consent

        allowed, state = consent.may_name(name)
        if not allowed:
            return "consent %s" % state
        return None

    def forget(self) -> None:
        """Drop the cooldowns and the streak.

        For tests, and for the end of a day: "we already greeted them"
        should not survive into tomorrow morning.
        """
        self._streak_key, self._streak = None, 0
        self._last_greeted.clear()
        self._strangers.clear()
