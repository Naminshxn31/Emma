"""Temporarily bind the verified CP2102 and collect incoming bytes, then unbind.

No serial payload is transmitted. Driver binding and opening the tty may change
control lines; this is a diagnostic connection, not electrically passive probing.
The vendor app must be stopped and the USB interface must be unbound beforehand.
"""
import argparse
import json
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bind-for-read', action='store_true', required=True)
    p.add_argument('--interface', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*:1\.0', args.interface):
        raise SystemExit('Invalid USB interface')
    cfg = dotenv_values(ROOT / '.env')
    adb = cfg.get('ROBOT_ARM_ADB') or str(ROOT / 'tools/android/platform-tools/adb.exe')
    cmd = [adb]
    if cfg.get('ROBOT_ARM_ADB_SERIAL'):
        cmd += ['-s', cfg['ROBOT_ARM_ADB_SERIAL']]
    # The remote trap restores the unbound state even if the host disconnects.
    script = '''
set -eu
iface=INTERFACE
dev=${iface%:*}
sys=/sys/bus/usb/devices/$dev
[ "$(cat "$sys/idVendor")" = 10c4 ]
[ "$(cat "$sys/idProduct")" = ea60 ]
if pidof com.aobo.robot.ai3 >/dev/null; then echo VENDOR_RUNNING >&2; exit 2; fi
[ ! -e /sys/bus/usb/devices/$iface/driver ]
bound=0
port=
old=
cleanup() {
  if [ -n "$old" ] && [ -n "$port" ]; then stty -F "$port" "$old" || true; fi
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
echo "PORT=$port" >&2
old=$(stty -F "$port" -g)
stty -F "$port" 115200 cs8 -parenb -cstopb raw -echo -ixon -ixoff -crtscts clocal
exec 3< "$port"
echo READER_READY >&2
timeout --foreground -s KILL 4 cat <&3 || true
exec 3<&-
'''.replace('INTERFACE', shlex.quote(args.interface))
    at = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(cmd + ['exec-out', 'su 0 sh -c ' + shlex.quote(script)], capture_output=True, timeout=20)
    check = subprocess.run(cmd + ['shell', 'readlink /sys/bus/usb/devices/' + args.interface + '/driver'], capture_output=True, timeout=6)
    report = {'at_utc':at, 'interface':args.interface, 'serial_payload_sent':False,
              'rx_bytes':len(result.stdout), 'rx_hex':result.stdout.hex(),
              'exit_code':result.returncode, 'stderr':result.stderr.decode('utf-8','replace'),
              'driver_after':check.stdout.decode('utf-8','replace').strip(),
              'driver_check_exit':check.returncode}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))
    if result.returncode or report['driver_after']:
        raise SystemExit('Diagnostic or cleanup failed; inspect report')


if __name__ == '__main__':
    main()
