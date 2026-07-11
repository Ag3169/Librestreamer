"""Intelligent load balancing engine.

Scores backends based on resource usage and priority, picks the best one
for streaming.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..backends.clients import BackendClient

log = logging.getLogger("librestreamer.balancer")


@dataclass
class BackendState:
    name: str
    kind: str
    healthy: bool = True
    score: float = 0.0
    priority: int = 1
    max_streams: int = 4
    active_streams: int = 0
    snapshot: dict[str, Any] = field(default_factory=dict)
    last_seen: float = 0.0
    cpu_usage_pct: float = -1
    memory_usage_pct: float = -1
    gpu_usage_pct: float = -1
    gpu_name: str = ""
    memory_total_bytes: int = 0
    memory_used_bytes: int = 0
    disk_total_bytes: int = 0
    disk_used_bytes: int = 0
    disk_usage_pct: float = -1
    server_name: str = ""
    server_id: str = ""


class LoadBalancer:
    def __init__(self, auto_load_balance: bool = True, failover_timeout: float = 30.0):
        self.auto_load_balance = auto_load_balance
        self.failover_timeout = failover_timeout
        self._states: dict[str, BackendState] = {}

    def update_state(self, name: str, kind: str, snapshot: dict[str, Any] | None,
                     priority: int = 1, max_streams: int = 4) -> None:
        now = time.time()
        if snapshot is None:
            if name in self._states:
                self._states[name].healthy = False
                self._states[name].last_seen = now
            return
        cpu = float(snapshot.get("cpu_usage_pct", -1) or -1)
        mem = float(snapshot.get("memory_usage_pct", -1) or -1)
        gpu = float(snapshot.get("gpu_usage_pct", -1) or -1)
        streams = int(snapshot.get("active_streams", 0) or 0)
        load = 0.5 * max(cpu, 0) + 0.3 * max(mem, 0) + 0.2 * max(gpu, 0)
        if max_streams > 0 and streams >= max_streams:
            load += 1000
        score = load - 10.0 * max(priority, 0)
        s = BackendState(
            name=name, kind=kind, healthy=True, score=score,
            priority=priority, max_streams=max_streams,
            active_streams=streams, snapshot=snapshot, last_seen=now,
        )
        s.cpu_usage_pct = cpu
        s.memory_usage_pct = mem
        s.gpu_usage_pct = gpu
        s.gpu_name = str(snapshot.get("gpu_name", ""))
        s.memory_total_bytes = int(snapshot.get("memory_total_bytes", 0) or 0)
        s.memory_used_bytes = int(snapshot.get("memory_used_bytes", 0) or 0)
        s.disk_total_bytes = int(snapshot.get("disk_total_bytes", 0) or 0)
        s.disk_used_bytes = int(snapshot.get("disk_used_bytes", 0) or 0)
        s.disk_usage_pct = float(snapshot.get("disk_usage_pct", -1) or -1)
        s.server_name = str(snapshot.get("server_name", name))
        s.server_id = str(snapshot.get("server_id", ""))
        self._states[name] = s

    def remove_state(self, name: str) -> None:
        self._states.pop(name, None)

    def get_state(self, name: str) -> BackendState | None:
        s = self._states.get(name)
        if s and self.failover_timeout > 0:
            if time.time() - s.last_seen > self.failover_timeout and not s.healthy:
                pass
        return s

    def states(self) -> list[dict[str, Any]]:
        now = time.time()
        out = []
        for s in self._states.values():
            if self.failover_timeout > 0 and now - s.last_seen > self.failover_timeout:
                s.healthy = False
            out.append({
                "name": s.name, "kind": s.kind, "healthy": s.healthy,
                "score": round(s.score, 3), "priority": s.priority,
                "active_streams": s.active_streams,
                "cpu_usage_pct": s.cpu_usage_pct,
                "memory_usage_pct": s.memory_usage_pct,
                "gpu_usage_pct": s.gpu_usage_pct,
                "gpu_name": s.gpu_name,
                "memory_total_bytes": s.memory_total_bytes,
                "memory_used_bytes": s.memory_used_bytes,
                "disk_total_bytes": s.disk_total_bytes,
                "disk_used_bytes": s.disk_used_bytes,
                "disk_usage_pct": s.disk_usage_pct,
                "server_name": s.server_name,
                "server_id": s.server_id,
                "last_seen": s.last_seen,
                "last_seen_ago": round(now - s.last_seen, 1) if s.last_seen else -1,
            })
        return out

    def select_backend(self, candidates: list[str]) -> str | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        now = time.time()
        if not self.auto_load_balance:
            best = None
            best_pri = 999
            for name in candidates:
                s = self._states.get(name)
                if s and s.healthy and (self.failover_timeout == 0 or now - s.last_seen <= self.failover_timeout):
                    if s.priority < best_pri:
                        best = name
                        best_pri = s.priority
            return best or candidates[0]
        best: BackendState | None = None
        for name in candidates:
            s = self._states.get(name)
            if s is None or not s.healthy:
                continue
            if self.failover_timeout > 0 and now - s.last_seen > self.failover_timeout:
                continue
            if best is None or s.score < best.score:
                best = s
        return best.name if best else candidates[0]

    def is_healthy(self, name: str) -> bool:
        s = self._states.get(name)
        if not s:
            return False
        if self.failover_timeout > 0 and time.time() - s.last_seen > self.failover_timeout:
            return False
        return s.healthy
