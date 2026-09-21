"""Send only the manufacturer's #Read query; never execute or write a group."""
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
QUERY = b'#Read\r\n'


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-groups', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    cfg = dotenv_values(ROOT/'.env')
    adb = [cfg.get('ROBOT_ARM_ADB') or str(ROOT/'tools/android/platform-tools/adb.exe')]
    if cfg.get('ROBOT_ARM_ADB_SERIAL'):
        adb += ['-s', cfg['ROBOT_ARM_ADB_SERIAL']]
    url = 'http://127.0.0.1:8001/arm/state?' + urllib.parse.urlencode({'token':cfg.get('WS_TOKEN','')})
    with urllib.request.urlopen(url, timeout=10) as response:
        state = json.load(response)
    s = state.get('state', {})
    port = s.get('port','')
    if not state.get('ok') or s.get('armed') is not False or not s.get('port_present') or not re.fullmatch(r'/dev/ttyUSB[0-9]+', port):
        raise RuntimeError('Requires a present port and locked motion API')
    check = await asyncio.create_subprocess_exec(*adb, 'shell', 'pidof com.aobo.robot.ai3', stdout=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(check.communicate(),5)
    if out.strip() or check.returncode not in (0,1):
        raise RuntimeError('Vendor application running or check failed')
    script = f"exec 3< {port} || exit 2; printf 'READER_READY\\n'; timeout --foreground -s KILL 4 cat <&3"
    reader = await asyncio.create_subprocess_exec(*adb,'exec-out','sh -c '+shlex.quote(script),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    chunks = []
    async def collect():
        while data := await reader.stdout.read(4096):
            chunks.append(data)
    try:
        ready = await asyncio.wait_for(reader.stdout.readline(),4)
        if ready.strip() != b'READER_READY':
            raise RuntimeError('Reader unavailable; no query sent')
        receiving = asyncio.create_task(collect())
        at = datetime.now(timezone.utc).isoformat()
        # Exact case and CRLF found in the manufacturer's RIOS_USC binary.
        command = f"printf '%s\\r\\n' '#Read' > {port}"
        writer = await asyncio.create_subprocess_exec(*adb,'shell','sh -c '+shlex.quote(command),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        wo,we = await asyncio.wait_for(writer.communicate(),5)
        await asyncio.wait_for(reader.wait(),7)
        await asyncio.wait_for(receiving,2)
        raw = b''.join(chunks)
        report = {'at_utc':at,'query_hex':QUERY.hex(),'motion_sent':False,'writer_exit':writer.returncode,
                  'writer_stderr':we.decode('utf-8','replace'),'rx_hex':raw.hex(),'rx_ascii':raw.decode('ascii','backslashreplace'),
                  'rx_bytes':len(raw),'reader_exit':reader.returncode,'interpretation':'raw response, not group keyframes or joint feedback'}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        if reader.returncode is None:
            reader.kill()
            await reader.wait()


if __name__ == '__main__':
    asyncio.run(main())
