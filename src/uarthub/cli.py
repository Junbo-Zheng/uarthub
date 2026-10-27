# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Junbo Zheng

import argparse
import logging
from importlib.metadata import PackageNotFoundError, version


def _version() -> str:
    # Installed metadata is the source of truth; fall back to the in-tree
    # __init__ string for zero-install runs (main.py shim, editable checkouts).
    try:
        return version("uarthub")
    except PackageNotFoundError:
        from . import __version__

        return __version__


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
        prog="uarthubd",
        description=f"uarthubd {_version()} — share one serial port over TCP.",
    )
    p.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"uarthubd {_version()}",
        help="Show the version and exit",
    )
    from .client import _complete_uart

    p.add_argument(
        "uart",
        nargs="?",
        help="serial device (default: auto-detect; with multiple ports, "
        "pass one explicitly)",
    ).completer = _complete_uart
    p.add_argument(
        "baud", nargs="?", type=int, default=921600, help="baud rate (default: 921600)"
    )
    p.add_argument(
        "--host", default="0.0.0.0", help="TCP listen address (default: 0.0.0.0)"
    )
    p.add_argument(
        "-p", "--port", type=int, default=7000, help="TCP listen port (default: 7000)"
    )
    p.add_argument(
        "--auto-exit",
        action="store_true",
        help="exit 2s after the last client disconnects "
        "(for servers spawned by the client; manual servers never auto-exit)",
    )
    # Tab completion (uarthubd <TAB>) when argcomplete is hooked into the
    # shell; no-op otherwise. Must precede parse_args per argcomplete docs.
    try:
        import argcomplete

        argcomplete.autocomplete(p)
    except ImportError:
        pass
    args = p.parse_args(argv)

    import asyncio

    from .client import pick_device
    from .hub import UartHub

    hub = UartHub(
        pick_device(args.uart),
        args.baud,
        args.host,
        args.port,
        auto_exit=args.auto_exit,
    )
    try:
        asyncio.run(_serve(hub))
    except KeyboardInterrupt:
        logger = logging.getLogger(__name__)
        logger.info("bye")
    return 0


async def _serve(hub) -> None:
    await hub.start()
    try:
        await hub.run()
    finally:
        await hub.stop()


if __name__ == "__main__":
    raise SystemExit(main())
