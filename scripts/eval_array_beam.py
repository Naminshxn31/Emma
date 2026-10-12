"""How much does the robot's array beam buy? The app's own ArrayBeam.java,
compiled with a JDK and run on synthetic 4-mic rooms (tests/array_room.py).

    python scripts/eval_array_beam.py            # the table (~1-2 min)
    python scripts/eval_array_beam.py --quick    # one seed, fewer rooms

Each room: a target talker at 10 deg (in front, where faces are), other
talkers elsewhere, a diffuse noise field 10 dB under the target,
reverberation. Every component goes through exactly the filter the mixture
got, so the numbers are measured ratios:
  dSIR / dSNR  gain over microphone 0 against the other talkers / the noise
  tgt          the target's own level change (a beam must not eat the voice)
  LSD          log-spectral distance of the target, dB (> 1 = coloured)
  trips        MVDR's self-cancellation guard falling back to DS
Steering is read off a DOA calibration taught from simulated faces (no
geometry given), like the robot's. JDK: JAVA_HOME, the repo's
.tmp/android-toolchain, or PATH.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests import array_room as R  # noqa: E402

SRC = ROOT / "apps/emma-robot-android/app/src/main/java/com/emma/robot/control"


def jdk() -> tuple[str, str]:
    from tests.test_emma_robot_body_nav_app import _javac

    tools = _javac()
    if tools is None:
        raise SystemExit("no JDK (javac/java): set JAVA_HOME")
    return tools


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    javac, java = jdk()
    work = Path(tempfile.mkdtemp(prefix="array_beam_"))
    (work / "src").mkdir()
    (work / "src" / "BeamHarness.java").write_text(R.BEAM_HARNESS, encoding="utf-8")
    subprocess.run([javac, "-encoding", "UTF-8", "-d", str(work / "out"), str(SRC / "DoaMath.java"),
                    str(SRC / "ArrayBeam.java"), str(work / "src" / "BeamHarness.java")],
                   check=True, capture_output=True, encoding="utf-8")
    jvm = {"java": java, "cp": str(work / "out"), "work": work}
    seeds = (3,) if args.quick else (3, 4)
    rooms = [("2 talkers, RT60 0.5", dict(rt60=0.5)),
             ("1 talker at 70, anechoic", dict(rt60=0.0, others=((70.0, 0.0),))),
             ("3 talkers +3 dB, RT60 0.8", dict(rt60=0.8, others=((60.0, 3.0), (-90.0, 3.0), (160.0, 3.0))))]
    if args.quick:
        rooms = rooms[:1]
    print(f"{'geometry':7s} {'room':27s} {'case':10s} {'beam':6s} {'dSIR':>6s} {'dSNR':>6s} {'tgt':>6s} "
          f"{'LSD':>5s} {'trips':>5s}")
    for geometry, pos in R.GEOMETRIES.items():
        cal = R.taught_calibration(pos, np.random.default_rng(5))
        for label, kw in rooms:
            for case, error_deg, mismatch_db in (("steered", 0.0, 0.0), ("+8 deg", 8.0, 0.0),
                                                 ("+15 deg", 15.0, 0.0), ("mics 3dB", 0.0, 3.0)):
                if args.quick and case != "steered":
                    continue
                rows = {}
                for seed in seeds:
                    comps = R.room(pos, np.random.default_rng(seed), target_az=10.0, **kw)
                    if mismatch_db:
                        g = 10 ** (np.random.default_rng(9).normal(0, mismatch_db, 4) / 20)
                        comps = {k: v * g[:, None] for k, v in comps.items()}
                    tau = cal.arrivals(10.0 + error_deg)
                    for name, mode, t, uns in (("mic0", "ds", None, "1,0,0,0"), ("avg", "ds", None, "0.25,0.25,0.25,0.25"),
                                               ("DS", "ds", tau, "1,0,0,0"), ("MVDR", "mvdr", tau, "1,0,0,0")):
                        if case != "steered" and name in ("mic0", "avg"):
                            continue
                        out, info = R.beam(jvm, comps, mode, t, sigma=cal.sigma, unsteered=uns)
                        g = R.gains(comps, out)
                        rows.setdefault(name, []).append((g["sir"], g["snr"], g["target"], g["lsd"], info["guard"]))
                for name, vals in rows.items():
                    m = np.mean(np.array(vals), axis=0)
                    print(f"{geometry:7s} {label:27s} {case:10s} {name:6s} {m[0]:+6.2f} {m[1]:+6.2f} {m[2]:+6.2f} "
                          f"{m[3]:5.2f} {int(sum(v[4] for v in vals)):5d}")
    comps = R.room(R.circle(), np.random.default_rng(3), target_az=10.0, rt60=0.5)
    cal = R.taught_calibration(R.circle(), np.random.default_rng(5))
    _, info = R.beam(jvm, {"mix": comps["mix"]}, "mvdr", cal.arrivals(10.0), sigma=cal.sigma, keys=("mix",))
    seconds = comps["mix"].shape[1] / R.FS
    print(f"\nCPU: MVDR beam {info['lead_ns'] / 1e6:.0f} ms for {seconds:.0f} s of audio "
          f"= {info['lead_ns'] / 1e9 / seconds:.3f} x real time on one core of this machine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
