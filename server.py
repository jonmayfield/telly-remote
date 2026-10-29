"""Telly Remote server.

Serves the web remote and relays button presses to a Flipper Zero over USB.
When the Flipper's Telly Remote app is running and paired with the TV, buttons
go over Bluetooth (no line of sight needed); otherwise, and for buttons with no
known Bluetooth code, they go as infrared (NEC extended, address 0xFE02, codes
captured from the real remote).

Run:  python3 server.py   then open http://localhost:8642
Only the Python standard library is needed. Close qFlipper first; it holds
the Flipper's serial port.
"""
import glob
import json
import os
import select
import sys
import termios
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("PORT", 8642))
WEB_DIR = Path(__file__).parent / "web"

# Captured from the real Telly remote (TYRC-1) with the Flipper's IR receiver.
# All buttons use NEC extended protocol, address 0xFE02.
IR_PROTOCOL = "NECext"
IR_ADDRESS = "FE02"
BUTTONS = {
    "power": "FF00",
    "input": "9F60",
    "telly": "A758",
    "back": "E619",
    "screen_up": "A956",
    "home": "A35C",
    "up": "A659",
    "down": "AE51",
    "left": "AB54",
    "right": "EA15",
    "ok": "AA55",
    "a": "9E61",
    "screen_down": "A857",
    "b": "9D62",
    "mute": "BE41",
    "vol_up": "B748",
    "vol_down": "F40B",
    "menu": "DC23",
}

# Bluetooth HID usages sent by the Flipper's Telly Remote app: ("k", keyboard
# usage) or ("c", consumer usage). Verified on the TV: up/down/right, ok,
# back, home, vol_up, menu, sleep. The Telly-only buttons (telly, screen, A/B,
# input, power) have no known HID code yet, so they always use infrared.
BLE_BUTTONS = {
    "up": ("k", "52"),
    "down": ("k", "51"),
    "left": ("k", "50"),
    "right": ("k", "4F"),
    "ok": ("k", "28"),
    "back": ("c", "224"),
    "home": ("c", "223"),
    "vol_up": ("c", "E9"),
    "vol_down": ("c", "EA"),
    "mute": ("c", "E2"),
    # Found with the key explorer: same as the remote's Menu (opens Telly Settings).
    "menu": ("c", "40"),
    # Not on the physical remote. Found with the key explorer: turns off both
    # screens and the mood lighting (Power only turns off the main screen).
    "sleep": ("c", "32"),
}


class Flipper:
    """Minimal Flipper Zero CLI client over the USB CDC serial port."""

    def __init__(self):
        self.fd = None
        self.lock = threading.Lock()

    def _open(self):
        ports = glob.glob("/dev/cu.usbmodemflip_*")
        if not ports:
            raise RuntimeError("Flipper not found on USB")
        fd = os.open(ports[0], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] |= termios.CLOCAL | termios.CREAD
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        self.fd = fd
        os.write(fd, b"\r")
        self._read_prompt(3)

    def _read_prompt(self, timeout):
        buf, end = b"", time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(self.fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise OSError("Flipper disconnected")
                buf += chunk
                if buf.rstrip().endswith(b">:"):
                    return buf.decode(errors="replace")
        raise TimeoutError("Flipper did not respond")

    def command(self, cmd, timeout=3):
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.fd is None:
                        self._open()
                    os.write(self.fd, cmd.encode() + b"\r")
                    return self._read_prompt(timeout)
                except (OSError, TimeoutError):
                    if self.fd is not None:
                        os.close(self.fd)
                    self.fd = None
                    if attempt == 2:
                        raise

    def send_ir(self, command_hex):
        # Firmware 1.5.1's `ir tx` refuses to run while any app is open, so when
        # the Telly Remote app is running, send through its own `telly ir`.
        args = f"{IR_PROTOCOL} {IR_ADDRESS} {command_hex}"
        out = self.command(f"telly ir {args}").replace("\r", "")
        if "\nok" in out:
            return
        out = self.command(f"ir tx {args}").replace("\r", "")
        if "Usage" in out or "rror" in out or "cannot be run" in out:
            raise RuntimeError(out.strip().splitlines()[-2] if "\n" in out.strip() else out.strip())

    def send_ble(self, kind, usage_hex):
        """Press a key over Bluetooth. Returns False if the app isn't connected."""
        out = self.command(f"telly {kind} {usage_hex} {BLE_HOLD_MS}")
        return "\nok" in out.replace("\r", "")

    def bt_status(self):
        out = self.command("telly status").replace("\r", "")
        for state in ("connected", "advertising", "off"):
            if f"\n{state}" in out:
                return state
        return "unavailable"  # Telly Remote app not running


flipper = Flipper()


BLE_HOLD_MS = 10  # how long each Bluetooth key is held down
TEXT_MAX = 100
SHIFT = 0x0200  # left-shift modifier, in the high byte the Flipper app expects
_TEXT_KEYS = {" ": 0x2C, "-": 0x2D, ".": 0x37, ",": 0x36, "/": 0x38, "\n": 0x28}


def text_usage(ch):
    """Keyboard usage (with shift bit) for typing one character, or None."""
    if "a" <= ch <= "z":
        return 0x04 + ord(ch) - ord("a")
    if "A" <= ch <= "Z":
        return SHIFT | (0x04 + ord(ch) - ord("A"))
    if "1" <= ch <= "9":
        return 0x1E + ord(ch) - ord("1")
    if ch == "0":
        return 0x27
    return _TEXT_KEYS.get(ch)


def press_button(name):
    """Send a named button, preferring Bluetooth. Returns (transport, code)."""
    if name in BLE_BUTTONS:
        kind, usage = BLE_BUTTONS[name]
        if flipper.send_ble(kind, usage):
            return "bluetooth", f"{kind}:{usage}"
    if name not in BUTTONS:
        raise RuntimeError(f"{name} needs Bluetooth; start the Telly Remote app on the Flipper")
    flipper.send_ir(BUTTONS[name])
    return "ir", BUTTONS[name]

# Mood lighting is only reachable through the TV's on-screen menu, so these
# macros replay the remote presses: Menu opens "Telly Settings" with Mood
# Lighting first; inside it the rows are Enable, Effect, Brightness.
MOOD_EFFECTS = [
    "White", "Blue", "Red", "Green", "Cyan", "Magenta", "Yellow",
    "Orange", "Violet", "Pink", "Candy Cane", "Rainbow",
    "Static Rainbow", "4th of July", "4th of July Static",
]
NAV_DELAY = 0.35     # between d-pad presses
SCREEN_DELAY = 1.2   # after a press that opens or closes a panel
macro_lock = threading.Lock()


def open_mood_lighting():
    """Menu → top of Telly Settings → into Mood Lighting, cursor on Enable."""
    return (
        [("menu", SCREEN_DELAY)]
        + [("up", NAV_DELAY)] * 12
        + [("ok", SCREEN_DELAY)]
        + [("up", NAV_DELAY)] * 3
    )


CLOSE = [("back", SCREEN_DELAY), ("back", SCREEN_DELAY)]


def mood_macro(action, value=None):
    steps = open_mood_lighting()
    if action == "toggle":
        steps += [("ok", SCREEN_DELAY)]
    elif action == "effect":
        index = MOOD_EFFECTS.index(value)
        steps += [("down", NAV_DELAY), ("ok", SCREEN_DELAY)]
        steps += [("up", NAV_DELAY)] * len(MOOD_EFFECTS)
        steps += [("down", NAV_DELAY)] * index
        steps += [("ok", SCREEN_DELAY)]
    elif action == "brightness":
        # value is a signed number of slider steps (negative = dimmer).
        steps += [("down", NAV_DELAY), ("down", NAV_DELAY)]
        key = "right" if value > 0 else "left"
        steps += [(key, 0.15)] * abs(value)
    else:
        raise ValueError("unknown action")
    return steps + CLOSE


# Bluetooth presses arrive reliably, so the IR-sized pauses can shrink; the TV's
# menu animations set the floor. Tune here if a macro starts missing steps.
BLE_DELAY_SCALE = float(os.environ.get("BLE_DELAY_SCALE", 0))


def run_steps(steps, scale=None):
    """Play (button, delay) steps. scale shrinks the delays of Bluetooth presses."""
    scale = BLE_DELAY_SCALE if scale is None else scale
    with macro_lock:
        # Fastest path: hand the whole sequence to the Flipper app in one command,
        # so there's no USB round trip between key presses.
        if all(button in BLE_BUTTONS for button, _ in steps):
            seq = " ".join(
                f"{BLE_BUTTONS[b][0]}{BLE_BUTTONS[b][1]}:{int(d * scale * 1000)}"
                for b, d in steps
            )
            total = sum(d * scale + BLE_HOLD_MS / 1000 for _, d in steps)
            out = flipper.command(f"telly seq {BLE_HOLD_MS} {seq}", timeout=total + 5)
            if "\nok" in out.replace("\r", ""):
                return
        for button, delay in steps:
            via, _ = press_button(button)
            time.sleep(delay * scale if via == "bluetooth" else delay)


# Shortcuts into the lower "smart screen" bar. Input opens it with the cursor
# on the first For You item; Up reaches the tab row. We go via fixed tabs (not
# For You, which may reorder) and count from the first tab and first item.
SMART_TABS = [
    "Home Feed", "For You", "Inputs", "Watch", "Music",
    "Game Room", "Video Calling", "Fitness", "Settings",
]
# Optional local config.json (git-ignored), e.g. {"hdmi_names": ["Living room", "Streaming stick", "Console"]}.
# Names are labels only: shortcuts pick inputs by position in the Inputs tab.
CONFIG_PATH = Path(__file__).parent / "config.json"
CONFIG = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
HDMI_NAMES = CONFIG.get("hdmi_names") or ["HDMI 1", "HDMI 2", "HDMI 3"]

SHORTCUTS = {
    "Inputs": HDMI_NAMES,
    "Watch": ["TV Tuner"],
    "Music": ["LiveOne", "Spotify", "Karaoke", "Streams"],
    "Game Room": ["Remote Control Games", "Motion Controlled Games", "Smart Screen Games"],
    "Video Calling": ["Telly Phone", "Zoom", "Telly Photo Booth"],
    "Fitness": ["Telly Fit"],
    # Same list as the Menu button's "Telly Settings" panel; reached via Menu
    # (all Bluetooth) rather than the lower screen's Settings tab.
    "Settings": [
        "Mood Lighting", "Timers", "Photos", "Network", "Picture", "Sound",
        "HDMI", "Remote Control", "Account", "Accessibility", "Help & Support",
    ],
}
TAB_DELAY = 0.6       # after switching tab, while its items load
SHORTCUT_SCALE = 0.5  # navigation needs a little time for the bar to redraw


def shortcut_macro(tab, item):
    item_index = SHORTCUTS[tab].index(item)
    if tab == "Settings":
        settings = SHORTCUTS["Settings"]
        return (
            [("menu", SCREEN_DELAY)]
            + [("up", NAV_DELAY)] * len(settings)
            + [("down", NAV_DELAY)] * item_index
            + [("ok", SCREEN_DELAY)]
        )
    tab_index = SMART_TABS.index(tab)
    return (
        [("input", SCREEN_DELAY), ("up", SCREEN_DELAY)]
        + [("left", NAV_DELAY)] * len(SMART_TABS)
        + [("right", TAB_DELAY)] * tab_index
        + [("down", SCREEN_DELAY)]
        + [("left", NAV_DELAY)] * 6
        + [("right", NAV_DELAY)] * item_index
        + [("ok", SCREEN_DELAY)]
    )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self):
        # The page changes as the remote evolves; never serve a stale copy.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/"):
            sys.stderr.write("%s\n" % (fmt % args))

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/status":
            try:
                return self._json(200, {"flipper": True, "bluetooth": flipper.bt_status()})
            except Exception as e:
                return self._json(200, {"flipper": False, "error": str(e)})
        if self.path == "/api/buttons":
            return self._json(200, BUTTONS)
        if self.path == "/api/mood/effects":
            return self._json(200, MOOD_EFFECTS)
        if self.path == "/api/shortcuts":
            return self._json(200, SHORTCUTS)
        return super().do_GET()

    def do_POST(self):
        if self.path not in ("/api/press", "/api/mood", "/api/shortcut"):
            return self._json(404, {"error": "not found"})
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 1024)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "bad json"})

        if self.path == "/api/shortcut":
            tab, item = body.get("tab"), body.get("item")
            if tab not in SHORTCUTS or item not in SHORTCUTS[tab]:
                return self._json(400, {"error": "unknown shortcut"})
            if macro_lock.locked():
                return self._json(409, {"error": "another shortcut is running"})
            try:
                # Settings goes via Menu, all Bluetooth, so it can run at full speed.
                scale = BLE_DELAY_SCALE if tab == "Settings" else SHORTCUT_SCALE
                run_steps(shortcut_macro(tab, item), scale)
            except Exception as e:
                return self._json(503, {"error": str(e)})
            return self._json(200, {"ok": True})

        if self.path == "/api/mood":
            action, value = body.get("action"), body.get("value")
            if action == "effect" and value not in MOOD_EFFECTS:
                return self._json(400, {"error": "unknown effect"})
            if action == "brightness" and (not isinstance(value, int) or not -100 <= value <= 100):
                return self._json(400, {"error": "brightness steps must be -100..100"})
            if macro_lock.locked():
                return self._json(409, {"error": "another lighting change is running"})
            try:
                run_steps(mood_macro(action, value))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                return self._json(503, {"error": str(e)})
            return self._json(200, {"ok": True})

        try:
            if "hid" in body:
                # Key explorer: any keyboard ("k") or consumer ("c") usage over Bluetooth.
                hid = body["hid"] if isinstance(body["hid"], dict) else {}
                kind, usage = hid.get("kind"), str(hid.get("usage", "")).strip().upper()
                if kind not in ("k", "c") or not 1 <= len(usage) <= 4 or any(
                    ch not in "0123456789ABCDEF" for ch in usage
                ):
                    return self._json(400, {"error": "hid needs kind k|c and a 1-4 digit hex usage"})
                if not flipper.send_ble(kind, usage):
                    return self._json(409, {"error": "Bluetooth not connected"})
                return self._json(200, {"ok": True, "code": f"{kind}:{usage}", "via": "bluetooth"})
            if "text" in body:
                text = str(body["text"])[:TEXT_MAX]
                usages = [text_usage(ch) for ch in text]
                if None in usages:
                    return self._json(400, {"error": "only letters, digits, space and . , - / are supported"})
                for usage in usages:
                    if not flipper.send_ble("k", f"{usage:X}"):
                        return self._json(409, {"error": "Bluetooth not connected"})
                return self._json(200, {"ok": True, "code": f"{len(text)} chars", "via": "bluetooth"})
            if "button" in body:
                if body["button"] not in BUTTONS and body["button"] not in BLE_BUTTONS:
                    return self._json(400, {"error": "unknown button"})
                via, code = press_button(body["button"])
            elif "code" in body:
                code = str(body["code"]).strip().upper()
                if len(code) != 4 or any(c not in "0123456789ABCDEF" for c in code):
                    return self._json(400, {"error": "code must be 4 hex digits"})
                flipper.send_ir(code)
                via = "ir"
            else:
                return self._json(400, {"error": "button or code required"})
        except Exception as e:
            return self._json(503, {"error": str(e)})
        return self._json(200, {"ok": True, "code": code, "via": via})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Telly Remote on http://localhost:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
