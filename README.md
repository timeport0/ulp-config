# ulp-config

Native Linux configurator for **Calypso ULP485 / ULP UART** ultrasonic
anemometers. A single Python script that replaces the vendor's Windows-only
*ConfiguradorULP* for identifying a sensor and setting its output mode, baud
rate, data rate, wind filter and units over a USB-RS485 adapter.

The protocol was worked out from ConfiguradorULP 1.24 and verified on real
hardware; the NMEA-style XOR checksums match every command the vendor tool
sends.

## Requirements

- Python 3
- [pyserial](https://pypi.org/project/pyserial/) — `pip install pyserial`
  or `apt install python3-serial`
- A USB-RS485 adapter on the A/B pair (white = GND, green = A, yellow = B,
  brown = VCC)

## Usage

```sh
# identify the sensor
./ulp_config.py --port /dev/ttyUSB0 --info

# configure: NMEA stream, 38400 baud, 4 Hz, medium filter, m/s
./ulp_config.py --port /dev/ttyUSB0 --mode stream --baud 38400 \
    --rate 4 --filter medium --units ms

# watch the output after a power cycle
./ulp_config.py --port /dev/ttyUSB0 --monitor --baud 38400

# try to enter config mode without unplugging the brown wire
./ulp_config.py --port /dev/ttyUSB0 --soft-reset 38400 --info
```

All configuration happens at 19200 8N1 during the boot beacon the sensor
sends on power-up, regardless of its configured operating baud. The tool
walks you through the brown-wire power cycle when it needs one, and
`--soft-reset` tries a `$RESET` at the current operating baud first.

Run `./ulp_config.py --help` for every option. See the docstring at the top
of the script for how the config window behaves.
