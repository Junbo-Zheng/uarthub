# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""Shared fixtures: a pty-backed fake device and a live hub on a thread."""

import asyncio
import os
import pty
import select
import socket
import threading
import time

import pytest

from uarthub.hub import UartHub


class FakeDevice:
    """A pty pair standing in for the serial device: write to master = device
    output, read from master = what the device received from clients."""

    def __init__(self):
        self.master, slave = pty.openpty()
        self.name = os.ttyname(slave)

    def output(self, data: bytes) -> None:
        os.write(self.master, data)

    def received(self, timeout: float = 2.0) -> bytes:
        deadline = time.time() + timeout
        buf = b""
        while time.time() < deadline:
            r, _, _ = select.select([self.master], [], [], 0.1)
            if r:
                buf += os.read(self.master, 4096)
                if buf:
                    return buf
        return buf


class HubFixture:
    def __init__(self, device: FakeDevice, port: int):
        self.device = device
        self.port = port
        self._next_probe = 0

    def connect(self, timeout: float = 3.0) -> socket.socket:
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        # A finished TCP connect does NOT mean the server handler has run yet
        # (accept is async) — sending a probe and waiting for it to reach the
        # device proves the client is registered with the hub before the test
        # starts asserting on broadcasts.
        marker = f"<probe-{self._next_probe}>".encode()
        self._next_probe += 1
        sock.sendall(marker)
        deadline = time.time() + timeout
        seen = b""
        while marker not in seen:
            if time.time() > deadline:
                raise TimeoutError("hub did not register the client in time")
            r, _, _ = select.select([self.device.master], [], [], 0.1)
            if r:
                seen += os.read(self.device.master, 4096)
        return sock


def _hub_fixture(auto_exit: bool):
    device = FakeDevice()
    port = _free_port()
    started = threading.Event()
    loop = asyncio.new_event_loop()
    main_task: list = []

    async def _main():
        hub = UartHub(device.name, 921600, "127.0.0.1", port, auto_exit=auto_exit)
        await hub.start()
        started.set()
        try:
            await hub.run()
        except asyncio.CancelledError:
            await hub.stop()
            raise

    def _thread_target():
        asyncio.set_event_loop(loop)
        task = loop.create_task(_main())
        task.add_done_callback(lambda _: loop.stop())
        main_task.append(task)
        loop.run_forever()

    thread = threading.Thread(target=_thread_target, daemon=True)
    thread.start()
    assert started.wait(5), "hub failed to start"

    yield HubFixture(device, port)

    loop.call_soon_threadsafe(main_task[0].cancel)
    thread.join(5)
    loop.close()
    os.close(device.master)


@pytest.fixture
def hub_env():
    yield from _hub_fixture(auto_exit=False)


@pytest.fixture
def hub_env_auto_exit():
    yield from _hub_fixture(auto_exit=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
