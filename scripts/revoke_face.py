"""
Withdraw somebody's face from the greeter.

    python scripts/revoke_face.py "ชื่อ"                # stop naming them, now
    python scripts/revoke_face.py "ชื่อ" --delete-shots # ...and delete their camera crops
    python scripts/revoke_face.py --list                # everybody, with consent status

Takes effect on the next camera frame — the watcher reads `people.json` at
decision time — and permanently once the gallery is rebuilt
(build-face-gallery.cmd), which drops the vectors as well. `--delete-shots`
removes the JPGs in data/faces/enrolled/<name>/; without it they stay, in
case the person changes their mind (they can re-enrol either way, which
records fresh consent).

Staff portraits from staff.csv are not touched by this script: they are the
sales team's files. Revoking a staff member here still stops the robot
naming them; removing the portrait is a separate, human decision.
"""
from __future__ import annotations

import argparse
import shutil
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="withdraw a face from the greeter")
    ap.add_argument("name", nargs="?", help="the enrolled name, exactly as greeted")
    ap.add_argument("--delete-shots", action="store_true",
                    help="also delete data/faces/enrolled/<name>/")
    ap.add_argument("--list", action="store_true", help="show everybody and their status")
    args = ap.parse_args(argv)

    from app import consent, enrollment

    if args.list or not args.name:
        people = consent.load()
        names = sorted({n for n, _ in enrollment.enrolled()} | set(people))
        if not names:
            print("nobody enrolled from the camera, no consent records")
            return 0
        for name in names:
            entry = people.get(name, {})
            print(f"  {consent.status(name, people=people):8s} {name:20s} "
                  f"{entry.get('consent_at', '-')[:10]:10s} -> {entry.get('expires_at', '-')[:10]}")
        print("\nstatus: ok = may be named · missing = never consented (tolerated unless "
              "FACE_REQUIRE_CONSENT=true) · revoked/expired = never named")
        return 0

    had = consent.revoke(args.name)
    print(f"revoked: {args.name}" + ("" if had else " (no consent record existed — recorded the revocation anyway)"))
    if args.delete_shots:
        folder = enrollment.ENROLLED / args.name
        if folder.is_dir():
            shutil.rmtree(folder)
            print(f"deleted {folder}")
        else:
            print(f"no camera shots at {folder}")
    print("the robot stops naming them from the next frame; run build-face-gallery.cmd "
          "to drop their vectors from the gallery too")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass
    sys.exit(main())
