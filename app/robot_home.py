"""Validated virtual home commands. No device drivers or network I/O."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class HomeChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    device: Literal["lights", "ac", "curtains", "tv"]
    on: bool | None = None
    value: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def valid_command(self):
        if self.on is None and self.value is None:
            raise ValueError("specify on or value")
        if self.device == "tv" and self.value is not None:
            raise ValueError("TV accepts on/off only")
        if self.device == "ac" and self.value is not None and not 16 <= self.value <= 30:
            raise ValueError("AC temperature must be 16..30")
        if self.device == "curtains" and self.on is not None and self.value is not None:
            raise ValueError("curtains accept on (open/closed) OR value (0..100)")
        return self


def initial_home():
    return {"lights": {"on": True, "brightness": 85},
            "ac": {"on": True, "temperature": 25},
            "curtains": {"position": 80}, "tv": {"on": False}}


def apply_change(state, command: HomeChange):
    device = state[command.device]
    if command.device == "curtains":
        device["position"] = command.value if command.value is not None else (100 if command.on else 0)
        return
    if command.on is not None:
        device["on"] = command.on
        if command.device == "lights" and command.on and command.value is None and device["brightness"] == 0:
            device["brightness"] = 85
    if command.value is not None:
        device["brightness" if command.device == "lights" else "temperature"] = command.value
        if command.device == "lights" and command.on is None:
            device["on"] = command.value > 0
