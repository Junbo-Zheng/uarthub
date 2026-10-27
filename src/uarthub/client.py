# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""uarthub client: connect to a uarthubd server from your terminal.

    uarthub <host> [-p 7000]

Ctrl-C (or EOF) exits; the server and other clients are unaffected.
"""

import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from importlib.metadata import PackageNotFoundError, version

from serial.tools import list_ports

logger = logging.getLogger(__name__)


def _version() -> str:
    try:
        return version("uarthub")
    except PackageNotFoundError:
        from uarthub import __version__

        return __version__


def pick_device(explicit: str | None) -> str:
    """Resolve the serial device: explicit wins, else auto-detect.

    Lists ports via pyserial (cross-platform: /dev/tty* on Linux, COMx on
    Windows). Legacy ttyS* nodes with no description are noise (32 of them on
    a typical desktop) and are treated as absent. With several real ports the
    historical defaults win (ttyACM1 first — the usual watch console), and only
    when none of them matches does the listing ask the user to pick with -u.
    """
    if explicit:
        return explicit
    ports = [(p.device, p.description) for p in list_ports.comports()]
    real = [
        d
        for d, desc in ports
        if not (d.startswith("/dev/ttyS") and desc in ("n/a", ""))
    ]
    if not real:
        raise SystemExit(
            "uarthub: no serial ports found (legacy ttyS* nodes don't count). "
            "Plug the device in, or pass one explicitly with -u <port>."
        )
    if len(real) == 1:
        return real[0]
    for preferred in ("/dev/ttyACM1", "/dev/ttyACM0"):
        if preferred in real:
            logger.info("multiple ports, defaulting to %s", preferred)
            return preferred
    listing = "\n".join(f"  {d}" for d in sorted(real))
    raise SystemExit(
        f"uarthub: multiple serial ports found:\n{listing}\nPick one with -u <port>."
    )


def _complete_uart(**kwargs) -> list[str]:
    """Shell-Tab completion for -u: live list of real serial ports."""
    return [
        d
        for d, desc in ((p.device, p.description) for p in list_ports.comports())
        if not (d.startswith("/dev/ttyS") and desc in ("n/a", ""))
    ]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.DEBUG,
        datefmt="%Y-%m-%d %H:%M:%S",
        format=(
            "%(asctime)-19s.%(msecs)03d %(levelname)-8s "
            "%(filename)s %(lineno)-3d %(process)d %(message)s"
        ),
    )
    p = argparse.ArgumentParser(
        prog="uarthub",
        description=(
            f"uarthub {_version()} — client for a uarthubd server. "
            "Ctrl-C exits; other clients and the server are unaffected."
        ),
    )
    p.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"uarthub {_version()}",
        help="Show the version and exit",
    )
    p.add_argument("host", nargs="?", default="127.0.0.1", help="host running uarthubd")
    p.add_argument(
        "-p", "--port", type=int, default=7000, help="TCP port (default: 7000)"
    )
    p.add_argument(
        "-u",
        "--uart",
        help="serial device to start a server with if none is running "
        "(default: auto-detect; with multiple ports, pick one with -u)",
    ).completer = _complete_uart
    p.add_argument(
        "--baud",
        type=int,
        default=921600,
        help="baud rate for the auto-started server (default: 921600)",
    )
    # Tab completion (uarthub -u <TAB>) when argcomplete is hooked into the
    # shell; no-op otherwise.
    try:
        import argcomplete

        argcomplete.autocomplete(p)
    except ImportError:
        pass
    args = p.parse_args(argv)

    host = args.host
    if host.startswith("/dev/") or host[:3].upper() == "COM":
        # "uarthub /dev/ttyACM3" / "uarthub COM3" — the user meant the serial
        # device, but the positional argument is a host. A host can never look
        # like a device path, so rewrite unconditionally.
        host = "127.0.0.1"
        if not args.uart:
            args.uart = args.host
    # Device resolution is deferred to the auto-start path in _connect: when a
    # server is already running, the client never touches a serial port.
    try:
        _run(host, args.port, args.uart, args.baud)
    except KeyboardInterrupt:
        pass  # the intended way out
    return 0


def _other_openers(device: str) -> list[int]:
    """PIDs of other processes holding `device` open (Linux /proc scan).

    A serial port open without an exclusive lock can be opened again, so a
    plain open() proves nothing — but two readers on one port steal each
    other's bytes, so we refuse to start a server next to an existing opener.
    """
    if not os.path.isdir("/proc"):
        return []  # best-effort detection, Linux-only
    me = str(os.getpid())
    pids = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid == me:
            continue
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:  # not ours / already gone
            continue
        for fd in fds:
            try:
                if os.readlink(f"/proc/{pid}/fd/{fd}") == device:
                    pids.append(int(pid))
                    break
            except OSError:
                continue
    return pids


def _connect(host: str, port: int, device: str | None, baud: int) -> socket.socket:
    try:
        return socket.create_connection((host, port))
    except OSError as e:
        if host not in ("127.0.0.1", "localhost"):
            raise  # can't start a server on a remote host
        logger.debug("no server at %s:%d (%s) -- starting one", host, port, e)
        device = pick_device(device)
        pids = _other_openers(device)
        if pids:
            raise SystemExit(
                f"uarthub: {device} is already open by another process "
                f"(PID {' '.join(map(str, pids))}).\n"
                f"  Two readers on one port tear each other's data — close "
                f"that program first, then run uarthub again.\n"
                f"  (check: lsof {device})"
            )
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uarthub.cli",
                device,
                str(baud),
                "--auto-exit",
                "--port",
                str(port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,  # its serial echo would duplicate ours
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # survive the client that spawned it
        )
        for _ in range(20):  # give the daemon a moment to bind
            time.sleep(0.25)
            try:
                return socket.create_connection((host, port))
            except OSError:
                continue
        raise


def _run(host: str, port: int, device: str | None, baud: int) -> None:
    sock = _connect(host, port, device, baud)

    def _pump() -> None:
        while True:
            try:
                data = sock.recv(4096)
            except OSError:  # main thread closed the socket on exit
                break
            if not data:
                break
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    try:
        if sys.stdin.isatty() and os.name == "posix":
            # Byte-for-byte forwarding so device-side line editing (NSH tab
            # completion, backspace) works. cbreak keeps ISIG so Ctrl-C still
            # raises KeyboardInterrupt here instead of reaching the device.
            import termios
            import tty

            fd = sys.stdin.fileno()
            old_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            try:
                while True:
                    b = sys.stdin.buffer.read(1)
                    if not b:  # EOF
                        break
                    sock.sendall(b)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        elif sys.stdin.isatty():
            # Windows console: msvcrt gives us unbuffered key reads without
            # needing the ReadConsole mode dance.
            import msvcrt

            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):  # arrow/function key prefix
                        msvcrt.getwch()  # discard the code — not typeable on NSH anyway
                        continue
                    if ch == "\x03":  # Ctrl-C
                        raise KeyboardInterrupt
                    sock.sendall(ch.encode("utf-8", "replace"))
                else:
                    time.sleep(0.02)  # kbhit is a poll — don't spin the core
        else:
            # Piped stdin: line-based forwarding, no tty to reconfigure.
            for line in sys.stdin:
                sock.sendall(line.encode("utf-8", "replace"))
    except OSError as e:
        # Server dropped the connection (e.g. its serial write path died);
        # exit cleanly instead of dumping a traceback into the console.
        logger.error("connection lost: %s", e)
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
