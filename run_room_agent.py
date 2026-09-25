"""Start the room agent on THIS machine.

Separate from `run_server.py` on purpose: this is the only thing a room PC
runs. It holds no API key, no project data and no conversation — it takes a
named action from the server and performs it here.

    set ROOM_AGENT_NAME=ห้องขาย 1
    set ROOM_AGENT_TOKEN=<a long random string, different per machine>
    set ROOM_AGENT_HOST=0.0.0.0        (only when the server is another machine)
    python run_room_agent.py

Nothing is wired into Emma yet — this runs and is tested on its own first
(owner, 2026-09-24: "อย่าเพิ่งเอาไปใส่ที่ emma นะ ทำทดสอบไปก่อน").
"""

import os
import sys
from pathlib import Path

# Same lesson as run_server.py: paths in this repo are written relative to the
# repo, so a shortcut started from anywhere else must not change what they
# mean. The agent writes its log under data/room-agent/.
ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from app.room_agent import main

    main()
