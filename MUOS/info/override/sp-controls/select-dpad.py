#!/usr/bin/env python3
# sp-controls: hold-SELECT D-pad modifier.
#
# Runs alongside a game whose dpad.conf mode ends in "+select-dpad" (or is
# "dpad+select-stick"). Normal mode: the D-pad is in stick mode
# (nds_pwrkey=2); while SELECT is physically held the mode flips to the
# real D-pad (nds_pwrkey=0), and back on release.
#
# --invert swaps the two (dpad.conf mode "dpad+select-stick"): the D-pad
# rests in real D-pad mode (0) and SELECT flips to stick mode (2). Needed
# for flycast vl, whose stickless-handheld input patch crosses the
# channels in-core (RetroPad d-pad drives the DC analog stick, the
# analog axes drive the DC d-pad) — observed in Shenmue II 2026-08-31.
#
# Each flip injects centring events for the channel being left, so a
# direction held across the flip can't get stuck for readers like
# RetroArch. The SP pad declares ABS_RX/RY (codes 3/4), NOT ABS_X/Y —
# the kernel silently drops injected codes 0/1.
#
# argv[1] = PID of the launcher (sp-controls launch.sh exec's the real
# launcher, keeping its PID); the watcher exits when that PID is gone.

import os
import select
import struct
import sys

EV_DEV = "/dev/input/event1"
DPAD_FILE = "/sys/class/power_supply/axp2202-battery/nds_pwrkey"
SELECT_CODE = 310  # BTN_SELECT on the SP pad
FMT = "llHHi"
EV_SIZE = struct.calcsize(FMT)

PARENT = int(sys.argv[1]) if len(sys.argv) > 1 else None
INVERT = "--invert" in sys.argv[2:]

BASE, HELD = (0, 2) if INVERT else (2, 0)
AXES_CENTRE = ((3, 0), (4, 0))  # ABS_RX / ABS_RY: the fake stick's axes
HAT_CENTRE = ((16, 0), (17, 0))  # ABS_HAT0X / ABS_HAT0Y: the real D-pad


def centre_for(mode):
    # Centring events for the channel that `mode` was emitting.
    return AXES_CENTRE if mode == 2 else HAT_CENTRE


def set_mode(value):
    try:
        with open(DPAD_FILE, "w") as f:
            f.write(str(value))
    except OSError:
        pass


def emit_centre(fd, pairs):
    for code, value in pairs:
        os.write(fd, struct.pack(FMT, 0, 0, 3, code, value))
    os.write(fd, struct.pack(FMT, 0, 0, 0, 0, 0))


def main():
    rfd = os.open(EV_DEV, os.O_RDONLY | os.O_NONBLOCK)
    wfd = os.open(EV_DEV, os.O_WRONLY)
    held = False
    while True:
        ready, _, _ = select.select([rfd], [], [], 1.0)
        if PARENT is not None and not os.path.exists("/proc/%d" % PARENT):
            break
        if not ready:
            continue
        try:
            data = os.read(rfd, EV_SIZE * 64)
        except BlockingIOError:
            continue
        for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
            _, _, etype, code, value = struct.unpack_from(FMT, data, off)
            if etype != 1 or code != SELECT_CODE:
                continue
            if value == 1 and not held:
                held = True
                set_mode(HELD)
                emit_centre(wfd, centre_for(BASE))
            elif value == 0 and held:
                held = False
                set_mode(BASE)
                emit_centre(wfd, centre_for(HELD))
    if held:
        set_mode(BASE)


if __name__ == "__main__":
    main()
