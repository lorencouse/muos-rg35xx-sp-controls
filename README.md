# SP control pack for muOS (Anbernic RG35XX SP)

The RG35XX SP has no analog stick. [muOS](https://muos.dev) can make the
D-pad act as the left stick (**L2+R2+A** in game), but it forgets on every
launch, and N64's C-buttons hide behind a held R2. This pack makes the
controls right per system, automatically:

- **D-pad ↔ analog stick set at launch, per ROM folder** — N64, PSP and
  Dreamcast get stick mode (one rumble on launch), PlayStation stays on the
  D-pad. Per-game overrides.
- **N64 C-buttons on the face buttons** (Mupen64Plus-Next "Independent
  C-button Controls"): **B**=A, **Y**=B, **A**=C-Down, **X**=C-Up,
  **L1/R1**=C-Left/Right, **L2**=Z, **R2**=R, **SELECT**=L.
- **PlayStation pad type = analog**, so stick mode moves the character in
  analog games instead of being ignored.

## Install

1. Download `muOS-SP-Controls-<version>.muxzip` from the
   [latest release](../../releases/latest).
2. Copy it into the `ARCHIVE` folder on the SD card.
3. **Applications → Archive Manager**, select it. It installs:
   ```
   MUOS/init/30-sp-controls.sh   boot hook
   MUOS/info/override/           per-folder launch overrides + dpad.conf
   ```
4. Enable **Configuration → Advanced Settings → User Init Scripts**, reboot.

Works from SD1 or SD2. Uninstall: delete the two paths (the two RetroArch
core options it sets stay until you change them in RetroArch or reflash).

## Configure

`MUOS/info/override/sp-controls/dpad.conf`:

```ini
n64=stick        # whole folder: D-pad drives the analog stick
psp=stick
dreamcast=stick
psx=dpad         # plain D-pad; flip single games:
psx/Ape Escape (USA)=stick
```

Only folders with an override script are affected (`n64 psp dreamcast psx`);
copy `n64.sh` to `<folder>.sh` for another one. PortMaster ports are covered
separately by `gc-pad.py` (in `MUOS/info/override/sp-controls/`): a uinput
translator that turns the single D-pad into three channels — rest = left
stick, hold SELECT = real D-pad, hold R2 = right stick — with buttons 1:1 and
MENU opening the game's own menu. Wire it into a port's launch script (see
the header comments in `gc-pad.py`) and `touch <gamedir>/gc-pad.enable`.

Don't like the N64 layout? Set `mupen64plus-alt-map` to `False` in
`MUOS/init/30-sp-controls.sh` (or remove that line and change it in
RetroArch's core options).

## How it works

muOS has a *launch override* mechanism: if `MUOS/info/override/<folder>.sh`
exists, `launch.sh` runs it instead of the normal launcher. On stock 2601
that mechanism is inert — `launch.sh` reads `/opt/muos/share/info/override`,
which doesn't exist on the rootfs, while the card's folder is bound at
`/run/muos/storage/info/override`. The boot hook binds the card folder over
the rootfs path (only when the rootfs dir is absent or empty), and re-applies
the two RetroArch per-core options every boot since they live on the rootfs.

Each override sets `/sys/class/power_supply/axp2202-battery/nds_pwrkey`
(2 = stick, 0 = D-pad — the same file muOS's own toggle flips), then works
out which launcher muOS would have used (from `/tmp/ovl_go` + the matching
assign `.ini`) and execs it. `launch.sh` resets the mode when the game exits.

Log: `MUOS/log/controls.log`.

## Tested on

Anbernic RG35XX SP, muOS 2601.0 Jacaranda. The D-pad mode file is specific
to Anbernic H700 boards; the RetroArch options are generic.

Part of a set — see also
[Bluetooth + USB-C audio](https://github.com/lorencouse/muos-rg35xx-sp-bluetooth),
[Wi-Fi auto-connect](https://github.com/lorencouse/muos-wifi-autoconnect)
and the [all-in-one package](https://github.com/lorencouse/muos-rg35xx-sp-all-in-one).

## License

MIT — see [LICENSE](LICENSE).
