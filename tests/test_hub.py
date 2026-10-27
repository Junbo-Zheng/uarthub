# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""End-to-end tests against a fake serial device (a pty pair)."""

import socket
import time

import pytest

from uarthub import __version__
from uarthub.cli import main


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_and_exits(flag, capsys):
    with pytest.raises(SystemExit) as exc:
        main([flag])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def _recv(sock: socket.socket, expect: bytes, timeout: float = 2.0) -> bytes:
    sock.settimeout(timeout)
    buf = b""
    while expect not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def test_device_output_reaches_all_clients(hub_env):
    c1 = hub_env.connect()
    c2 = hub_env.connect()

    hub_env.device.output(b"hello_from_device\n")

    assert b"hello_from_device" in _recv(c1, b"hello_from_device")
    assert b"hello_from_device" in _recv(c2, b"hello_from_device")
    c1.close()
    c2.close()


def test_any_client_can_send(hub_env):
    c1 = hub_env.connect()
    c2 = hub_env.connect()

    c1.sendall(b"cmd_from_c1\n")
    assert hub_env.device.received() == b"cmd_from_c1\n"

    c2.sendall(b"cmd_from_c2\n")
    assert hub_env.device.received() == b"cmd_from_c2\n"
    c1.close()
    c2.close()


def test_client_leaving_does_not_disturb_others(hub_env):
    c1 = hub_env.connect()
    c2 = hub_env.connect()

    c1.close()
    time.sleep(0.2)

    c2.sendall(b"still_working\n")
    assert hub_env.device.received() == b"still_working\n"

    hub_env.device.output(b"late_output\n")
    assert b"late_output" in _recv(c2, b"late_output")
    c2.close()


def test_auto_exit_after_last_client(hub_env_auto_exit):
    c = hub_env_auto_exit.connect()
    c.close()
    time.sleep(3)  # 2s grace + margin

    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", hub_env_auto_exit.port), timeout=1)


def test_auto_exit_keeps_server_while_client_remains(hub_env_auto_exit):
    keeper = hub_env_auto_exit.connect()
    passerby = hub_env_auto_exit.connect()
    passerby.close()
    time.sleep(3)  # passerby leaving must NOT trigger the exit timer

    keeper.sendall(b"still_here\n")
    assert hub_env_auto_exit.device.received() == b"still_here\n"
    keeper.close()
