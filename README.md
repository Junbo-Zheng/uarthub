[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

# uarthub

Share one serial port with multiple people over TCP — pure Python, zero config.

A serial device can only be opened by one process at a time, which makes collaborative debugging
awkward: whoever grabbed the port first owns it, and everyone else waits. `uarthub` solves this
by owning the device once and bridging it to any number of TCP clients. Everyone sees the same
serial output, anyone can type commands, and clients join
and leave freely without disturbing each other.

![uarthub architecture](https://raw.githubusercontent.com/Junbo-Zheng/uarthub/main/assets/architecture.svg)

Cross-platform: Linux, Windows (`COM*`), and macOS (`/dev/tty.usbmodem*`) all work.
Serial device path is whatever your system exposes — `/dev/ttyACM*`, `/dev/ttyUSB*`,
`/dev/ttyS*`, a pty, anything pyserial can open. `uarthubd` is the only process that
holds the device; clients talk to it over TCP and never touch the port directly.

## Features

- **Multi-client broadcast** — serial output goes to every client (and to
  the host's own stdout)
- **Everyone can send** — input from any client is forwarded verbatim to the device
- **Join/leave anytime** — a client disconnecting (Ctrl-C in `uarthub`, the intended way)
  disturbs the others or the device connection
- **Pure Python, zero config** — a single command, the only dependencies are
  [pyserial](https://pypi.org/project/pyserial/) and
  [argcomplete](https://pypi.org/project/argcomplete/) (shell Tab completion
  for `-u`)

## Install

```bash
pip install uarthub          # or: pipx install uarthub
```

Or run straight from a checkout, no install needed:

```bash
./main.py --help
```

## Usage

The one-command flow — just run `uarthub` on the machine with the device attached; it starts a
background daemon itself if none is running, and that daemon exits by itself once every client
has disconnected:

```bash
uarthub                      # auto-detects the serial port, auto-starts a server if needed
uarthub /dev/ttyACM3         # shorthand: a /dev/ path as the positional arg
uarthub COM3                 # same shorthand on Windows
uarthub -u /dev/ttyACM0 --baud 115200        # which device the server should open
uarthub -u /dev/ttyUSB0                      # any serial path works (ttyACM* / ttyUSB* / COM*)
```

An explicit daemon never exits on its own and is the right choice for a shared lab machine:

```bash
uarthubd /dev/ttyACM1 921600 --port 7000      # manual server, stays up
uarthubd /dev/ttyACM1 921600 --auto-exit      # exits 2s after the last client leaves
```

Anyone — on the same machine or over the network — connects from their own terminal:

```bash
uarthub <hub-host>          # or: uarthub <hub-host> -p 7000
```

Daemon arguments:

| Argument | Default | Description |
|----------|---------|-------------|
| `uart` | auto-detect | serial device path (multiple ports: `ttyACM1`/`ttyACM0` preferred, else pass one explicitly) |
| `baud`   | `921600` | baud rate |
| `--host` | `0.0.0.0` | TCP listen address |
| `-p, --port` | `7000` | TCP listen port |

Connected clients immediately start receiving serial output and can type commands. **Ctrl-C exits the client**;
the daemon and other clients are unaffected. Multiple people can be connected at the same time;
when two people type simultaneously the bytes simply interleave, so agree on who drives.

`telnet <hub-host> 7000` or `nc <hub-host> 7000` work too — `uarthub` is just a client that
exits the way you expect.

> [!NOTE]
> The hub process is the *only* process that opens the device. Stop any existing terminal or
> logger (e.g. `screen`/`minicom`) that holds the port before starting `uarthub`.

## Development

```bash
pip install -e ".[dev]"   # pytest, ruff, build, twine
pytest                    # runs from src/ via pythonpath, no install needed
ruff check src tests main.py
```

The test suite exercises the full broadcast/send/disconnect behavior against a pty-backed fake
device — no real hardware required.

## How it works

`uarthub` opens the serial device with pyserial and runs a small asyncio server. Serial output
is offered to every connected client; input from any client is written to the device. One owner,
N observers.

## License

This project is licensed under the Apache License, Version 2.0.

See
[LICENSE](LICENSE) or <https://www.apache.org/licenses/LICENSE-2.0> for the
full text.
