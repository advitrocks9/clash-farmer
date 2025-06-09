import serial
import threading
import time
from serial.tools import list_ports
from .errors import MakcuConnectionError, MakcuTimeoutError

class SerialTransport:
    baud_change_command = bytearray([0xDE, 0xAD, 0x05, 0x00, 0xA5, 0x00, 0x09, 0x3D, 0x00])

    def __init__(self):
        self._log_messages = []
        self._lock = threading.Lock()
        self._is_connected = False
        self._response_buffer = ""
        self._response_ready = threading.Event()
        self._waiting_for_response = False
        self._response_timeout = 0.01
        self._command_lock = threading.Lock()

        self.port = self.find_com_port()
        if not self.port:
            raise MakcuConnectionError("Makcu device not found. Please specify a port explicitly.")

        self.baudrate = 115200
        self.serial = None
        self._current_baud = None

    def receive_response(self, sent_command: str = "") -> str:
        try:
            if not self._response_ready.wait(timeout=self._response_timeout):
                return ""
            response = self._response_buffer
            self._response_buffer = ""
            self._response_ready.clear()

            lines = [ln.strip() for ln in response.strip().splitlines() if ln.strip()]
            cmd = sent_command.strip()
            cleaned = []
            for ln in lines:
                if ln == cmd or not ln:
                    continue
                if ln.startswith('>>> '):
                    actual = ln[4:].strip()
                    if actual and actual != cmd:
                        cleaned.append(actual)
                else:
                    cleaned.append(ln)
            result = "\n".join(cleaned)
            return result
        except Exception:
            return ""

    def _log(self, message):
        self._log_messages.append(message)
        if len(self._log_messages) > 20:
            self._log_messages.pop(0)
        print(f"[Makcu] {message}", flush=True)

    def find_com_port(self):
        self._log("Searching for CH343 device…")
        for port in list_ports.comports():
            if "VID:PID=1A86:55D3" in port.hwid.upper():
                self._log(f"Device found: {port.device}")
                return port.device
        self._log("No CH343 device found.")
        return None

    def _open_serial_port(self, port, baud_rate):
        try:
            self._log(f"Opening {port} at {baud_rate} baud…")
            return serial.Serial(port, baud_rate, timeout=0.05)
        except serial.SerialException:
            self._log(f"Failed to open {port} at {baud_rate} baud.")
            return None

    def _change_baud_to_4M(self):
        if not (self.serial and self.serial.is_open):
            return False
        self._log("Switching baud to 4,000,000…")
        self.serial.write(self.baud_change_command)
        self.serial.flush()
        time.sleep(0.05)
        self.serial.baudrate = 4000000
        self._current_baud = 4000000
        self._log("Baud switched to 4,000,000.")
        return True

    def connect(self):
        if self._is_connected:
            self._log("Already connected.")
            return
        self.serial = self._open_serial_port(self.port, 115200)
        if not self.serial:
            raise MakcuConnectionError(f"Cannot connect to {self.port} at 115200.")
        self._log(f"Connected to {self.port} at 115200.")
        if not self._change_baud_to_4M():
            raise MakcuConnectionError("Failed to switch to 4,000,000 baud.")
        self._is_connected = True

    def disconnect(self):
        with self._lock:
            if self.serial and self.serial.is_open:
                self.serial.close()
            self.serial = None
            self._is_connected = False
            self._log("Disconnected.")

    def is_connected(self):
        return self._is_connected

    def send_command(self, command, expect_response=False):
        if not (self._is_connected and self.serial and self.serial.is_open):
            raise MakcuConnectionError("Serial connection not open.")
        with self._command_lock:
            try:
                if expect_response:
                    self._response_buffer = ""
                    self._response_ready.clear()
                    self._waiting_for_response = True
                self.serial.write(command.encode("ascii") + b"\r\n")
                self.serial.flush()
                if expect_response:
                    response = self.receive_response(sent_command=command)
                    self._waiting_for_response = False
                    if not response:
                        raise MakcuTimeoutError(f"No response for: {command}")
                    return response
            except Exception:
                self._waiting_for_response = False
                raise
