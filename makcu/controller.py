import random
import time
import win32api
import math
from .mouse import Mouse
from .connection import SerialTransport
from .errors import MakcuConnectionError
from .enums import MouseButton

class MakcuController:

    def __init__(self):
        self.transport = SerialTransport()
        self.mouse = Mouse(self.transport)

    def connect(self):
        self.transport.connect()

    def disconnect(self):
        self.transport.disconnect()

    def is_connected(self):
        return self.transport.is_connected()

    def _check_connection(self):
        if not self.transport.serial or not self.transport.serial.is_open:
            raise MakcuConnectionError("Not connected")

    def click(self, button: MouseButton):
        self._check_connection()
        self.mouse.press(button)
        self.mouse.release(button)

    def move(self, dx: int, dy: int):
        self._check_connection()
        self.mouse.move(dx, dy)

    def press(self, button: MouseButton):
        self._check_connection()
        self.mouse.press(button)

    def release(self, button: MouseButton):
        self._check_connection()
        self.mouse.release(button)

    def scroll(self, delta: int):
        self._check_connection()
        self.mouse.scroll(delta)

    def smooth_scroll(self, delta: int):
        self._check_connection()
        step = int(delta / abs(delta))
        for i in range(abs(delta)):
            self.scroll(step)
            time.sleep(0.01)

    def pan(self, dx, dy, button: MouseButton):
        self._check_connection()
        self.smooth_move(random.randint(800, 1200),random.randint(400,600))
        time.sleep(0.01)
        self.mouse.press(button)
        x,y = win32api.GetCursorPos()
        self.smooth_move(-dx + x, -dy + y)
        self.mouse.release(button)

    def smooth_move(self, destX, destY, gravityCoefficient=7, windCoefficient=5, maxVelocity=15, thresholdDistance=12):
        self._check_connection()
        startX, startY = win32api.GetCursorPos()
        currentX = float(startX)
        currentY = float(startY)
        integerX = int(round(startX))
        integerY = int(round(startY))
        residualX = residualY = 0.0
        velocityX = velocityY = windX = windY = 0.0

        def move_callback(x, y):
            prevX, prevY = win32api.GetCursorPos()
            deltaX = x - prevX
            deltaY = y - prevY
            self.move(deltaX, deltaY)
            time.sleep(0.01)

        while math.hypot(destX - currentX, destY - currentY) >= 1:
            distance = math.hypot(destX - currentX, destY - currentY)
            windMag = min(windCoefficient, distance)
            if distance >= thresholdDistance:
                windX = windX / 1.73205080757 + (2 * random.random() - 1) * windMag / 2.2360679775
                windY = windY / 1.73205080757 + (2 * random.random() - 1) * windMag / 2.2360679775
            else:
                windX /= 1.73205080757
                windY /= 1.73205080757
                if maxVelocity < 3:
                    maxVelocity = random.random() * 3 + 3
                else:
                    maxVelocity /= 2.2360679775
            velocityX += windX + gravityCoefficient * (destX - currentX) / distance
            velocityY += windY + gravityCoefficient * (destY - currentY) / distance
            vMag = math.hypot(velocityX, velocityY)
            if vMag > maxVelocity:
                clipVel = maxVelocity / 2 + random.random() * maxVelocity / 2
                velocityX = velocityX / vMag * clipVel
                velocityY = velocityY / vMag * clipVel
            currentX += velocityX
            currentY += velocityY
            deltaXTotal = currentX - integerX + residualX
            deltaYTotal = currentY - integerY + residualY
            moveDX = int(round(deltaXTotal))
            moveDY = int(round(deltaYTotal))
            residualX = deltaXTotal - moveDX
            residualY = deltaYTotal - moveDY
            if moveDX or moveDY:
                integerX += moveDX
                integerY += moveDY
                move_callback(integerX, integerY)

        if integerX != destX or integerY != destY:
            move_callback(destX, destY)

        return integerX, integerY

    def drag(self,x, y, button: MouseButton):
        self._check_connection()
        self.press(button)
        time.sleep(0.01)
        self.smooth_move(x,y)
        time.sleep(0.01)
        self.release(button)

    def click_human_like(self, button: MouseButton, count: int = 1,
                         profile: str = "normal", jitter: int = 0):
        self._check_connection()
        timing_profiles = {
            "normal": (60, 120, 100, 180),
            "fast": (30, 60, 50, 100),
            "slow": (100, 180, 150, 300),
        }
        if profile not in timing_profiles:
            raise ValueError(f"Invalid profile: {profile}. Choose from {list(timing_profiles.keys())}")
        min_down, max_down, min_wait, max_wait = timing_profiles[profile]
        for _ in range(count):
            if jitter > 0:
                dx = random.randint(-jitter, jitter)
                dy = random.randint(-jitter, jitter)
                self.mouse.move(dx, dy)
            self.mouse.press(button)
            time.sleep(random.uniform(min_down, max_down) / 1000.0)
            self.mouse.release(button)
            time.sleep(random.uniform(min_wait, max_wait) / 1000.0)
