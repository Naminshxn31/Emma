"""
Is the microphone alive at the operating-system level? Ask it directly.

    python scripts/check_mic.py            # every input device, 2s each
    python scripts/check_mic.py --seconds 4

Built on 2026-09-01, the morning a camera-rung call read `call audio:
peak=0.0000` for every frame while the browser swore the track was live and
unmuted (`call mic (start): 'Microphone (LCS_USB_AUDIO)' muted=False
state=live handed=True`). Two client-side fixes went in on the theory that
opening the device a second time was the fault — and neither changed a
single sample, because the fault was never in the browser: the USB
microphone was delivering nothing to *any* program. WASAPI, MME and WDM-KS
all sat there without one frame. Windows privacy said Allow, Device Manager
said OK, and the browser said live. The only thing that could say
"the device is dead" was a program that asked the device itself.

That is this script. It bypasses the browser, the page, the WebSocket and
the server, opens each capture device the way any desktop program would,
and reports whether samples arrive and what they measure. Three verdicts:

    NO FRAMES   the device delivers nothing — a hardware mute button, a USB
                device in a bad state (replug it), or an audio service that
                needs a restart/reboot. No software above this line can help.
    SILENT      frames arrive but every sample is exactly zero — same causes,
                one layer up (the driver is running, the capture path is not).
    alive       frames with real room noise in them. If Emma still hears
                nothing, the fault is above this line: which device the
                browser picked, the page, the VAD.

A real microphone never reads an exact zero; room noise alone measures
well above it. So the test needs nobody to speak — it only needs to run.
"""
from __future__ import annotations

import argparse
import sys
import time


def classify(samples, frames_expected: int) -> str:
    """Verdict for one device's capture. `samples` is float32 in [-1, 1]."""
    if samples is None or len(samples) == 0:
        return "NO FRAMES — the device delivers nothing to any program"
    import numpy as np

    peak = float(np.abs(samples).max())
    if peak == 0.0:
        return "SILENT — frames arrive but every sample is exactly zero"
    if len(samples) < frames_expected * 0.5:
        return f"stalling — only {len(samples)}/{frames_expected} samples arrived"
    return "alive"


def _probe(sd, index: int, seconds: float):
    import numpy as np

    info = sd.query_devices(index)
    rate = int(info["default_samplerate"]) or 48000
    got: list = []
    try:
        with sd.InputStream(device=index, samplerate=rate, channels=1, dtype="int16",
                            callback=lambda data, *_: got.append(data.copy())):
            time.sleep(seconds)
    except Exception as exc:                        # noqa: BLE001 - reported, not raised
        return None, f"ERROR — {str(exc).strip().splitlines()[0]}"
    if not got:
        return None, classify(None, int(rate * seconds))
    x = np.concatenate(got).astype("float32") / 32768.0
    return x, classify(x, int(rate * seconds))


def main(argv=None) -> int:
    # A Thai Windows console decodes cp874 by default; the verdict lines
    # carry em dashes and must survive a double-click launch.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                               # pragma: no cover - not a real stream
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--seconds", type=float, default=2.0, help="capture length per device")
    args = ap.parse_args(argv)

    try:
        import sounddevice as sd
    except ImportError:
        print("sounddevice is not installed — .venv\\Scripts\\python.exe -m pip install sounddevice")
        return 2
    import numpy as np

    apis = {i: a["name"] for i, a in enumerate(sd.query_hostapis())}
    devices = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    if not devices:
        print("no input devices at all — Windows sees no microphone")
        return 1
    print(f"{len(devices)} input device(s), {args.seconds:.0f}s each. Nobody needs to talk — "
          "room noise is enough; a live microphone never reads exactly zero.\n")
    worst = 0
    for index, d in devices:
        name = d["name"]
        if name.startswith(("Microsoft Sound Mapper", "Primary Sound Capture")):
            continue                               # aliases of the default device
        x, verdict = _probe(sd, index, args.seconds)
        stats = ""
        if x is not None and len(x):
            stats = (f"peak={np.abs(x).max():.4f} rms={float(np.sqrt((x * x).mean())):.4f} "
                     f"zero={(x == 0).mean() * 100:.0f}%  ")
        print(f"[{index:2d}] {name} ({apis.get(d['hostapi'], '?')}): {stats}{verdict}")
        sys.stdout.flush()
        if not verdict.startswith("alive"):
            worst = 1
    print()
    if worst:
        print("A device with NO FRAMES / SILENT is dead below the browser: check a mute "
              "button on the microphone itself, unplug and replug it, or reboot. "
              "Nothing in Emma's settings can fix that layer.")
    else:
        print("Every microphone is alive at the OS level. If Emma still hears nothing, "
              "the fault is above this line: the device the browser picked (address-bar "
              "mic icon), the page, or the VAD — read the `call audio:` lines in the server log.")
    return worst


if __name__ == "__main__":
    sys.exit(main())
