#!/usr/bin/env python3
# sp-controls: GameCube-style pad for the stickless RG35XX SP.
#
# The SP has one D-pad and no analog sticks, but a GameCube game wants
# three separate directional inputs (control stick, D-pad, C-stick). We
# grab the physical pad and synthesize a virtual one, routing the single
# D-pad to a different channel depending on which modifier is held:
#
#   (nothing)   D-pad -> control stick   (ABS_X/ABS_Y)
#   SELECT held D-pad -> D-pad           (ABS_HAT0X/Y)
#   R2 held     D-pad -> C-stick         (ABS_RX/ABS_RY)
#
# Buttons are forwarded with their PHYSICAL codes (a:304 b:305 y:306
# x:307 l1:308 r1:309 sel:310 start:311 guide:312 l2:314 r2:315), so
# muOS-level listeners that also watch the virtual pad (muxhotkey,
# gptokeyb2 - Select+Start still quits the port) see the exact codes the
# real pad would send. The game-facing meaning is given by an explicit
# SDL_GAMECONTROLLERCONFIG mapping exported by the port's launch script
# (see gc-pad.mapping in this directory), NOT by the evdev codes:
# L1/R1 -> GC L/R triggers, L2 -> GC Z, SELECT -> back, guide -> guide.
# The earlier revision remapped codes to the "standard" gamepad layout
# and collided SELECT with L and R2 with Start; SDL also never auto-maps
# an unknown uinput GUID, which is why the game ignored the virtual pad.
#
# MENU tap: the pad sends 354 on a short MENU press; we synthesize
# guide (312) from it, which the launch script binds to the game's own
# settings menu. muhotkey can't help here: it never opens the virtual
# pad, and menu_tap.sh's injected fallback can't reach us either (the
# kernel drops keys injected into a grabbed device by anyone but the
# grab holder).
#
# nds_pwrkey is forced to 0 so the pad always emits the real D-pad on
# ABS_HAT0X/Y; we do all channel routing ourselves rather than flipping
# the kernel's stick mode (which only offers two channels, and emits
# ABS_RX/RY - the *right* stick - for its fake stick).
#
# argv[1] = PID to follow; the translator exits when that PID is gone,
# releasing the grab. Same contract as select-dpad.py.
# argv[2:] may contain --no-grab: run without the exclusive grab, so
# keys injected into event1 from adb still reach us (testing only).

import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import time

EV_DEV = "/dev/input/event1"
UINPUT = "/dev/uinput"
DPAD_FILE = "/sys/class/power_supply/axp2202-battery/nds_pwrkey"

# --- physical SP pad codes (from notes) ---
P_A, P_B, P_Y, P_X = 304, 305, 306, 307
P_L1, P_R1, P_SEL, P_START = 308, 309, 310, 311
P_GUIDE, P_L2, P_R2 = 312, 314, 315
P_MENU = 354

# Buttons forwarded 1:1 (modifiers SELECT/R2 included - they double as
# real buttons so quit chords and the SDL mapping can see them).
BUTTONS = {P_A, P_B, P_Y, P_X, P_L1, P_R1, P_SEL, P_START,
           P_GUIDE, P_L2, P_R2}

# event1 is NOT just the gamepad: the same device carries volume, MENU
# and ESC, and we grab it exclusively. Forwarding volume to the virtual
# pad is NOT enough: muhotkey (which runs audio.sh on VOL_UP/VOL_DOWN)
# only opens the devices named in the muOS config - event0/event1 - and
# never the virtual pad, so under the grab it goes deaf. We run audio.sh
# ourselves instead, with our own hold-to-repeat.
K_ESC, K_VOLDOWN, K_VOLUP = 1, 114, 115
PASSTHROUGH = {K_ESC, K_VOLDOWN, K_VOLUP, P_MENU}
AUDIO_SH = "/opt/muos/script/device/audio.sh"
VOL_KEYS = {K_VOLDOWN: "D", K_VOLUP: "U"}
VOL_REPEAT_DELAY = 0.4    # held key starts repeating after this
VOL_REPEAT_EVERY = 0.25   # matches the select() timeout granularity

# muhotkey is also the IDLE detector: blind under our grab, it declares
# the device idle mid-game - muting audio (idle_mute), dimming the
# screen (idle_display) and finally SUSPENDING it (idle_sleep). The
# handlers re-read these settings at fire time, so zeroing them for the
# session cleanly disables idle; originals are restored on exit and
# also snapshotted to idle-settings.saved, which the boot hook restores
# if we ever die uncleanly (the settings live on the persistent rootfs).
IDLE_DIR = "/opt/muos/config/settings/power"
IDLE_KEYS = ("idle_display", "idle_sleep", "idle_mute")
IDLE_SAVE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "idle-settings.saved")

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
ABS_X, ABS_Y, ABS_RX, ABS_RY = 0, 1, 3, 4
ABS_HAT0X, ABS_HAT0Y = 16, 17

FMT = "llHHi"
EV_SIZE = struct.calcsize(FMT)

UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EVIOCGRAB = 0x40044590

STICK_MAX = 32767
PANIC_SECS = 2.0   # hold SELECT+START this long to drop the grab
MODE_STICK, MODE_DPAD, MODE_CSTICK = 0, 1, 2
CHANNELS = {
    MODE_STICK: (ABS_X, ABS_Y, STICK_MAX),
    MODE_CSTICK: (ABS_RX, ABS_RY, STICK_MAX),
    MODE_DPAD: (ABS_HAT0X, ABS_HAT0Y, 1),
}

PARENT = int(sys.argv[1]) if len(sys.argv) > 1 else None
NOGRAB = "--no-grab" in sys.argv[2:]

# Optional per-port MENU override: GCPAD_MENU_KEYS="311,309" makes a MENU
# tap synthesize that key sequence (the game's own menu chord, e.g. Start
# then R1 for the sm64ex family) instead of a guide press - for ports
# whose menu is not reachable from a single button.
MENU_KEYS = [int(c) for c in
             os.environ.get("GCPAD_MENU_KEYS", "").replace("+", ",").split(",")
             if c.strip().isdigit()]


def make_uinput():
    fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
    for ev in (EV_KEY, EV_ABS):
        fcntl.ioctl(fd, UI_SET_EVBIT, ev)
    for code in BUTTONS | PASSTHROUGH:
        fcntl.ioctl(fd, UI_SET_KEYBIT, code)
    for axis in (ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_HAT0X, ABS_HAT0Y):
        fcntl.ioctl(fd, UI_SET_ABSBIT, axis)

    absmax = [0] * 64
    absmin = [0] * 64
    for axis in (ABS_X, ABS_Y, ABS_RX, ABS_RY):
        absmax[axis], absmin[axis] = STICK_MAX, -STICK_MAX
    for axis in (ABS_HAT0X, ABS_HAT0Y):
        absmax[axis], absmin[axis] = 1, -1

    # struct uinput_user_dev: name[80], input_id{4xu16}, ff_effects_max,
    # then absmax/absmin/absfuzz/absflat (64 s32 each).
    dev = struct.pack(
        "<80sHHHHI" + "i" * 256,
        b"sp-controls GameCube Pad",
        0x0003, 0x1209, 0x6743, 0x0111,
        0,
        *(absmax + absmin + [0] * 64 + [0] * 64)
    )
    os.write(fd, dev)
    fcntl.ioctl(fd, UI_DEV_CREATE)
    return fd


def emit(fd, etype, code, value):
    os.write(fd, struct.pack(FMT, 0, 0, etype, code, value))


def syn(fd):
    emit(fd, EV_SYN, 0, 0)


def write_channel(fd, mode, hx, hy):
    ax, ay, scale = CHANNELS[mode]
    emit(fd, EV_ABS, ax, hx * scale)
    emit(fd, EV_ABS, ay, hy * scale)


def centre_channel(fd, mode):
    ax, ay, _ = CHANNELS[mode]
    emit(fd, EV_ABS, ax, 0)
    emit(fd, EV_ABS, ay, 0)


def current_mode(sel_held, r2_held):
    # Both held: fall back to the control stick; SELECT and R2 are still
    # forwarded as buttons, so in-game chords keep working.
    if sel_held and r2_held:
        return MODE_STICK
    if sel_held:
        return MODE_DPAD
    if r2_held:
        return MODE_CSTICK
    return MODE_STICK


def set_volume(direction):
    subprocess.Popen(["/bin/sh", AUDIO_SH, direction],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def disable_idle():
    saved = {}
    lines = []
    for key in IDLE_KEYS:
        path = os.path.join(IDLE_DIR, key)
        try:
            with open(path) as f:
                saved[key] = f.read().strip()
            lines.append("%s=%s\n" % (key, saved[key]))
            with open(path, "w") as f:
                f.write("0")
        except OSError:
            pass
    try:
        with open(IDLE_SAVE, "w") as f:
            f.writelines(lines)
    except OSError:
        pass
    # If idle already fired we start muted with a dimmed screen: wake up.
    subprocess.Popen(
        ["/bin/sh", "-c", ". /opt/muos/script/var/func.sh; DISPLAY_ACTIVE"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return saved


def restore_idle(saved):
    for key, val in saved.items():
        try:
            with open(os.path.join(IDLE_DIR, key), "w") as f:
                f.write(val)
        except OSError:
            pass
    try:
        os.unlink(IDLE_SAVE)
    except OSError:
        pass


def main():
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)   # reap audio.sh children
    # The launch script ends us with SIGTERM; turn it into SystemExit so
    # the finally block still releases the grab and restores idle settings.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        with open(DPAD_FILE, "w") as f:
            f.write("0")          # force real D-pad; we route it ourselves
    except OSError:
        pass

    rfd = os.open(EV_DEV, os.O_RDONLY | os.O_NONBLOCK)
    ufd = make_uinput()
    if not NOGRAB:
        try:
            fcntl.ioctl(rfd, EVIOCGRAB, 1)
        except OSError:
            pass                  # keep running un-grabbed rather than dying

    hx = hy = 0
    sel_held = r2_held = False
    start_held = False
    panic_since = None
    mode = MODE_STICK
    vol_dir = None
    vol_next = 0.0
    idle_saved = disable_idle()

    try:
        while True:
            ready, _, _ = select.select([rfd], [], [], 0.25)
            if PARENT is not None and not os.path.exists("/proc/%d" % PARENT):
                break

            # Panic escape. We read the physical stream directly, so this
            # works even when the game is receiving nothing at all - which
            # is the exact situation where you're otherwise hard-locked,
            # unable to exit the game to end the grab. Hold SELECT+START.
            if panic_since is not None and time.time() - panic_since >= PANIC_SECS:
                break

            if vol_dir is not None and time.time() >= vol_next:
                set_volume(vol_dir)
                vol_next = time.time() + VOL_REPEAT_EVERY

            if not ready:
                continue

            data = os.read(rfd, EV_SIZE * 32)
            for off in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                _, _, etype, code, value = struct.unpack(
                    FMT, data[off:off + EV_SIZE])

                if etype == EV_ABS and code in (ABS_HAT0X, ABS_HAT0Y):
                    if code == ABS_HAT0X:
                        hx = value
                    else:
                        hy = value
                    write_channel(ufd, mode, hx, hy)
                    syn(ufd)

                elif etype == EV_KEY and code in (P_SEL, P_R2):
                    if code == P_SEL:
                        sel_held = value != 0
                    else:
                        r2_held = value != 0
                    panic_since = (time.time()
                                   if sel_held and start_held else None)
                    new_mode = current_mode(sel_held, r2_held)
                    if new_mode != mode:
                        # Centre the channel we're leaving so a direction
                        # held across the flip can't stick.
                        centre_channel(ufd, mode)
                        mode = new_mode
                        write_channel(ufd, mode, hx, hy)
                    emit(ufd, EV_KEY, code, value)
                    syn(ufd)

                elif etype == EV_KEY and code in VOL_KEYS:
                    if value == 1:
                        set_volume(VOL_KEYS[code])
                        vol_dir = VOL_KEYS[code]
                        vol_next = time.time() + VOL_REPEAT_DELAY
                    elif value == 0 and vol_dir == VOL_KEYS[code]:
                        vol_dir = None
                    emit(ufd, EV_KEY, code, value)
                    syn(ufd)

                elif etype == EV_KEY and code in (P_MENU, P_GUIDE) and MENU_KEYS:
                    # Port with a menu key sequence: emit ONLY the sequence.
                    # The pad sends guide (312) when MENU is held a beat; our
                    # mapping exposes 312 as SDL guide, and gptokeyb kills
                    # the app on guide+start - which the synthesized Start
                    # tap would complete (verified on-device: 312 held +
                    # 311 tap = instant quit). So both MENU codes are
                    # swallowed here, never forwarded.
                    if code == P_MENU and value == 1:
                        # Deliberate taps: 0.15s press so a frame-sampled
                        # game can't miss it, 0.6s between keys so the
                        # first key's menu finishes opening (menu_tap.sh's
                        # proven flow used 0.5s).
                        for i, mk in enumerate(MENU_KEYS):
                            if i:
                                time.sleep(0.6)
                            emit(ufd, EV_KEY, mk, 1)
                            syn(ufd)
                            time.sleep(0.15)
                            emit(ufd, EV_KEY, mk, 0)
                            syn(ufd)

                elif etype == EV_KEY and code == P_MENU:
                    # MENU tap -> guide, which the launch script binds to
                    # the game's own menu. (Raw 312 from a long MENU hold
                    # still forwards via the button branch below - holding
                    # MENU and pressing Start is gptokeyb's force-quit.)
                    emit(ufd, EV_KEY, P_MENU, value)
                    emit(ufd, EV_KEY, P_GUIDE, value)
                    syn(ufd)

                elif etype == EV_KEY and code in BUTTONS:
                    if code == P_START:
                        start_held = value != 0
                        panic_since = (time.time()
                                       if sel_held and start_held else None)
                    emit(ufd, EV_KEY, code, value)
                    syn(ufd)

                elif etype == EV_KEY:
                    # Volume/ESC and anything else on this device. We hold
                    # an exclusive grab, so failing to forward these makes
                    # them dead keys system-wide.
                    emit(ufd, EV_KEY, code, value)
                    syn(ufd)
    finally:
        restore_idle(idle_saved)
        try:
            fcntl.ioctl(rfd, EVIOCGRAB, 0)
        except OSError:
            pass
        try:
            fcntl.ioctl(ufd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(ufd)
        os.close(rfd)


if __name__ == "__main__":
    main()
