from __future__ import annotations

import argparse
import logging
import signal
import threading

from .api import create_server
from .config import Settings
from .db import Database
from .repository import NotificationRepository
from .service import NotificationService
from .worker import start_workers


def main() -> None:
    parser = argparse.ArgumentParser(description="Reliable HTTP notification service")
    parser.add_argument("mode", nargs="?", choices=("api", "worker", "all"), default="all")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = Settings.from_env()
    database = Database(settings.db_path)
    database.initialize()
    repository = NotificationRepository(database)
    stop_event = threading.Event()

    def stop(*_: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    workers = start_workers(repository, settings, stop_event) if args.mode in {"worker", "all"} else []
    server = None
    if args.mode in {"api", "all"}:
        service = NotificationService(repository, settings)
        server = create_server(settings.bind_host, settings.port, service, repository, settings.max_body_bytes + 16_384)
        server.timeout = 0.5
        logging.info("API listening on http://%s:%d", settings.bind_host, settings.port)
        while not stop_event.is_set():
            server.handle_request()
    else:
        stop_event.wait()

    if server:
        server.server_close()
    for thread in workers:
        thread.join(timeout=2)


if __name__ == "__main__":
    main()

