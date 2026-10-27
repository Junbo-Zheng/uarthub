# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""End-to-end tests for the uarthub client binary against a live hub."""

import os
import select
import subprocess
import sys
import time

import pytest

from uarthub import __version__
from uarthub.client import main as client_main

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_and_exits(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        client_main([flag])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_dev_path_positional_treated_as_device(monkeypatch, capsys):
    """`uarthub /dev/ttyACM3` rewrites to host=127.0.0.1 device=/dev/ttyACM3."""
    import uarthub.client as client_mod

    seen = {}

    def fake_run(host, port, device, baud):
        seen.update(host=host, port=port, device=device, baud=baud)

    monkeypatch.setattr(client_mod, "_run", fake_run)
    assert client_mod.main(["/dev/ttyACM3"]) == 0
    assert seen["host"] == "127.0.0.1"
    assert seen["device"] == "/dev/ttyACM3"

    # An explicit -u still wins over the positional /dev/ path.
    seen.clear()
    client_mod.main(["/dev/ttyACM3", "-u", "/dev/ttyACM0"])
    assert seen["host"] == "127.0.0.1"  # a device path can never be a host
    assert seen["device"] == "/dev/ttyACM0"


def test_other_openers_detects_external_holder(hub_env):
    """A subprocess holding the device open must be reported by PID."""
    from uarthub.client import _other_openers

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"f = open({hub_env.device.name!r}); import time; time.sleep(30)",
        ],
    )
    try:
        deadline = time.time() + 3
        pids = []
        while holder.pid not in pids:
            assert time.time() < deadline, "holder never showed up in /proc scan"
            pids = _other_openers(hub_env.device.name)
        assert _other_openers("/dev/nonexistent-tty") == []
    finally:
        holder.kill()
        holder.wait()


def test_client_end_to_end(hub_env):
    """client connects, sees device output, sends commands, Ctrl-C exits."""
    env = dict(os.environ, PYTHONPATH=os.path.join(_REPO_ROOT, "src"))
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "uarthub.client",
            "127.0.0.1",
            "-p",
            str(hub_env.port),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        # Probe: a line from the client's stdin reaching the device proves the
        # hub has registered the connection.
        proc.stdin.write(b"probe_line\n")
        proc.stdin.flush()
        deadline = time.time() + 3
        buf = b""
        while b"probe_line" not in buf:
            assert time.time() < deadline, "client did not connect in time"
            r, _, _ = select.select([hub_env.device.master], [], [], 0.1)
            if r:
                buf += os.read(hub_env.device.master, 4096)

        # device -> client: ambient output (no client input precedes it) must
        # arrive — the default receive mode is all-output, not quiet.
        hub_env.device.output(b"ambient_from_device\n")
        out = b""
        deadline = time.time() + 3
        while b"ambient_from_device" not in out:
            assert time.time() < deadline, "client never saw ambient device output"
            r, _, _ = select.select([proc.stdout], [], [], 0.1)
            if r:
                out += os.read(proc.stdout.fileno(), 4096)

        hub_env.device.output(b"reply_from_device\n")
        out = b""
        deadline = time.time() + 3
        while b"reply_from_device" not in out:
            assert time.time() < deadline, "client never saw device output"
            r, _, _ = select.select([proc.stdout], [], [], 0.1)
            if r:
                out += os.read(proc.stdout.fileno(), 4096)

        # Ctrl-C exits cleanly
        proc.send_signal(2)  # SIGINT == terminal Ctrl-C
        proc.wait(timeout=3)
        assert proc.returncode == 0
    finally:
        if proc.poll() is None:
            proc.kill()
