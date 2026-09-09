"""Deliver simulated navigation outcomes to the existing Emma voice session."""
from __future__ import annotations

import asyncio


async def watch_robot(live, robot) -> None:
    from app import events, session

    epoch, cursor = robot.epoch, robot.sequence
    while True:
        await asyncio.sleep(0.1)
        if epoch != robot.epoch:
            epoch, cursor = robot.epoch, robot.sequence
            continue
        fresh = [event for event in robot.events if event["seq"] > cursor]
        for event in fresh:
            cursor = event["seq"]
            kind = event["type"]
            if kind == "connection_lost":
                text = "การเชื่อมต่อกับหุ่นจำลองหลุด ไม่ทราบสถานะการเดิน ให้บอกผู้ใช้สั้นๆ ว่าเป็นผลจำลอง"
            elif kind == "robot_arrived" and event.get("reason") != "cancelled":
                place = "ฐานชาร์จ" if event["place"] == "base" else event["place"]
                text = (f"หุ่นจำลองถึง{place}แล้ว แจ้งผู้ใช้ว่าถึงแล้วในตัวจำลองเท่านั้น"
                        if event["ok"] else
                        f"หุ่นจำลองไป{place}ไม่สำเร็จ ผลจำลองคือ {event['reason']} "
                        "แจ้งตามจริง ห้ามบอกว่าถึงแล้วหรือยืนยันว่าหุ่นจริงหยุด")
            else:
                continue

            def relevant(event=event, event_epoch=epoch):
                if session._active is not live or robot.epoch != event_epoch:
                    return False
                if event["type"] == "connection_lost":
                    return not robot.state["connected"]
                # A queued arrival cannot talk over a newer stop/trip/reset.
                latest = next((command for command in reversed(robot.commands.values())
                               if command["status"] != "rejected"), None)
                return latest is not None and latest["command_id"] == event["command_id"]

            await events.announce(text, source="robot_simulator", still_relevant=relevant)
