from .controller import MakcuController
from .enums import MouseButton
from .errors import MakcuError, MakcuConnectionError

def create_controller():
    makcu = MakcuController()
    makcu.connect()
    return makcu

__all__ = [
    "MakcuController",
    "MouseButton",
    "MakcuError",
    "MakcuConnectionError",
    "create_controller",
]