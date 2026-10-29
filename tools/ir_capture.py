"""Record IR signals with the Flipper for a fixed time, printing each with a timestamp.

Usage: python3 tools/ir_capture.py <seconds> [outfile]
"""
import os, select, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import flipcli as f

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
out = open(sys.argv[2], "w") if len(sys.argv) > 2 else None
fd = f.open_port()
os.write(fd, b"\r"); f.read_until_prompt(fd, 3)
os.write(fd, b"ir rx\r")
start, buf = time.time(), b""
while time.time() - start < secs:
    r, _, _ = select.select([fd], [], [], 0.1)
    if not r:
        continue
    buf += os.read(fd, 4096)
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        s = line.decode(errors="replace").strip()
        if s and not s.startswith(("ir rx", "Receiving", "Press Ctrl")):
            msg = f"{time.time() - start:6.2f} {s}"
            print(msg, flush=True)
            if out:
                out.write(msg + "\n"); out.flush()
os.write(fd, b"\x03")
f.read_until_prompt(fd, 3)
