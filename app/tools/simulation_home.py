"""Tools available exclusively inside the robot simulator context."""
from pydantic import ValidationError
from app.robot_backend import active
from app.robot_home import HomeChange
from app.tools.registry import tool


@tool(name="set_simulated_device", description=(
    "ควบคุม smart home ในฉากจำลองเท่านั้น: lights ไฟทั้งหมด เปิด/ปิดหรือความสว่าง, "
    "ac แอร์ เปิด/ปิดหรืออุณหภูมิ, curtains ม่าน เปิด/ปิดหรือเปอร์เซ็นต์เปิด, tv ทีวี เปิด/ปิด "
    "เมื่อขอเปิดปิดไฟ แอร์ ม่าน ทีวี ให้เรียกเครื่องมือนี้ ไม่ใช่แค่ตอบรับ"),
    parameters={"type": "object", "properties": {
        "device": {"type": "string", "enum": ["lights", "ac", "curtains", "tv"]},
        "on": {"type": "boolean", "description": "true เปิด/false ปิด; ม่าน true คือเปิดเต็ม"},
        "value": {"type": "integer", "minimum": 0, "maximum": 100,
                  "description": "ไฟ:ความสว่าง 0..100, แอร์:16..30 องศา, ม่าน:เปอร์เซ็นต์เปิด 0..100 (ใช้แทน on); ทีวีไม่ใช้"}},
        "required": ["device"]}, tags=["simulation"])
def set_simulated_device(device: str, on: bool | None = None, value: int | None = None):
    backend = active.get()
    if backend is None:
        return {"ok": False, "error": "simulation_only"}
    try:
        command = HomeChange(device=device, on=on, value=value)
    except ValidationError:
        return {"ok": False, "hardware": "simulated", "error": "invalid_device_command"}
    return backend.set_home(command)


@tool(name="get_simulated_home", description="อ่านสถานะไฟ แอร์ ม่าน ทีวีจากฉาก smart home จำลอง ไม่ใช่เซนเซอร์จริง",
      parameters={"type": "object", "properties": {}}, tags=["simulation"])
def get_simulated_home():
    backend = active.get()
    return ({"ok": True, "hardware": "simulated", "smart_home": backend.snapshot()["smart_home"]}
            if backend is not None else {"ok": False, "error": "simulation_only"})
