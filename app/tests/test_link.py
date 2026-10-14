"""Serial link over a pty pair: the test plays the control on the master side."""

import os
import threading
import time
import tty
import unittest

from alphacam.link import serial_link as sl

SAMPLE = b"%\r\nO2\r\n#132=5\r\n" + b"G1X40.Z-50.F0.3\r\n" * 40 + b"M65\r\n%\r\n"


class PtyControl:
    """The master end of a pty acting as the Alpha-T control."""

    def __init__(self):
        self.master, slave = os.openpty()
        tty.setraw(self.master)
        self.slave_name = os.ttyname(slave)
        self._slave = slave
        self.received = bytearray()

    def read_until(self, n: int, timeout: float = 10.0) -> None:
        t0 = time.monotonic()
        while len(self.received) < n and time.monotonic() - t0 < timeout:
            try:
                self.received += os.read(self.master, 4096)
            except BlockingIOError:
                time.sleep(0.005)

    def close(self):
        os.close(self.master)
        os.close(self._slave)


class SendReceive(unittest.TestCase):
    def setUp(self):
        self.ctl = PtyControl()
        os.set_blocking(self.ctl.master, False)
        self.ser = sl.open_port(self.ctl.slave_name)

    def tearDown(self):
        self.ser.close()
        self.ctl.close()

    def test_send_waits_for_xon_and_honours_xoff(self):
        log = sl.TransferLog()
        done = {}

        def control():
            time.sleep(0.2)
            self.ctl.read_until(1, 0.05)
            done["before_xon"] = len(self.ctl.received)  # nothing may arrive before DC1
            os.write(self.ctl.master, bytes([sl.XON]))
            self.ctl.read_until(100)
            os.write(self.ctl.master, bytes([0x13]))  # DC3: pause
            time.sleep(0.05)
            self.ctl.read_until(10**6, 0.3)
            done["paused_at"] = len(self.ctl.received)
            time.sleep(0.3)
            done["still_paused"] = len(self.ctl.received) == done["paused_at"]
            os.write(self.ctl.master, bytes([sl.XON]))
            self.ctl.read_until(len(SAMPLE))

        t = threading.Thread(target=control)
        t.start()
        n = sl.send(self.ser, SAMPLE, sl.SendOptions(char_delay_ms=0), log=log)  # paced like a 9600 baud UART
        t.join()
        self.assertEqual(n, len(SAMPLE))
        self.assertEqual(bytes(self.ctl.received), SAMPLE)
        self.assertEqual(done["before_xon"], 0)
        self.assertTrue(done["still_paused"])
        self.assertLess(done["paused_at"] - 100, 10 + 4)  # stopped within 10 characters of DC3 (FANUC 087)
        self.assertTrue(any("XOFF" in e for _, e in log.events))

    def test_send_cancel_while_waiting(self):
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()
        with self.assertRaises(sl.Cancelled):
            sl.send(self.ser, SAMPLE, cancel=cancel)
        self.assertFalse(self.ctl.received)

    def test_xon_timeout(self):
        with self.assertRaises(sl.TransferTimeout):
            sl.send(self.ser, SAMPLE, sl.SendOptions(xon_timeout=0.2))

    def test_receive_until_second_percent(self):
        backup = b"%\r\n:0001\r\nG01X1.Z-2.\r\n%\r\n"
        threading.Timer(0.1, lambda: os.write(self.ctl.master, backup + b"trailing")).start()
        got = sl.receive(self.ser, first_timeout=3)
        self.assertEqual(got, backup[:-2])  # up to and including the second %, like ATMANOP Save

    def test_receive_idle_timeout(self):
        threading.Timer(0.05, lambda: os.write(self.ctl.master, b"%\r\nhalf")).start()
        with self.assertRaises(sl.TransferTimeout) as cm:
            sl.receive(self.ser, first_timeout=3, idle_timeout=0.3)
        self.assertEqual(cm.exception.data, b"%\r\nhalf")  # what arrived is kept, not thrown away

    def test_receive_ends_at_dc4(self):
        """The NC ends its output with DC4; a backup with one % (or none) still ends there."""
        backup = b"\x12%\r\n:0001\r\nG01X1.Z-2.\r\n\x14"
        threading.Timer(0.1, lambda: os.write(self.ctl.master, backup + b"trailing")).start()
        got = sl.receive(self.ser, first_timeout=3, idle_timeout=2)
        self.assertEqual(got, backup)


class Pure(unittest.TestCase):
    def test_manops_load_parity(self):
        self.assertEqual(sl.manops_load_bytes(b"%\x00A"), bytes([0x25 | 0x80, 0x00, 0x41]))
        self.assertEqual(sl.manops_load_bytes(b"\xff"), b"\x00")
        # 'C' = 0x43 has three bits set -> parity bit on
        self.assertEqual(sl.manops_load_bytes(b"C"), bytes([0xC3]))

    def test_loopback_with_echo(self):
        class Echo:
            def __init__(self):
                self.buf = b""

            def reset_input_buffer(self):
                self.buf = b""

            def write(self, b):
                self.buf += b

            def flush(self):
                pass

            def read(self, n):
                out, self.buf = self.buf[:n], self.buf[n:]
                return out

        ok, got = sl.loopback(Echo())
        self.assertTrue(ok)
