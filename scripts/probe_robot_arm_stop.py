"""Bounded RX capture plus stop; optional explicit single-channel motion probe.

Requires --send-stop explicitly. Logs no credentials. A response on the serial
wire is evidence of bytes received, not measured joint position or a safe halt.
--centre-channel-1 additionally requires an operator beside the robot with
access to the main power switch. It commands 1500 once, then stops and locks.
--step-channel-1 instead sends one +40 step, only if the last command was 1500.
"""
import argparse
import asyncio
import json
import re
import shlex
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
PROBE_CHANNELS = (1, 11, 7, 8)


def parse_probe_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-stop', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    motion = parser.add_mutually_exclusive_group()
    motion.add_argument('--centre-channel-1', action='store_true')
    motion.add_argument('--step-channel-1', action='store_true')
    motion.add_argument('--centre-channel', type=int, choices=PROBE_CHANNELS)
    motion.add_argument('--step-channel', type=int, choices=PROBE_CHANNELS)
    motion.add_argument('--group', type=int, choices=[3], help='Owner-reported reference group, once; stop after an 8-second observation window')
    motion.add_argument('--map-channel', type=int, choices=range(1,21), help='Supervised commissioning: one channel to P1500/T9999, remote stop after two seconds; not a known neutral pose')
    parser.add_argument('--map-target', type=int, choices=[1500,1540], default=1500)
    args = parser.parse_args(argv)
    if args.map_target != 1500 and args.map_channel is None:
        parser.error('--map-target requires --map-channel')
    args.channel = args.map_channel or args.centre_channel or args.step_channel or 1
    args.is_step = args.step_channel_1 or args.step_channel is not None
    args.has_motion = args.centre_channel_1 or args.is_step or args.centre_channel is not None or args.group is not None or args.map_channel is not None
    return args


def validate_step(args, state):
    if args.is_step and state.get('commanded', {}).get(str(args.channel)) != 1500:
        raise RuntimeError('Step probe requires last-commanded 1500 on the selected channel; no automatic centre')


async def main():
    args = parse_probe_args()
    if args.has_motion:
        raise RuntimeError('Commissioning suspended: channel 5 kept changing after timed STOP; establish physical stopping and input semantics before further mapping')
    cfg = dotenv_values(ROOT / '.env')
    token = cfg.get('WS_TOKEN', '')
    adb = cfg.get('ROBOT_ARM_ADB') or str(ROOT / 'tools/android/platform-tools/adb.exe')
    serial = cfg.get('ROBOT_ARM_ADB_SERIAL', '')
    adb_args = [adb] + (['-s', serial] if serial else [])
    origin = 'http://127.0.0.1:8001'

    def api(endpoint, body=None):
        if body is None:
            req = urllib.request.Request(origin + endpoint + '?' + urllib.parse.urlencode({'token':token}))
        else:
            req = urllib.request.Request(origin + endpoint, data=json.dumps(dict(body, token=token)).encode(), headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)

    before = await asyncio.to_thread(api, '/arm/state')
    if not before.get('ok'):
        raise RuntimeError('State unavailable; no probe performed')
    state = before['state']
    if args.map_channel is not None and state.get('armed') is not False:
        raise RuntimeError('Commissioning requires locked normal controls')
    if args.group is not None and not state.get('groups_enabled'):
        raise RuntimeError('Stored groups disabled; no probe performed')
    validate_step(args, state)
    port = state.get('port', '')
    if not state.get('port_present') or not re.fullmatch(r'/dev/ttyUSB[0-9]+', port):
        raise RuntimeError('No verified serial device path; no probe performed')
    check = await asyncio.create_subprocess_exec(*adb_args, 'shell', 'pidof com.aobo.robot.ai3', stdout=asyncio.subprocess.PIPE)
    app_out, _ = await asyncio.wait_for(check.communicate(), timeout=5)
    if app_out.strip() or check.returncode not in (0, 1):
        raise RuntimeError('Vendor app running or process check failed; no probe performed')

    # Hold one reader open while the normal server performs its two stop writes.
    # Do not alter baud, DTR/RTS, launch the vendor app, or issue a home group.
    reader_seconds = 13 if args.group is not None else 5
    read_command = f"exec 3< {port} || exit 2; printf 'READER_READY\\n'; timeout --foreground -s KILL {reader_seconds} cat <&3"
    reader = await asyncio.create_subprocess_exec(*adb_args, 'exec-out', 'sh -c ' + shlex.quote(read_command),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    chunks = []
    timed_out = False
    motion_result = None
    async def collect():
        while data := await reader.stdout.read(4096):
            chunks.append(data)
    try:
        ready = await asyncio.wait_for(reader.stdout.readline(), timeout=4)
        if ready.strip() != b'READER_READY':
            raise RuntimeError('Serial reader did not become ready')
        receive = asyncio.create_task(collect())
        started = datetime.now(timezone.utc).isoformat()
        try:
            if args.map_channel is not None:
                # Separate commissioning route. Keep normal controls locked;
                # do not widen the production CHANNELS list or claim a mapping.
                # The device-side trap sends STOP even if the host disconnects.
                # Only two near-centre protocol targets for supervised mapping;
                # neither is represented as a measured joint position.
                travel_ms = 9999 if args.map_target == 1500 else 3000
                target = f'#{args.channel}P{args.map_target}T{travel_ms}'
                script = f"exec 4> {port} || exit 2; cleanup() {{ printf '%s\\r\\n' '#STOP' >&4; }}; trap cleanup EXIT HUP INT TERM; printf '%s\\r\\n' {shlex.quote(target)} >&4; sleep 2"
                writer = await asyncio.create_subprocess_exec(*adb_args, 'shell', 'sh -c '+shlex.quote(script), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    out, err = await asyncio.wait_for(writer.communicate(), timeout=5)
                finally:
                    if writer.returncode is None:
                        writer.kill()
                        await writer.wait()
                motion_result = {'ok':writer.returncode == 0,'did':'commission','at':args.map_target,'channel':args.channel,
                                 'note':'Remote timed stop; target is not a measured or known-neutral position',
                                 'stderr':err.decode('utf-8','replace')}
            elif args.has_motion:
                rearm = await asyncio.to_thread(api, '/arm/command', {'action':'arm'})
                if not rearm.get('ok'):
                    raise RuntimeError('Rearm failed')
                action = 'up' if args.is_step else 'centre'
                body = {'action':'group', 'group':args.group} if args.group is not None else {'action':action, 'channel':args.channel}
                motion_result = await asyncio.to_thread(api, '/arm/command', body)
                if motion_result.get('ok'):
                    await asyncio.sleep(8 if args.group is not None else 2)
        finally:
            result = await asyncio.to_thread(api, '/arm/command', {'action':'stop'})
        try:
            await asyncio.wait_for(reader.wait(), timeout=9)
        except asyncio.TimeoutError:
            timed_out = True
            reader.kill()
            await reader.wait()
        await asyncio.wait_for(receive, timeout=2)
        raw = b''.join(chunks)
        err = await reader.stderr.read()
    finally:
        if reader.returncode is None:
            reader.kill()
            await reader.wait()
    after = await asyncio.to_thread(api, '/arm/state')
    command = f"{'up' if args.is_step else 'centre'} channel {args.channel} then stop" if args.has_motion else 'stop via existing API (two writes)'
    if args.group is not None:
        command = f'group {args.group} once; stop after 8 seconds (completion not presumed)'
    if args.map_channel is not None:
        command = f'commission channel {args.channel}: P{args.map_target} T{travel_ms} then remote stop after two seconds'
    report = {'at_utc':started, 'command':command,
        'motion_result':motion_result,
        'stop_result':result, 'rx_hex':raw.hex(), 'rx_ascii':raw.decode('ascii','backslashreplace'),
        'rx_bytes':len(raw), 'reader_exit':reader.returncode,
        'local_reader_timed_out':timed_out,
        'reader_stderr':err.decode('utf-8','replace'),
        'contains_stop_ack':b'#STOP+OK' in raw,
        'armed_after':after.get('state',{}).get('armed'),
        'physical_motion_observed':None, 'visual_assessment':'not performed by serial probe',
        'joint_position_measured':False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2), encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__ == '__main__':
    asyncio.run(main())
