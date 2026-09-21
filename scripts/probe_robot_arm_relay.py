"""One explicit, camera-supervised arm-relay power pulse; no joint command.

Requires the operator nearby with the main power cutoff available. Power can
cause existing servo targets to take effect. The relay's prior physical state
is unknown; this test commands OFF at the end, not a verified restoration.
"""
import argparse
import json
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.decode_robot_cp210 import decode_stream

RELAY_ON = bytes.fromhex('a5 01 2a 02 00 00 00 08 00 26')
RELAY_OFF = bytes.fromhex('a5 01 2a 02 00 00 00 08 01 25')


def require_released_io(raw):
    decoded = decode_stream(raw)
    samples = [f for f in decoded['frames'] if 'io_raw' in f]
    if len(samples) < 20 or decoded['rejected_candidates'] or any(f['io_bits']['io7'] for f in samples):
        raise RuntimeError('No consistent released E-stop telemetry; no relay test')
    return len(samples)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--one-second-power-test', action='store_true', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    preflight = args.output.with_name('relay-preflight.json')
    run = subprocess.run([sys.executable, str(ROOT/'scripts/read_robot_cp210_status.py'),
                          '--bind-for-read', '--interface', '9-1.2:1.0', '--output', str(preflight)],
                         capture_output=True, timeout=30)
    if run.returncode:
        raise RuntimeError('CP210 preflight failed; no relay test')
    baseline = json.loads(preflight.read_text(encoding='utf-8'))
    count = require_released_io(bytes.fromhex(baseline['rx_hex']))
    cfg = dotenv_values(ROOT/'.env')
    request = urllib.request.Request('http://127.0.0.1:8001/arm/command',
        data=json.dumps({'action':'stop','token':cfg.get('WS_TOKEN','')}).encode(),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        stop = json.load(response)
    if not stop.get('ok') or not stop.get('stop_write_ok'):
        raise RuntimeError('Arm stop write failed; no relay test')
    adb = [cfg.get('ROBOT_ARM_ADB') or str(ROOT/'tools/android/platform-tools/adb.exe')]
    if cfg.get('ROBOT_ARM_ADB_SERIAL'):
        adb += ['-s',cfg['ROBOT_ARM_ADB_SERIAL']]
    on = ''.join('\\%03o' % b for b in RELAY_ON)
    off = ''.join('\\%03o' % b for b in RELAY_OFF)
    script = r'''
set -eu
iface=9-1.2:1.0
[ "$(cat /sys/bus/usb/devices/9-1.2/idVendor)" = 10c4 ]
[ "$(cat /sys/bus/usb/devices/9-1.2/idProduct)" = ea60 ]
if pidof com.aobo.robot.ai3 >/dev/null; then exit 2; fi
[ ! -e /sys/bus/usb/devices/$iface/driver ]
bound=0
opened=0
old=
port=
cleanup() {
  if [ "$opened" = 1 ]; then
    printf 'OFF_BYTES' >&3
    sleep 0.06
    printf 'OFF_BYTES' >&3
    exec 3>&-
    echo RELAY_OFF_WRITES_COMPLETED
  fi
  if [ -n "$old" ]; then stty -F "$port" "$old" || true; fi
  if [ "$bound" = 1 ]; then printf '%s' "$iface" > /sys/bus/usb/drivers/cp210x/unbind; fi
}
trap cleanup EXIT HUP INT TERM
printf '%s' "$iface" > /sys/bus/usb/drivers/cp210x/bind
bound=1
for node in /sys/bus/usb/devices/$iface/ttyUSB*; do
  [ -d "$node" ] || continue
  [ -z "$port" ] || exit 3
  port=/dev/${node##*/}
done
[ -n "$port" ]
old=$(stty -F "$port" -g)
stty -F "$port" 115200 cs8 -parenb -cstopb raw -echo -ixon -ixoff -crtscts clocal
exec 3<> "$port"
opened=1
printf 'ON_BYTES' >&3
echo RELAY_ON_WRITE_COMPLETED
sleep 1
'''.replace('ON_BYTES',on).replace('OFF_BYTES',off)
    result = subprocess.run(adb+['exec-out','su 0 sh -c '+shlex.quote(script)], capture_output=True, timeout=12)
    status = result.stdout.decode('utf-8','replace')
    report = {'test':'relay power on for one second then command off twice',
              'released_io_samples_before':count, 'joint_command_sent':False,
              'on_frame_hex':RELAY_ON.hex(), 'off_frame_hex':RELAY_OFF.hex(),
              'remote_exit':result.returncode, 'remote_output':status,
              'off_write_reported':'RELAY_OFF_WRITES_COMPLETED' in status,
              'relay_voltage_measured':False, 'physical_state_after':'unmeasured'}
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    if result.returncode or not report['off_write_reported']:
        raise RuntimeError('Relay cleanup unconfirmed; operator must use physical cutoff')


if __name__ == '__main__':
    main()
