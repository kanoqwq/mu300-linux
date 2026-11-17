# SPDX-License-Identifier: MIT
"""Pre-attach H4 command channel. Never runs alongside the kernel HCI owner."""
import os
import array
import fcntl
import select
import struct
import termios
import time

from core import Invalid

class H4:
    def __init__(self, device, baud):
        self.fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            discipline = array.array('i', [0])
            fcntl.ioctl(self.fd, termios.TIOCGETD, discipline, True)
            if discipline[0] != 0:
                raise Invalid('bluetooth_uart_already_owned')
            speed = getattr(termios, 'B' + str(baud))
            settings = termios.tcgetattr(self.fd)
            settings[:4] = [0, 0, termios.CS8 | termios.CLOCAL | termios.CREAD | termios.CRTSCTS, 0]
            settings[4:6] = [speed, speed]
            settings[6][termios.VMIN], settings[6][termios.VTIME] = 0, 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
        except Exception:
            os.close(self.fd)
            raise

    def read(self, count, deadline):
        data = bytearray()
        while len(data) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise Invalid('bluetooth_uart_command_timed_out')
            part = os.read(self.fd, count - len(data))
            if not part:
                raise Invalid('bluetooth_uart_closed')
            data.extend(part)
        return bytes(data)

    def command(self, opcode, data=b''):
        if len(data) > 255:
            raise Invalid('invalid_hci_command')
        packet = b'\x01' + struct.pack('<HB', opcode, len(data)) + data
        deadline = time.monotonic() + 3
        while packet:
            if not select.select([], [self.fd], [], max(0,deadline-time.monotonic()))[1]:
                raise Invalid('bluetooth_uart_command_timed_out')
            packet = packet[os.write(self.fd, packet):]
        while True:
            if self.read(1,deadline) != b'\x04':
                raise Invalid('unexpected_bluetooth_uart_framing')
            event, length = self.read(2, deadline)
            payload = self.read(length, deadline)
            if event == 0x0e and len(payload) >= 4 and int.from_bytes(payload[1:3],'little') == opcode:
                if payload[3]:
                    raise Invalid('bluetooth_vendor_command_rejected')
                return payload[4:]
            if event == 0x0f and len(payload) >= 4 and int.from_bytes(payload[2:4],'little') == opcode and payload[0]:
                raise Invalid('bluetooth_vendor_command_rejected')

    def close(self):
        os.close(self.fd)

def aic_address(device, mac):
    controller = H4(device, 1500000)
    try:
        # Stock B16 libbt-vendor.so hw_config_set_bdaddr uses FC70, six reversed address bytes.
        # This is volatile controller configuration, not a write to calibration or modem NV.
        controller.command(0x0c03)
        controller.command(0xfc70, bytes.fromhex(mac.replace(':',''))[::-1])
    finally:
        controller.close()
