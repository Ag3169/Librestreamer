"""Background metrics polling service.

Polls each backend every N seconds, updates the load balancer and stores
metrics history in the database.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from ..backends.clients import BackendClient
from ..balancer.engine import LoadBalancer
import sqlite3

log = logging.getLogger("librestreamer.monitor")


class MetricsPoller:
    def __init__(self, clients: dict[str, BackendClient], balancer: LoadBalancer,
                 conn: sqlite3.Connection, interval: float = 5.0):
        self.clients = clients
        self.balancer = balancer
        self.conn = conn
        self.interval = max(1.0, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="metrics-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def poll_once(self) -> None:
        from ..db import database as db
        for name, client in self.clients.items():
            info = client.info
            snap = client.fetch_metrics()
            self.balancer.update_state(
                name, client.kind, snap,
                priority=info.priority, max_streams=info.max_streams,
            )
            if snap:
                cpu = float(snap.get("cpu_usage_pct", 0) or 0)
                mem = float(snap.get("memory_usage_pct", 0) or 0)
                gpu = float(snap.get("gpu_usage_pct", 0) or 0)
                streams = int(snap.get("active_streams", 0) or 0)
                try:
                    db.add_metrics(self.conn, name, cpu, mem, gpu, streams)
                except Exception as e:
                    log.debug("metrics history error: %s", e)

    def add_client(self, client: BackendClient) -> None:
        self.clients[client.name] = client

    def remove_client(self, name: str) -> None:
        self.clients.pop(name, None)
