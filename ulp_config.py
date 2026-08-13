#!/usr/bin/env python3
"""
ulp_config.py - Native Linux configurator for Calypso ULP485 / ULP UART
ultrasonic anemometers.

Protocol reverse-engineered from Calypso's ConfiguradorULP 1.24
(PyInstaller/wxPython app). Verified: the NMEA-style XOR checksum
reimplementation matches all 16 checksums hardcoded in the vendor binary.

How the sensor's config mode works:
  * On power-up the ULP transmits a "$START_CONFIG..." beacon at
    19200 8N1 (regardless of its configured operating baud) and briefly
    listens for configurator commands before dropping into normal
    operation. All configuration happens at 19200.
  * The vendor app holds the device captive by repeatedly sending
    $RESET94399*6B and re-catching the boot beacon. This tool does the
    same between steps.

Usage examples:
  # Just identify the sensor (then it resumes normal operation on next power cycle):
  ./ulp_config.py --port /dev/ttyUSB0 --info

  # Configure: NMEA stream mode, 38400 baud, 4 Hz, medium filter, m/s:
  ./ulp_config.py --port /dev/ttyUSB0 --mode stream --baud 38400 \
      --rate 4 --filter medium --units ms

  # After configuring, power-cycle the sensor and watch the output:
  ./ulp_config.py --port /dev/ttyUSB0 --monitor --baud 38400

The connect dance (same as the Windows app):
  1. Wire white(GND), green(A), yellow(B) - but NOT brown (VCC).
  2. Run this tool; when prompted, connect the brown wire.
"""

import argparse
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("pyserial required:  pip install pyserial  (or apt install python3-serial)")

CONFIG_BAUD = 19200  # boot beacon / config session baud, always

MODES = {
    "stream": "ULTRAMODE_02",
    "demand": "ULTRAMODE_03",
    "modbus": "ULTRAMODE_06",
    "i2c":    "ULTRAMODE_08",
    "sdi12":  "ULTRAMODE_09",
}

# filter level -> (median, repeticion); damping is always 00
FILTERS = {
    "lowpower": ("FILTER_MEDIAN_03", "FILTER_REPETICION_01"),
    "low":      ("FILTER_MEDIAN_05", "FILTER_REPETICION_02"),
    "medium":   ("FILTER_MEDIAN_10", "FILTER_REPETICION_05"),
    "high":     ("FILTER_MEDIAN_20", "FILTER_REPETICION_10"),
}

UNITS = {"knots": "NMEA_UNITS_N", "kmh": "NMEA_UNITS_K", "ms": "NMEA_UNITS_M"}

RATES = ["0.1", "0.2", "0.5", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]

BAUDS = ["1200", "2400", "4800", "9600", "14400", "19200", "38400", "57600", "115200"]


def checksum(msg: str) -> str:
    chk = 0
    for ch in msg:
        chk ^= ord(ch)
    return hex(chk)[2:].upper().zfill(2)


def frame(msg: str) -> bytes:
    return b"$" + msg.encode() + b"*" + checksum(msg).encode() + b"\r\n"


def rate_cmd(rate: str) -> bytes:
    # $DATARATE_01,00  /  $DATARATE_00,50  etc (XX,YY = Hz with 2 decimals, comma)
    whole, _, frac = rate.partition(".")
    msg = f"DATARATE_{int(whole):02d},{(frac or '0'):0<2s}"
    return frame(msg)


def baud_cmd(baud: str) -> bytes:
    # 6-digit zero-padded field: 9600 -> 009600, 38400 -> 038400, 115200 -> 115200
    return frame("BAUDRATE_" + baud.zfill(6))


class ULP:
    def __init__(self, port: str, verbose: bool = True):
        self.ser = serial.Serial(port, CONFIG_BAUD, timeout=0.8)
        self.verbose = verbose

    def log(self, *a):
        if self.verbose:
            print(*a)

    def expect(self, wr: bytes, token: bytes, cmd_timeout=0.5, global_timeout=30):
        """Vendor's detect_command: (re)send wr, scan lines for token."""
        start = time.time()
        self.ser.reset_output_buffer()
        while time.time() - start < global_timeout:
            if wr:
                self.ser.write(wr)
                self.log(f"  -> {wr!r}")
                time.sleep(0.1)
            sent = time.time()
            while time.time() - sent < cmd_timeout:
                ln = self.ser.readline()
                if ln:
                    self.log(f"  <- {ln!r}")
                    if b"STOP_CONFIG" in ln.upper():
                        raise RuntimeError(
                            "Sensor left config mode ($STOP_CONFIG) - the config "
                            "window expires ~5s after the last accepted command, "
                            "so a command was probably rejected. Redo the brown-wire "
                            "power cycle and try again.")
                    if token.upper() in ln.upper():
                        return ln
        raise TimeoutError(f"no {token!r} response within {global_timeout}s")

    # --- session steps -------------------------------------------------
    def soft_reset(self, op_baud: str, timeout=10) -> bool:
        """Try to reboot the sensor by sending $RESET at its operating baud.
        Returns True if the boot beacon was caught (no power cycle needed)."""
        print(f"Attempting soft reset at {op_baud} baud...")
        self.ser.baudrate = int(op_baud)
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                self.ser.write(frame("RESET94399"))
                self.log(f"  -> {frame('RESET94399')!r} @ {op_baud}")
                time.sleep(0.2)
                # beacon comes at CONFIG_BAUD; hop over and listen briefly
                self.ser.baudrate = CONFIG_BAUD
                sent = time.time()
                while time.time() - sent < 1.5:
                    ln = self.ser.readline()
                    if ln:
                        self.log(f"  <- {ln!r}")
                        if b"START_CONF" in ln.upper():
                            print("Soft reset worked - ULP entered config mode.")
                            return True
                self.ser.baudrate = int(op_baud)
            return False
        finally:
            self.ser.baudrate = CONFIG_BAUD

    def catch_boot(self, timeout=120, soft_baud=None):
        if soft_baud and self.soft_reset(soft_baud):
            return
        if soft_baud:
            print("Soft reset not accepted by this firmware - falling back "
                  "to manual power cycle.")
        print("\nDisconnect the BROWN (power) wire now if it's connected.")
        input("Press Enter, then reconnect BROWN when told... ")
        print(">>> Connect the BROWN wire NOW - waiting for boot beacon...")
        self.expect(b"", b"START_CONF", cmd_timeout=0.5, global_timeout=timeout)
        print("ULP entered config mode.")

    def reset_reenter(self):
        """Reset the sensor and re-catch the config window."""
        self.expect(frame("RESET94399"), b"START_CONFIG", global_timeout=6)

    def read_info(self):
        uid = self.expect(frame("GETID94799"), b"UID")
        hw = self.expect(frame("GETHWVERSION53"), b"HW")
        fw = self.expect(frame("GETFWVERSION53"), b"FW")
        return uid, hw, fw

    def configure(self, mode, baud, rate, filt, units):
        print("\nConfiguring...")
        self.expect(frame(MODES[mode]), b"ULTRAMOD")
        if mode not in ("i2c", "sdi12") and baud:
            self.expect(baud_cmd(baud), b"$BAUDRATE")
        if filt:
            median, rep = FILTERS[filt]
            self.expect(frame(median), b"$FILTER_MED")
            self.expect(frame(rep), b"$FILTER_REP")
            self.expect(frame("FILTER_DAMPING_00"), b"$FILTER_DAM")
        if mode in ("stream", "i2c") and rate:
            self.expect(rate_cmd(rate), b"$DATARATE")
        if units:
            self.expect(frame(UNITS[units]), b"$NMEA_UNITS")
        print("\nCONFIGURATION COMPLETE.")
        print("Power-cycle the sensor (brown wire off/on) to run with new settings.")


def monitor(port, baud):
    ser = serial.Serial(port, int(baud), timeout=2)
    print(f"Monitoring {port} @ {baud} (Ctrl-C to stop)")
    try:
        while True:
            ln = ser.readline()
            if ln:
                sys.stdout.write(ln.decode(errors="replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--info", action="store_true", help="identify sensor only")
    p.add_argument("--monitor", action="store_true",
                   help="just print the sensor's output stream (use with --baud)")
    p.add_argument("--mode", choices=MODES, help="output mode")
    p.add_argument("--baud", choices=BAUDS, help="operating baud rate to set "
                   "(or, with --monitor, the baud to listen at)")
    p.add_argument("--rate", choices=RATES, help="data rate in Hz (stream mode)")
    p.add_argument("--filter", dest="filt", choices=FILTERS, help="wind filter")
    p.add_argument("--units", choices=UNITS, help="wind speed units")
    p.add_argument("-q", "--quiet", action="store_true", help="hide raw serial traffic")
    p.add_argument("--soft-reset", metavar="OP_BAUD", dest="soft",
                   help="first try $RESET at the sensor's current operating baud "
                        "(e.g. 38400) to enter config mode without a power cycle")
    args = p.parse_args()

    if args.monitor:
        monitor(args.port, args.baud or "38400")
        return

    if not args.info and not args.mode:
        p.error("either --info, --monitor, or --mode <...> is required")

    ulp = ULP(args.port, verbose=not args.quiet)
    ulp.catch_boot(soft_baud=args.soft)
    uid, hw, fw = ulp.read_info()
    print(f"\nSerial Number: {uid.decode(errors='replace').strip()}")
    print(f"Hardware:      {hw.decode(errors='replace').strip()}")
    print(f"Firmware:      {fw.decode(errors='replace').strip()}")

    if args.info:
        print("\nDone. Power-cycle the sensor to resume normal operation.")
        return

    # hold the device in the config window, then push settings
    ulp.reset_reenter()
    ulp.configure(args.mode, args.baud, args.rate, args.filt, args.units)


if __name__ == "__main__":
    main()
