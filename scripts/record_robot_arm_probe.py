"""Record the PC observer camera around one explicitly selected arm probe.

No automatic channel search or retries. Requires an operator beside the robot
with a reachable main power switch. Camera 0 is the live Camera Hub output on
this workstation; inspect an observation first before requesting motion.
"""
import argparse
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import cv2
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.camera import open_camera


def wait_for_probe(process, camera_failed, request_stop, timeout=25,
                   clock=time.monotonic, sleep=time.sleep):
    deadline = clock() + timeout
    while process.poll() is None:
        if camera_failed.is_set() or clock() >= deadline:
            request_stop()
            raise RuntimeError('Observation failed or probe timed out; requested stop, physical halt remains unverified')
        sleep(0.1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe', choices=['observe', 'centre', 'step', 'relay', 'group3', 'map'], default='observe')
    p.add_argument('--channel', type=int, choices=range(1,21), default=1)
    p.add_argument('--camera', type=int, choices=range(8), default=0)
    p.add_argument('--map-target', type=int, choices=[1500,1540], default=1500)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    if args.probe in ('centre', 'step') and args.channel not in (1,11,7,8):
        p.error('Normal centre/step controls remain restricted to vendor channels')
    if args.map_target != 1500 and args.probe != 'map':
        p.error('--map-target requires --probe map')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    quit_capture = threading.Event()
    camera_failed = threading.Event()
    frames = []
    started = time.monotonic()

    def capture():
        next_frame = 0.0
        failures = 0
        cap = None
        try:
            # Keep camera creation and reads on the same capture thread.
            cap = open_camera(index=args.camera, say=print)
            if cap is None:
                camera_failed.set()
                return
            while not quit_capture.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures >= 5:
                        camera_failed.set()
                        return
                    continue
                failures = 0
                if float(frame.std()) < 2:
                    # Virtual Camera Hub may deliver black during startup.
                    # Wait within the baseline deadline, never count it as video.
                    if frames:
                        camera_failed.set()
                        return
                    continue
                elapsed = time.monotonic() - started
                if elapsed >= next_frame:
                    name = f'frame-{len(frames):04d}.jpg'
                    if not cv2.imwrite(str(args.output_dir / name), frame):
                        camera_failed.set()
                        return
                    frames.append({'file':name, 'seconds':round(elapsed,4)})
                    next_frame = elapsed + 0.1
        except Exception:
            camera_failed.set()
        finally:
            if cap is not None:
                cap.release()

    worker = threading.Thread(target=capture, daemon=True)
    worker.start()
    command_at = None
    process = None
    def request_stop():
        token = dotenv_values(ROOT / '.env').get('WS_TOKEN','')
        req = urllib.request.Request('http://127.0.0.1:8001/arm/command',
            data=json.dumps({'action':'stop','token':token}).encode(),
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.load(response)
        print('Observer stop requested; write_ok:', result.get('stop_write_ok'))
    try:
        deadline = time.monotonic() + 20
        while len(frames) < 12 and not camera_failed.is_set() and time.monotonic() < deadline:
            time.sleep(0.1)
        if len(frames) < 12 or camera_failed.is_set():
            raise RuntimeError('Camera did not deliver baseline frames; no motion launched')
        if args.probe != 'observe':
            command_at = time.monotonic() - started
            flag = '--centre-channel' if args.probe == 'centre' else '--step-channel'
            probe_command = [sys.executable, str(ROOT / 'scripts/probe_robot_arm_stop.py'),
                '--send-stop', flag, str(args.channel), '--output', str(args.output_dir / 'serial.json')]
            if args.probe == 'group3':
                probe_command = [sys.executable, str(ROOT / 'scripts/probe_robot_arm_stop.py'),
                    '--send-stop', '--group', '3', '--output', str(args.output_dir / 'serial.json')]
            if args.probe == 'map':
                probe_command = [sys.executable, str(ROOT / 'scripts/probe_robot_arm_stop.py'),
                    '--send-stop', '--map-channel', str(args.channel), '--map-target', str(args.map_target), '--output', str(args.output_dir / 'serial.json')]
            if args.probe == 'relay':
                probe_command = [sys.executable, str(ROOT/'scripts/probe_robot_arm_relay.py'),
                    '--one-second-power-test', '--output', str(args.output_dir/'relay.json')]
            process = subprocess.Popen(probe_command,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            wait_for_probe(process, camera_failed, request_stop)
            out, err = process.communicate(timeout=2)
            print(out.decode('utf-8','replace'))
            if process.returncode:
                raise RuntimeError(err.decode('utf-8','replace'))
        else:
            time.sleep(3)
        time.sleep(2)
    finally:
        if process is not None and process.poll() is None:
            try:
                request_stop()
            finally:
                process.kill()
                process.wait(timeout=5)
        quit_capture.set()
        worker.join(timeout=3)
        (args.output_dir / 'frames.json').write_text(json.dumps({
            'probe':args.probe, 'channel':None if args.probe == 'group3' else args.channel,
            'group':3 if args.probe == 'group3' else None,
            'camera':args.camera, 'command_process_started_seconds':command_at,
            'camera_failed':camera_failed.is_set(), 'frames':frames},indent=2),encoding='utf-8')
    print('Frames saved:',len(frames), 'Camera failed:',camera_failed.is_set())


if __name__ == '__main__':
    main()
