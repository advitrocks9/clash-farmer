from .enums import MouseButton
from .errors import MakcuCommandError

class Mouse:
    def __init__(self, transport):
        self.transport = transport

    def _send_button_command(self, button: MouseButton, state: int):
        command_map = {
            MouseButton.LEFT: "left",
            MouseButton.RIGHT: "right",
            MouseButton.MIDDLE: "middle",
            MouseButton.MOUSE4: "ms1",
            MouseButton.MOUSE5: "ms2",
        }
        if button not in command_map:
            raise MakcuCommandError(f"Unsupported button: {button}")
        self.transport.send_command(f"km.{command_map[button]}({state})")

    def press(self, button: MouseButton):
        self._send_button_command(button, 1)

    def release(self, button: MouseButton):
        self._send_button_command(button, 0)

    def move(self, x: int, y: int):
        self.transport.send_command(f"km.move({x},{y})")

    def scroll(self, delta: int):
        self.transport.send_command(f"km.wheel({delta})")