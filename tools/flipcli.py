"""Send commands to the Flipper Zero CLI over USB serial and print the output.

Usage: python3 tools/flipcli.py "info device" ["other command" ...]
"""
import glob, os, sys, termios, time, select

def open_port():
    ports = glob.glob("/dev/cu.usbmodemflip_*")
    if not ports:
        sys.exit("Flipper not found on USB")
    fd = os.open(ports[0], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0; attrs[1] = 0; attrs[3] = 0  # raw iflag/oflag/lflag
    attrs[2] |= termios.CLOCAL | termios.CREAD
    attrs[4] = attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd

def read_until_prompt(fd, timeout):
    buf, end = b"", time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                buf += os.read(fd, 4096)
            except BlockingIOError:
                continue
            if buf.rstrip().endswith(b">:"):
                break
    return buf.decode(errors="replace")

def run(fd, cmd, timeout=5):
    os.write(fd, cmd.encode() + b"\r")
    out = read_until_prompt(fd, timeout)
    return out

if __name__ == "__main__":
    fd = open_port()
    os.write(fd, b"\r")
    read_until_prompt(fd, 3)
    for c in sys.argv[1:]:
        print(f"### {c}\n{run(fd, c)}")
