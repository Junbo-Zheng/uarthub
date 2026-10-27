# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

"""Core multiplexer: one serial device, N TCP clients."""

import asyncio
import logging
import sys

import serial

logger = logging.getLogger(__name__)


class UartHub:
    """Owns the serial device; bridges it to any number of TCP clients.

    - Serial output is broadcast to every client (and the host's stdout).
    - Input from any client is forwarded verbatim to the serial device.
    - Clients may join and leave at any time without disturbing the others.
    """

    def __init__(
        self,
        device: str,
        baud: int,
        host: str = "0.0.0.0",
        tcp_port: int = 7000,
        auto_exit: bool = False,
    ):
        self.device = device
        self.baud = baud
        self.host = host
        self.tcp_port = tcp_port
        self.auto_exit = auto_exit
        self.ser = serial.Serial(
            # Bounded timeout so the reader thread wakes up regularly and
            # notices a closed port; timeout=None blocks shutdown forever.
            device,
            baud,
            timeout=0.5,
        )
        self.clients: set[asyncio.StreamWriter] = set()
        self._server: asyncio.AbstractServer | None = None
        self._had_client = False
        self._exit_timer: asyncio.Task | None = None
        self._exit_event = asyncio.Event()

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.tcp_port
        )
        logger.info("opened %s @ %d", self.device, self.baud)
        logger.info(
            "listening on %s:%d -- clients: telnet %s %d",
            self.host,
            self.tcp_port,
            self.host,
            self.tcp_port,
        )

    async def run(self) -> None:
        """Serve until cancelled (or until the last client leaves in auto_exit)."""
        assert self._server is not None, "call start() first"
        async with self._server:
            serve_task = asyncio.ensure_future(self._server.serve_forever())
            serial_task = asyncio.ensure_future(self._serial_reader())
            jobs = {serve_task, serial_task}
            if self.auto_exit:
                jobs.add(asyncio.ensure_future(self._exit_event.wait()))
            # FIRST_COMPLETED, not gather: if the serial reader dies (device
            # pulled, driver error) the server must shut down, not linger as
            # a zombie that accepts clients but never relays serial data.
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            for task in jobs:
                task.cancel()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self.ser.close()

    async def _serial_reader(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, self.ser.read, 1)
                if data and self.ser.in_waiting:
                    data += self.ser.read(self.ser.in_waiting)
            except serial.SerialException as e:
                # pyserial raises this both on real disconnects and when
                # another process without an exclusive lock consumed the
                # byte; either way relaying is broken, so say why and stop.
                logger.error(
                    "serial reader failed: %s — device disconnected, or "
                    "another process is reading %s concurrently "
                    "(check: lsof %s)",
                    e,
                    self.device,
                    self.device,
                )
                return
            except Exception:
                logger.exception("serial reader crashed")
                return
            if data:
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
                self._broadcast(data)

    def _broadcast(self, data: bytes) -> None:
        for writer in self.clients:
            if writer.is_closing():
                continue
            writer.write(data)
            asyncio.ensure_future(writer.drain())

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        addr = writer.get_extra_info("peername")
        self._had_client = True
        if self._exit_timer is not None:
            self._exit_timer.cancel()
            self._exit_timer = None
        self.clients.add(writer)
        logger.info("client joined: %s (now %d)", addr, len(self.clients))
        try:
            writer.write(b"[uarthub] connected\r\n")
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                self.ser.write(data)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.exception("client %s input loop crashed", addr)
        finally:
            self.clients.discard(writer)
            writer.close()
            logger.info("client left: %s (now %d)", addr, len(self.clients))
            if self.auto_exit and not self.clients and self._had_client:
                self._exit_timer = asyncio.ensure_future(self._schedule_exit())

    async def _schedule_exit(self) -> None:
        # Grace window: a new client may be connecting right as the last one
        # leaves; give it a moment before shutting the whole server down.
        await asyncio.sleep(2)
        if not self.clients:
            logger.info("last client left, exiting (auto_exit)")
            # serve_forever() never returns on its own; closing the server is
            # what lets run()'s gather finish and the process actually exit.
            self._server.close()
            self.ser.close()  # unblocks the reader thread so the loop can end
            self._exit_event.set()
