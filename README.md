# Telly Remote

Control a [Telly](https://www.telly.com) dual-screen TV from your computer: a web remote
that mirrors the physical TYRC-1 remote, plus things the remote can't do (one-click mood
lighting colours, input and app shortcuts, a "sleep everything" key, typing into search
boxes).

A Flipper Zero does the talking. It pairs with the TV **over Bluetooth as a second Telly
remote**, so nothing has to point at the TV, and falls back to **infrared** for the few
buttons whose Bluetooth codes aren't known yet.

```
Browser → server.py (localhost) → USB → Flipper Zero ─┬─ Bluetooth (HID) → Telly
                                                       └─ Infrared        → Telly
```

> **Not affiliated with Telly.** Telly TVs are free because they show ads; the user
> agreement requires the TV to stay online with its ad screen working. This project only
> sends remote-control key presses and doesn't touch the ads, and that's how it should stay.

## What it does

- **Full remote**: Power, Input, the red Telly button, Back, Home, Screen ▲/▼, d-pad + OK,
  A/B, Mute, Volume, Menu, with keyboard shortcuts.
- **Mood lighting**: on/off, brightness and all 15 effects in about half a second (the
  Flipper replays the Settings menu path itself).
- **Shortcuts**: HDMI inputs (keys 1/2/3), TV Tuner, music apps, Game Room, video calling,
  Telly Fit, and every Telly Settings page.
- **Sleep**: a Bluetooth key the physical remote doesn't have; it turns off both screens and
  the mood lighting. Power wakes the TV.
- **Bluetooth key explorer** and **Type on the TV** for trying other standard keys.

## Requirements

- Telly TV (tested on model TY55_1).
- Flipper Zero running **firmware 1.5.1 with the patch in
  [`flipper/firmware/`](flipper/firmware/)** and the **Telly Remote** app from
  [`flipper/telly_remote/`](flipper/telly_remote/). Infrared alone works on stock firmware.
- macOS (Linux should work for the server; the Flipper port glob may need adjusting) and
  Python 3. The server uses only the standard library.

## Run

1. Plug the Flipper into the computer over USB. Close qFlipper, which locks the port.
2. On the Flipper, open **Apps → Bluetooth → Telly Remote** (needed for Bluetooth).
3. `python3 server.py`, then open http://localhost:8642. The header shows
   "Bluetooth connected" or "IR only".

Optional: copy `config.example.json` to `config.json` to name your HDMI inputs.

### Pairing the Flipper with the TV (once)

1. On the TV: **Menu → Remote Control → Pair**.
2. *Then* start the Telly Remote app, so it's in its first ~30 s of fast advertising.
3. When the TV shows a 6-digit passkey, press **OK on the Flipper**, then **Pair** on the TV.

After that the TV reconnects by itself within a few seconds whenever the app starts.

## How it works

### Infrared

The TYRC-1 remote sends **NEC extended, address 0xFE02** until it's paired by Bluetooth,
and the TV always listens for it. Captured with the Flipper's IR receiver
(`tools/ir_capture.py`):

| Button | Code | Button | Code |
|---|---|---|---|
| Power | FF00 | A | 9E61 |
| Input | 9F60 | Screen ▼ | A857 |
| Telly (red) | A758 | B | 9D62 |
| Back | E619 | Mute | BE41 |
| Screen ▲ | A956 | Vol + | B748 |
| Home | A35C | Vol − | F40B |
| Up / Down | A659 / AE51 | Menu ≡ | DC23 |
| Left / Right | AB54 / EA15 | OK | AA55 |

The TV has no network remote API: a full port scan finds only Spotify Connect.

### Bluetooth

The TV's pairing screen only accepts its own remote, and matching the name and service
isn't enough. A raw HCI capture with Apple's PacketLogger (with the Bluetooth logging
profile installed) showed what it checks. The remote, in pairing mode (Back + Home):

| Packet | Bytes |
|---|---|
| Advertisement | `02 01 05` flags = **LE Limited Discoverable** · `03 03 12 18` HID service · `06 09 "BT38B"` name · `05 FF 5D 00 55 66` manufacturer data |
| Scan response | `03 03 12 18` HID · `03 19 80 01` **appearance 0x0180 (remote control)** · `06 09 "BT38B"` · `05 FF 5D 00 55 88` |

It uses a public address with the Telink OUI (`A4:C1:38`). Stock Flipper firmware always
advertises as General Discoverable and can't set the scan response, hence the firmware patch
(`furi_hal_bt_set_custom_adv_data`). With byte-identical packets the TV connects and pairs.

Keys the TV accepts over Bluetooth (standard HID usages):

| Key | Usage | Notes |
|---|---|---|
| Up / Down / Left / Right | keyboard `52` / `51` / `50` / `4F` | |
| OK | keyboard `28` (Enter) | |
| Back / Home | consumer `224` / `223` | |
| Vol + / Vol − / Mute | consumer `E9` / `EA` / `E2` | |
| Menu | consumer `40` | Opens Telly Settings, same as Menu ≡ |
| Sleep | consumer `32` | Not on the remote: both screens and mood lighting off |

Power, Input, the red Telly button, Screen ▲/▼ and A/B still go by IR. Their Bluetooth
codes could be found by pairing a real remote to a computer and capturing its HID reports.

### Macros

Mood lighting and shortcuts replay menu navigation. When every step has a Bluetooth code,
the server sends the whole sequence as one `telly seq` command and the Flipper plays it with
10 ms key presses and no pauses. Shortcuts that start with Input (IR) use short pauses while
the lower screen redraws. `BLE_DELAY_SCALE` in `server.py` adds pauses back if a TV misses
steps.

## Repo layout

| Path | What |
|---|---|
| `server.py` | Web server and Flipper USB client (button routing, macros, shortcuts) |
| `web/index.html` | The web remote |
| `flipper/telly_remote/` | Flipper app: BLE HID remote + `telly` CLI command (keys, sequences, IR) |
| `flipper/firmware/` | Firmware 1.5.1 patch for custom advertising data, with build/flash steps |
| `tools/flipcli.py` | Run Flipper CLI commands from the computer |
| `tools/ir_capture.py` | Record IR codes with the Flipper |
| `tools/ble_scan.swift` | BLE advertisement scanner (CoreBluetooth) |
| `tools/deploy_flipper.sh` | Rebuild and relaunch the Flipper app |

## Gotchas

- Firmware 1.5.1's built-in `ir tx` refuses to run while any app is open, so the app has its
  own `telly ir` command and the server uses it when the app is running.
- In 1.5.1, `ble_profile_hid_*_press/release` return `true` on **failure**.
- Don't kill a running Flipper CLI command mid-way on 1.5.1; the CLI can hang until USB is
  replugged.
- macOS: Python needs Local Network permission to reach LAN devices, and command-line
  Bluetooth tools need to run from an app bundle (see `tools/ble_scan.Info.plist`).
