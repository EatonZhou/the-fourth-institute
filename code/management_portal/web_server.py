"""Portal Web process: uvicorn serving ``management_portal.asgi:app``.

An open page keeps its event stream (``/api/events``) open.  uvicorn's
graceful shutdown stops accepting connections and then waits for every open
response, so a stopping Web would wait for those streams until the shutdown
timeout while no request can reach it.  This server ends the streams as soon
as shutdown begins and pages reconnect to the next Web process; other
requests still get ``--timeout-graceful-shutdown`` seconds to finish.
"""
from __future__ import annotations

import argparse
import asyncio

import uvicorn

APP = "management_portal.asgi:app"
GRACEFUL_SHUTDOWN_SECONDS = 15


class PortalWebServer(uvicorn.Server):
    event_loop: asyncio.AbstractEventLoop | None = None

    async def startup(self, sockets=None) -> None:
        self.event_loop = asyncio.get_running_loop()
        await super().startup(sockets=sockets)

    def handle_exit(self, sig, frame) -> None:
        super().handle_exit(sig, frame)
        if self.event_loop is not None and not self.event_loop.is_closed():
            from . import asgi
            self.event_loop.call_soon_threadsafe(asgi.end_realtime_streams, asgi.app.state)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="management_portal.web_server", description="Run the Portal Web process.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--timeout-graceful-shutdown", type=int, default=GRACEFUL_SHUTDOWN_SECONDS)
    args = parser.parse_args(argv)
    PortalWebServer(uvicorn.Config(APP, host=args.host, port=args.port, access_log=False,
                                   timeout_graceful_shutdown=args.timeout_graceful_shutdown)).run()


if __name__ == "__main__":
    main()
