"""Trace only the configured arm USB device during the stop-only RX probe.

Explicit --send-stop is mandatory. Never invokes the optional motion flag.
Uses already mounted usbmon; does not mount debugfs or change drivers.
"""
import argparse
import asyncio
import json
import re
import shlex
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


async def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--send-stop', action='store_true', required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    cfg = dotenv_values(ROOT / '.env')
    adb = [cfg.get('ROBOT_ARM_ADB') or str(ROOT / 'tools/android/platform-tools/adb.exe')]
    if cfg.get('ROBOT_ARM_ADB_SERIAL'):
        adb += ['-s', cfg['ROBOT_ARM_ADB_SERIAL']]
    port = cfg.get('ROBOT_ARM_PORT', '')
    if not re.fullmatch('/dev/ttyUSB[0-9]+', port):
        raise RuntimeError('Unexpected serial path')
    tty = port.rsplit('/',1)[-1]
    device = f'/sys/class/tty/{tty}/device/../..'
    info = await asyncio.create_subprocess_exec(*adb, 'shell',
        f'cat {device}/idVendor {device}/idProduct {device}/busnum {device}/devnum',
        stdout=asyncio.subprocess.PIPE)
    raw, _ = await asyncio.wait_for(info.communicate(), 5)
    words = raw.decode().split()
    if len(words) != 4 or words[:2] != ['1a86','7523']:
        raise RuntimeError('Could not confirm CH340 USB identity')
    bus, dev = map(int, words[2:])
    # Filter on the robot, before transport/storage, excluding camera/mic traffic.
    script = (f'exec 3< /sys/kernel/debug/usb/usbmon/{bus}u || exit 2; '
        "printf 'MONITOR_READY\\n'; timeout -s KILL 9 cat <&3 | "
        'while IFS= read -r line; do case "$line" in '
        f'*:{bus}:{dev:03d}:*) printf "%s\\n" "$line";; esac; done')
    monitor = await asyncio.create_subprocess_exec(*adb, 'shell', 'su 0 sh -c ' + shlex.quote(script),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        ready = await asyncio.wait_for(monitor.stdout.readline(), 4)
        if ready.strip() != b'MONITOR_READY':
            raise RuntimeError('usbmon reader not ready')
        capture = asyncio.create_task(monitor.communicate())
        args.output_dir.mkdir(parents=True, exist_ok=True)
        probe = await asyncio.create_subprocess_exec(sys.executable,
            str(ROOT / 'scripts/probe_robot_arm_stop.py'), '--send-stop', '--output',
            str(args.output_dir / 'stop-rx.json'), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        result, error = await asyncio.wait_for(probe.communicate(), 20)
        data, err = await asyncio.wait_for(asyncio.shield(capture), 12)
        (args.output_dir / 'ch340-usbmon.txt').write_bytes(data)
        if probe.returncode:
            raise RuntimeError(error.decode('utf-8','replace'))
        print(result.decode('utf-8','replace'))
        print('CH340-only USB trace:')
        print(data.decode('ascii','replace'))
        print('Monitor stderr:', err.decode('utf-8','replace'))
    finally:
        if monitor.returncode is None:
            monitor.kill()
            await monitor.wait()


if __name__ == '__main__':
    asyncio.run(main())
