from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from typing import Any

from dotenv import load_dotenv
import psutil
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route
import uvicorn

from meridian_mcp.store import DEFAULT_ANALYTICS_DAYS, MeridianStore


REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

MANAGED_SERVICE_HOST = os.getenv("MERIDIAN_MANAGED_HOST", "127.0.0.1")
MANAGED_SERVICE_PORT = int(os.getenv("MERIDIAN_MANAGED_PORT", "8000"))
DASHBOARD_PORT = int(os.getenv("MERIDIAN_DASHBOARD_PORT", "8765"))
DASHBOARD_IDLE_TIMEOUT_SECONDS = int(os.getenv("MERIDIAN_DASHBOARD_IDLE_TIMEOUT_SECONDS", "20"))
DASHBOARD_BUILD = datetime.fromtimestamp(Path(__file__).stat().st_mtime, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _format_exception(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


@dataclass
class ServiceStatus:
    managed: bool
    running: bool
    reachable: bool
    host: str
    port: int
    pid: int | None
    started_at: str | None
    log_path: str
    service_url: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "managed": self.managed,
            "running": self.running,
            "reachable": self.reachable,
            "host": self.host,
            "port": self.port,
            "pid": self.pid,
            "started_at": self.started_at,
            "log_path": self.log_path,
            "service_url": self.service_url,
            "detail": self.detail,
        }


class MeridianServiceManager:
    def __init__(self, repo_root: Path, host: str, port: int) -> None:
        self.repo_root = repo_root
        self.host = host
        self.port = port
        self.state_dir = self.repo_root / ".meridian"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file = self.state_dir / "managed-service.json"
        self.log_file = self.state_dir / "managed-service.log"

    def _read_pid_record(self) -> dict[str, Any] | None:
        if not self.pid_file.exists():
            return None
        try:
            return json.loads(self.pid_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_pid_record(self, *, pid: int, started_at: str) -> None:
        self.pid_file.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "host": self.host,
                    "port": self.port,
                    "started_at": started_at,
                    "log_path": str(self.log_file),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _clear_pid_record(self) -> None:
        if self.pid_file.exists():
            self.pid_file.unlink()

    def _port_reachable(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            return sock.connect_ex((self.host, self.port)) == 0

    def _port_listener_pid(self) -> int | None:
        for connection in psutil.net_connections(kind="inet"):
            if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                continue
            if connection.laddr.ip == self.host and connection.laddr.port == self.port:
                return connection.pid
        return None

    def _managed_process(self, pid: int | None) -> psutil.Process | None:
        if pid is None:
            return None
        try:
            process = psutil.Process(pid)
        except psutil.Error:
            return None
        try:
            command_line = " ".join(process.cmdline())
        except psutil.Error:
            return None
        if "meridian_mcp.server" not in command_line:
            return None
        return process

    def status(self) -> ServiceStatus:
        record = self._read_pid_record()
        managed_pid = int(record["pid"]) if record and record.get("pid") else None
        managed_process = self._managed_process(managed_pid)
        reachable = self._port_reachable()
        listener_pid = self._port_listener_pid()
        managed = managed_process is not None and managed_process.is_running()
        running = managed and listener_pid == managed_pid and reachable

        detail = "Managed Meridian service is stopped."
        if running:
            detail = "Managed Meridian service is running."
        elif reachable and listener_pid is not None and listener_pid != managed_pid:
            detail = "Port is in use by another process. Dashboard controls are unavailable until that process stops."
        elif managed and not reachable:
            detail = "Managed process exists but is not responding on the expected port yet."

        if not managed and not reachable and record is not None:
            self._clear_pid_record()

        return ServiceStatus(
            managed=managed,
            running=running,
            reachable=reachable,
            host=self.host,
            port=self.port,
            pid=managed_pid if managed else listener_pid,
            started_at=record.get("started_at") if record else None,
            log_path=str(self.log_file),
            service_url=f"http://{self.host}:{self.port}/mcp",
            detail=detail,
        )

    def start(self) -> ServiceStatus:
        status = self.status()
        if status.running:
            return status
        if status.reachable and not status.managed:
            raise RuntimeError(status.detail)

        command = [
            sys.executable,
            "-m",
            "meridian_mcp.server",
            "--transport",
            "streamable-http",
            "--host",
            self.host,
            "--port",
            str(self.port),
        ]
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = self.log_file.open("ab")
        popen_kwargs: dict[str, Any] = {
            "cwd": str(self.repo_root),
            "stdin": subprocess.DEVNULL,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(command, **popen_kwargs)
        finally:
            log_handle.close()

        self._write_pid_record(pid=process.pid, started_at=_utc_now())
        deadline = time.time() + 12.0
        while time.time() < deadline:
            current = self.status()
            if current.running:
                return current
            time.sleep(0.25)
        return self.status()

    def stop(self) -> ServiceStatus:
        status = self.status()
        if not status.managed:
            if not status.reachable:
                self._clear_pid_record()
            return self.status()

        process = self._managed_process(status.pid)
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=8)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except psutil.Error:
                pass

        deadline = time.time() + 6.0
        while time.time() < deadline:
            current = self.status()
            if not current.running and not current.managed:
                self._clear_pid_record()
                break
            time.sleep(0.25)

        self._clear_pid_record()
        return self.status()


class DashboardSessionTracker:
    def __init__(self, idle_timeout_seconds: int) -> None:
        self.idle_timeout_seconds = idle_timeout_seconds
        self._lock = threading.Lock()
        self._clients: dict[str, float] = {}
        self._started_at = time.time()
        self._shutdown_started = False

    def heartbeat(self, client_id: str) -> None:
        now = time.time()
        with self._lock:
            self._clients[client_id] = now
            self._prune_locked(now)

    def unregister(self, client_id: str) -> None:
        with self._lock:
            self._clients.pop(client_id, None)

    def active_clients(self) -> int:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            return len(self._clients)

    def should_shutdown(self) -> bool:
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            if self._clients:
                return False
            if now - self._started_at < self.idle_timeout_seconds:
                return False
            if self._shutdown_started:
                return False
            self._shutdown_started = True
            return True

    def _prune_locked(self, now: float) -> None:
        stale_clients = [
            client_id
            for client_id, last_seen in self._clients.items()
            if now - last_seen > self.idle_timeout_seconds
        ]
        for client_id in stale_clients:
            self._clients.pop(client_id, None)


class DashboardLifetimeManager:
    def __init__(self, tracker: DashboardSessionTracker) -> None:
        self.tracker = tracker
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._watchdog, name="meridian-dashboard-watchdog", daemon=True)
        self._thread.start()

    def _watchdog(self) -> None:
        while True:
            time.sleep(2.0)
            if self.tracker.should_shutdown():
                os._exit(0)


store = MeridianStore(repo_root=REPO_ROOT, data_root=REPO_ROOT / ".meridian")
manager = MeridianServiceManager(REPO_ROOT, MANAGED_SERVICE_HOST, MANAGED_SERVICE_PORT)
session_tracker = DashboardSessionTracker(DASHBOARD_IDLE_TIMEOUT_SECONDS)
lifetime_manager = DashboardLifetimeManager(session_tracker)


def _dashboard_payload() -> dict[str, Any]:
    snapshot = store.dashboard_snapshot(days=DEFAULT_ANALYTICS_DAYS)
    return {
        "service": manager.status().to_dict(),
        "dashboard": {
            "pid": os.getpid(),
            "url": f"http://127.0.0.1:{DASHBOARD_PORT}",
            "generated_at": _utc_now(),
            "build": DASHBOARD_BUILD,
            "active_clients": session_tracker.active_clients(),
            "idle_timeout_seconds": DASHBOARD_IDLE_TIMEOUT_SECONDS,
        },
        "snapshot": snapshot,
    }


async def homepage(_: Request) -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


async def api_status(_: Request) -> JSONResponse:
    return JSONResponse(_dashboard_payload())


async def api_start(_: Request) -> JSONResponse:
    try:
        service = manager.start().to_dict()
        return JSONResponse({"service": service})
    except Exception as exc:
        return JSONResponse({"error": _format_exception(exc), "service": manager.status().to_dict()}, status_code=409)


async def api_stop(_: Request) -> JSONResponse:
    try:
        service = manager.stop().to_dict()
        return JSONResponse({"service": service})
    except Exception as exc:
        return JSONResponse({"error": _format_exception(exc), "service": manager.status().to_dict()}, status_code=500)


async def api_heartbeat(request: Request) -> JSONResponse:
    payload = await request.json()
    client_id = str(payload.get("client_id", "")).strip()
    if not client_id:
        return JSONResponse({"error": "client_id is required"}, status_code=400)
    session_tracker.heartbeat(client_id)
    return JSONResponse({"ok": True, "active_clients": session_tracker.active_clients()})


async def api_unregister(request: Request) -> JSONResponse:
    payload = await request.json()
    client_id = str(payload.get("client_id", "")).strip()
    if client_id:
        session_tracker.unregister(client_id)
    return JSONResponse({"ok": True, "active_clients": session_tracker.active_clients()})


app = Starlette(
    routes=[
        Route("/", homepage),
        Route("/api/status", api_status),
        Route("/api/service/start", api_start, methods=["POST"]),
        Route("/api/service/stop", api_stop, methods=["POST"]),
        Route("/api/session/heartbeat", api_heartbeat, methods=["POST"]),
        Route("/api/session/unregister", api_unregister, methods=["POST"]),
    ]
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Meridian dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT)
    args = parser.parse_args()
    lifetime_manager.start()
    uvicorn.run(app, host=args.host, port=args.port)


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Meridian Control</title>
  <style>
    :root {
      --bg: #f4efe4;
      --panel: rgba(255, 251, 242, 0.84);
      --border: rgba(77, 60, 35, 0.14);
      --ink: #1f1a17;
      --muted: #675a4a;
      --accent: #1d6b56;
      --warn: #a54b2a;
      --shadow: 0 18px 40px rgba(52, 39, 25, 0.10);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(29, 107, 86, 0.14), transparent 32%),
        radial-gradient(circle at top right, rgba(165, 75, 42, 0.14), transparent 28%),
        linear-gradient(180deg, #f8f3ea 0%, var(--bg) 100%);
      min-height: 100vh;
      overflow: hidden;
    }
    .shell {
      max-width: 1500px;
      margin: 0 auto;
      padding: 12px;
      height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
      gap: 12px;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.08fr 0.92fr;
      gap: 12px;
    }
    .hero-main {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: start;
    }
    .panel {
      background: var(--panel);
      backdrop-filter: blur(12px);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow);
      padding: 14px 16px;
      min-width: 0;
    }
    .title {
      font-size: clamp(1.45rem, 2.1vw, 2rem);
      line-height: 1;
      margin: 4px 0 8px;
      letter-spacing: -0.04em;
    }
    .subtle, .meta, .empty {
      color: var(--muted);
    }
    .status-line {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 1rem;
      margin-bottom: 4px;
    }
    .dot {
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: #c8bdb0;
      box-shadow: 0 0 0 5px rgba(200, 189, 176, 0.24);
    }
    .dot.live {
      background: var(--accent);
      box-shadow: 0 0 0 5px rgba(29, 107, 86, 0.18);
    }
    .controls {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 10px;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 9px 14px;
      font: inherit;
      cursor: pointer;
      background: var(--ink);
      color: white;
    }
    button.secondary {
      background: #e9dfd0;
      color: var(--ink);
    }
    button:disabled {
      opacity: 0.45;
      cursor: wait;
    }
    .dashboard-body {
      display: grid;
      grid-template-columns: minmax(230px, 0.72fr) minmax(0, 1.96fr) minmax(300px, 1fr);
      grid-template-rows: minmax(0, 1fr) 196px;
      gap: 12px;
      height: 100%;
      min-height: 0;
      align-items: stretch;
    }
    .stats-column {
      display: grid;
      gap: 12px;
      align-content: start;
      min-width: 0;
      grid-column: 1;
      grid-row: 1;
      min-height: 0;
    }
    .card {
      min-height: 78px;
    }
    .category-panel {
      grid-column: 2;
      grid-row: 1;
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }
    .stat {
      font-size: clamp(1.38rem, 2vw, 1.95rem);
      margin: 8px 0 2px;
      letter-spacing: -0.04em;
      line-height: 1;
      word-break: break-word;
    }
    .label {
      text-transform: uppercase;
      letter-spacing: 0.12em;
      font-size: 0.68rem;
      color: var(--muted);
    }
    .mini-list {
      display: grid;
      gap: 0;
      margin-top: 6px;
    }
    .mini-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-top: 1px solid rgba(77, 60, 35, 0.10);
      padding: 8px 0;
      align-items: baseline;
      font-size: 0.94rem;
    }
    .chart {
      margin-top: 0;
      width: 100%;
      height: 146px;
      border-radius: 14px;
      background: linear-gradient(180deg, rgba(255,255,255,0.65), rgba(233,223,208,0.55));
      border: 1px solid rgba(77, 60, 35, 0.10);
      overflow: hidden;
    }
    .usage-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      align-items: stretch;
      margin-top: 8px;
      min-height: 0;
      height: calc(100% - 24px);
    }
    .usage-side {
      display: grid;
      align-content: center;
      gap: 10px;
      min-width: 112px;
    }
    .legend {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 10px;
      margin-top: 0;
      color: var(--muted);
      font-size: 0.85rem;
      flex: 0 0 auto;
      white-space: nowrap;
    }
    .swatch {
      display: inline-block;
      width: 9px;
      height: 9px;
      border-radius: 999px;
      margin-right: 6px;
    }
    .breakdown {
      display: grid;
      gap: 7px;
      margin-top: 8px;
    }
    .bar {
      display: grid;
      grid-template-columns: 84px 1fr 62px;
      gap: 8px;
      align-items: center;
      font-size: 0.92rem;
    }
    .bar-track {
      height: 10px;
      border-radius: 999px;
      background: rgba(103, 90, 74, 0.12);
      overflow: hidden;
    }
    .bar-fill {
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #1d6b56, #82b8a9);
    }
    .sunburst-svg {
      width: 100%;
      height: 100%;
      min-height: 0;
      margin-top: 8px;
      border-radius: 14px;
      background: linear-gradient(180deg, rgba(255,255,255,0.7), rgba(233,223,208,0.4));
      border: 1px solid rgba(77, 60, 35, 0.10);
      overflow: hidden;
    }
    .context-panel {
      min-height: 0;
      grid-column: 3;
      grid-row: 1;
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }
    .context-grid {
      display: grid;
      gap: 10px;
      margin-top: 10px;
      min-height: 0;
      grid-template-rows: minmax(0, 1fr) minmax(72px, auto);
    }
    .context-block {
      border-radius: 14px;
      border: 1px solid rgba(77, 60, 35, 0.10);
      background: rgba(255, 255, 255, 0.46);
      padding: 10px 11px;
      min-width: 0;
      min-height: 0;
      overflow: hidden;
    }
    .context-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
      font-size: 0.86rem;
    }
    .context-badge {
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 0.7rem;
      background: rgba(31, 26, 23, 0.08);
      color: var(--muted);
      white-space: nowrap;
    }
    .context-list {
      display: grid;
      gap: 8px;
      max-height: none;
      min-height: 0;
      overflow: auto;
      padding-right: 4px;
    }
    .context-item {
      display: grid;
      gap: 2px;
      padding-bottom: 8px;
      border-bottom: 1px solid rgba(77, 60, 35, 0.08);
    }
    .context-item:last-child {
      padding-bottom: 0;
      border-bottom: 0;
    }
    .context-title {
      font-size: 0.84rem;
      font-weight: 700;
      line-height: 1.18;
    }
    .context-excerpt {
      font-size: 0.76rem;
      line-height: 1.25;
      color: var(--muted);
    }
    .context-meta {
      font-size: 0.7rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .compact-note {
      font-size: 0.9rem;
      line-height: 1.28;
    }
    .bottom-panel {
      min-height: 0;
      align-self: end;
      height: 100%;
      display: flex;
      flex-direction: column;
    }
    .bottom-row {
      grid-column: 1 / -1;
      grid-row: 2;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      min-width: 0;
      min-height: 0;
    }
    a {
      color: var(--accent);
      text-decoration: none;
    }
    @media (max-width: 1100px) {
      body { overflow: auto; }
      .shell { height: auto; }
      .hero, .dashboard-body { grid-template-columns: 1fr; }
      .dashboard-body { grid-template-rows: auto; }
      .stats-column, .category-panel, .context-panel, .bottom-row {
        grid-column: auto;
        grid-row: auto;
      }
      .bottom-row { grid-template-columns: 1fr; }
      .sunburst-svg { height: 250px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <section class="panel">
        <div class="label">Meridian Control</div>
        <div class="hero-main">
          <div>
            <h1 class="title">Meridian service dashboard</h1>
            <div class="status-line">
              <span id="status-dot" class="dot"></span>
              <strong id="status-text">Checking service...</strong>
            </div>
            <div id="build-stamp" class="subtle compact-note">UI build: loading...</div>
            <div id="status-detail" class="subtle compact-note">Loading current Meridian status.</div>
          </div>
          <div class="controls">
            <button id="start-button">Start</button>
            <button id="stop-button" class="secondary">Stop</button>
            <button id="refresh-button" class="secondary">Refresh</button>
          </div>
        </div>
      </section>
      <section class="panel">
        <div class="label">Service Details</div>
        <div class="mini-list">
          <div class="mini-row"><span>Managed endpoint</span><a id="service-url" href="#" target="_blank" rel="noreferrer">Unavailable</a></div>
          <div class="mini-row"><span>Browser sessions</span><span id="client-count" class="meta">-</span></div>
          <div class="mini-row"><span>Auto-close</span><span id="idle-timeout" class="meta">-</span></div>
          <div class="mini-row"><span>PID</span><span id="service-pid" class="meta">-</span></div>
        </div>
      </section>
    </div>

    <div class="dashboard-body">
      <div class="stats-column">
        <section class="panel card">
          <div class="label">Embedding</div>
          <div id="embedding-backend" class="stat">-</div>
          <div id="embedding-summary" class="subtle">Waiting for Meridian state.</div>
        </section>
        <section class="panel card">
          <div class="label">Database File</div>
          <div id="db-size" class="stat">-</div>
          <div id="db-meta" class="subtle"></div>
        </section>
        <section class="panel card">
          <div class="label">Stored Content</div>
          <div id="stored-bytes" class="stat">-</div>
          <div id="stored-meta" class="subtle"></div>
        </section>
        <section class="panel card">
          <div class="label">Documents</div>
          <div id="document-count" class="stat">-</div>
          <div id="memory-count" class="subtle"></div>
        </section>
      </div>

      <section class="panel category-panel">
        <div class="label">Category Map</div>
        <svg id="category-sunburst" class="sunburst-svg" viewBox="0 0 660 320" preserveAspectRatio="xMidYMid meet"></svg>
      </section>

      <section class="panel context-panel">
        <div class="label">Recent Context</div>
        <div class="context-grid">
          <div class="context-block">
            <div class="context-heading">
              <strong>Semantic memory</strong>
              <span id="semantic-count" class="context-badge">-</span>
            </div>
            <div id="semantic-list" class="context-list"></div>
          </div>
          <div class="context-block">
            <div class="context-heading">
              <strong>Episodic history</strong>
              <span id="episodic-count" class="context-badge">-</span>
            </div>
            <div id="episodic-list" class="context-list"></div>
          </div>
        </div>
      </section>

      <div class="bottom-row">
        <section class="panel bottom-panel">
          <div class="label">Storage Breakdown</div>
          <div id="breakdown" class="breakdown"></div>
        </section>

        <section class="panel bottom-panel">
          <div class="label">Usage Over Time</div>
          <div class="usage-layout">
            <svg id="usage-chart" class="chart" viewBox="0 0 800 220" preserveAspectRatio="none"></svg>
            <div class="usage-side">
              <div id="usage-note" class="subtle"></div>
              <div class="legend">
                <span><span class="swatch" style="background:#1d6b56"></span>Data added</span>
                <span><span class="swatch" style="background:#a54b2a"></span>Data retrieved</span>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>

  <script>
    const startButton = document.getElementById("start-button");
    const stopButton = document.getElementById("stop-button");
    const refreshButton = document.getElementById("refresh-button");
    const clientIdKey = "meridian_dashboard_client_id";
    const clientId = (() => {
      const existing = window.localStorage.getItem(clientIdKey);
      if (existing) return existing;
      const created = (window.crypto && window.crypto.randomUUID)
        ? window.crypto.randomUUID()
        : `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      window.localStorage.setItem(clientIdKey, created);
      return created;
    })();

    function formatBytes(value) {
      if (!Number.isFinite(value)) return "-";
      const units = ["B", "KB", "MB", "GB"];
      let size = value;
      let unit = units[0];
      for (const next of units) {
        unit = next;
        if (size < 1024 || next === units[units.length - 1]) break;
        size /= 1024;
      }
      return `${size.toFixed(size >= 100 || unit === "B" ? 0 : 1)} ${unit}`;
    }

    function cleanEmbeddingLabel(signature) {
      if (!signature) return "-";
      if (signature.startsWith("bge-m3:")) return "BGE-M3";
      if (signature.startsWith("openai:")) {
        const model = signature.slice("openai:".length).split(":")[0];
        return model ? "OpenAI (" + model + ")" : "OpenAI";
      }
      if (signature === "local-hash-v1") return "Local Hash";
      return signature.split(":")[0];
    }

    function setBusy(isBusy) {
      startButton.disabled = isBusy;
      stopButton.disabled = isBusy;
      refreshButton.disabled = isBusy;
    }

    function renderBreakdown(breakdown) {
      const host = document.getElementById("breakdown");
      host.innerHTML = "";
      const maxBytes = Math.max(1, ...breakdown.map(item => item.bytes || 0));
      for (const item of breakdown) {
        const row = document.createElement("div");
        row.className = "bar";
        row.innerHTML = `
          <div>${item.type}</div>
          <div class="bar-track"><div class="bar-fill" style="width:${(item.bytes / maxBytes) * 100}%"></div></div>
          <div class="meta">${formatBytes(item.bytes)}</div>
        `;
        host.appendChild(row);
      }
    }

    function renderUsageChart(usage) {
      const svg = document.getElementById("usage-chart");
      const note = document.getElementById("usage-note");
      const added = usage.daily_added_bytes || {};
      const retrieved = usage.daily_retrieved_bytes || {};
      const days = Array.from(new Set([...Object.keys(added), ...Object.keys(retrieved)])).sort();
      if (!days.length) {
        svg.innerHTML = "";
        note.textContent = usage.retrieval_history_note;
        return;
      }

      const values = days.map(day => ({
        day,
        added: added[day] || 0,
        retrieved: retrieved[day] || 0
      }));
      const maxValue = Math.max(1, ...values.flatMap(item => [item.added, item.retrieved]));
      const width = 800;
      const height = 198;
      const chartLeft = 42;
      const chartBottom = 158;
      const chartTop = 18;
      const chartWidth = width - chartLeft - 20;
      const slot = chartWidth / values.length;
      const barWidth = Math.max(6, slot * 0.32);

      const parts = [
        `<rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>`,
        `<line x1="${chartLeft}" y1="${chartTop}" x2="${chartLeft}" y2="${chartBottom}" stroke="rgba(103,90,74,0.35)"></line>`,
        `<line x1="${chartLeft}" y1="${chartBottom}" x2="${width - 12}" y2="${chartBottom}" stroke="rgba(103,90,74,0.35)"></line>`
      ];

      values.forEach((item, index) => {
        const x = chartLeft + slot * index + slot * 0.18;
        const addedHeight = ((chartBottom - chartTop) * item.added) / maxValue;
        const retrievedHeight = ((chartBottom - chartTop) * item.retrieved) / maxValue;
        const label = item.day.slice(5);
        parts.push(`<rect x="${x}" y="${chartBottom - addedHeight}" width="${barWidth}" height="${addedHeight}" rx="5" fill="#1d6b56"></rect>`);
        parts.push(`<rect x="${x + barWidth + 4}" y="${chartBottom - retrievedHeight}" width="${barWidth}" height="${retrievedHeight}" rx="5" fill="#a54b2a"></rect>`);
        parts.push(`<text x="${x + barWidth}" y="${chartBottom + 16}" text-anchor="middle" font-size="11" fill="#675a4a">${label}</text>`);
      });

      svg.innerHTML = parts.join("");
      note.textContent = usage.retrieval_history_available ? "" : usage.retrieval_history_note;
    }

    function renderContextList(hostId, items, emptyLabel, metaField = "updated_at") {
      const host = document.getElementById(hostId);
      host.innerHTML = "";
      if (!items.length) {
        host.innerHTML = `<div class="empty">${emptyLabel}</div>`;
        return;
      }
      for (const item of items) {
        const row = document.createElement("div");
        row.className = "context-item";
        const meta = String(item[metaField] || "").replace("T", " ").replace("Z", " UTC");
        row.innerHTML = `
          <div class="context-title">${item.title || item.id}</div>
          <div class="context-excerpt">${item.excerpt || ""}</div>
          <div class="context-meta">${meta || item.id}</div>
        `;
        host.appendChild(row);
      }
    }

    function polarToCartesian(cx, cy, radius, angle) {
      return {
        x: cx + radius * Math.cos(angle),
        y: cy + radius * Math.sin(angle)
      };
    }

    function donutPath(cx, cy, innerRadius, outerRadius, startAngle, endAngle) {
      const outerStart = polarToCartesian(cx, cy, outerRadius, startAngle);
      const outerEnd = polarToCartesian(cx, cy, outerRadius, endAngle);
      const innerEnd = polarToCartesian(cx, cy, innerRadius, endAngle);
      const innerStart = polarToCartesian(cx, cy, innerRadius, startAngle);
      const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;
      return [
        `M ${outerStart.x.toFixed(2)} ${outerStart.y.toFixed(2)}`,
        `A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${outerEnd.x.toFixed(2)} ${outerEnd.y.toFixed(2)}`,
        `L ${innerEnd.x.toFixed(2)} ${innerEnd.y.toFixed(2)}`,
        `A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${innerStart.x.toFixed(2)} ${innerStart.y.toFixed(2)}`,
        "Z"
      ].join(" ");
    }

    function colorForIndex(index, alpha = 1) {
      const hues = [164, 192, 28, 346, 214, 38, 116];
      const hue = hues[index % hues.length];
      return `hsla(${hue}, 52%, 42%, ${alpha})`;
    }

    function compactLabel(label, maxLength = 12) {
      if (!label) return "";
      if (label.length <= maxLength) return label;
      const first = label.split(/\\s+/)[0];
      if (first.length <= maxLength) return first;
      return `${label.slice(0, Math.max(4, maxLength - 1))}…`;
    }

    function formatKilobytes(value) {
      if (!Number.isFinite(value)) return "";
      return value >= 1024 ? `${(value / 1024).toFixed(value >= 10 * 1024 ? 0 : 1)} KB` : `${Math.round(value)} B`;
    }

    function renderCategories(categories) {
      const svg = document.getElementById("category-sunburst");
      svg.innerHTML = "";
      const bounds = svg.getBoundingClientRect();
      const width = Math.max(660, Math.round(bounds.width || 660));
      const height = Math.max(320, Math.round(bounds.height || 320));
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      if (!categories.length) {
        svg.innerHTML = `<text x="${width / 2}" y="${height / 2}" text-anchor="middle" dominant-baseline="middle" font-size="16" fill="#675a4a">No stored categories yet.</text>`;
        return;
      }

      const baseWidth = 660;
      const baseHeight = 320;
      const scale = Math.min(width / baseWidth, height / baseHeight) * 1.15;
      const centerX = width / 2;
      const centerY = height / 2;
      const centerRadius = 40 * scale;
      const categoryInner = 52 * scale;
      const categoryOuter = 148 * scale;
      const childInner = 154 * scale;
      const childOuter = 224 * scale;
      const categoryGuide = 134 * scale;
      const childGuide = 190 * scale;
      const categoryLabelRadius = 104 * scale;
      const childLabelRadius = 188 * scale;
      const totalBytes = Math.max(1, categories.reduce((sum, item) => sum + (item.bytes || 0), 0));
      const parts = [
        `<rect x="0" y="0" width="${width}" height="${height}" fill="transparent"></rect>`,
        `<circle cx="${centerX}" cy="${centerY}" r="${centerRadius}" fill="rgba(255,255,255,0.86)" stroke="rgba(77,60,35,0.08)"></circle>`,
        `<circle cx="${centerX}" cy="${centerY}" r="${categoryGuide}" fill="none" stroke="rgba(255,255,255,0.24)" stroke-width="1"></circle>`,
        `<circle cx="${centerX}" cy="${centerY}" r="${childGuide}" fill="none" stroke="rgba(255,255,255,0.18)" stroke-width="1"></circle>`,
        `<text x="${centerX}" y="${centerY - 6}" text-anchor="middle" font-size="11" fill="#675a4a" letter-spacing="1.1">STORE</text>`,
        `<text x="${centerX}" y="${centerY + 12}" text-anchor="middle" font-size="16" font-weight="700" fill="#1f1a17">${formatKilobytes(totalBytes)}</text>`
      ];

      const startBase = -Math.PI * 0.94;
      const sweep = Math.PI * 1.88;
      let cursor = startBase;

      categories.forEach((category, index) => {
        const categoryBytes = Math.max(0, category.bytes || 0);
        const categorySweep = sweep * (categoryBytes / totalBytes);
        const start = cursor;
        const end = cursor + Math.max(categorySweep, 0.035);
        const color = colorForIndex(index, 0.92);
        const categoryMid = (start + end) / 2;
        const categoryArcLength = (categoryOuter - categoryInner) * 1.19 * (end - start);

        parts.push(
          `<path d="${donutPath(centerX, centerY, categoryInner, categoryOuter, start, end)}" fill="${color}" stroke="rgba(255,255,255,0.72)" stroke-width="1.2"></path>`
        );

        const children = category.children || [];
        const childTotal = Math.max(1, children.reduce((sum, child) => sum + (child.bytes || 0), 0));
        let childCursor = start;
        const largeChildren = [];
        children.forEach((child, childIndex) => {
          const childSweep = (end - start) * ((child.bytes || 0) / childTotal);
          const childStart = childCursor;
          const childEnd = childCursor + Math.max(childSweep, 0.016);
          const childColor = colorForIndex(index + childIndex + 1, 0.26 + (0.54 * ((childIndex % 4) + 1) / 4));
          parts.push(
            `<path d="${donutPath(centerX, centerY, childInner, childOuter, childStart, childEnd)}" fill="${childColor}" stroke="rgba(255,255,255,0.84)" stroke-width="1"></path>`
          );
          largeChildren.push({ child, start: childStart, end: childEnd });
          childCursor += childSweep;
        });

        if (categoryArcLength > 40) {
          const labelPoint = polarToCartesian(centerX, centerY, categoryLabelRadius, categoryMid);
          parts.push(
            `<text x="${labelPoint.x.toFixed(2)}" y="${labelPoint.y.toFixed(2)}" text-anchor="middle" dominant-baseline="middle" font-size="11" font-weight="700" fill="#ffffff">${compactLabel(category.label, categoryArcLength > 68 ? 12 : 8)}</text>`
          );
          if (categoryArcLength > 66) {
            parts.push(
              `<text x="${labelPoint.x.toFixed(2)}" y="${(labelPoint.y + 13).toFixed(2)}" text-anchor="middle" dominant-baseline="middle" font-size="10" fill="rgba(255,255,255,0.88)">${formatKilobytes(categoryBytes)}</text>`
            );
          }
        }

        largeChildren
          .sort((a, b) => ((b.child.bytes || 0) - (a.child.bytes || 0)))
          .slice(0, 3)
          .forEach(({ child, start: childStart, end: childEnd }) => {
            const childMid = (childStart + childEnd) / 2;
            const childArcLength = childLabelRadius * 1.07 * (childEnd - childStart);
            if (childArcLength <= 36) return;
            const childPoint = polarToCartesian(centerX, centerY, childLabelRadius, childMid);
            parts.push(
              `<text x="${childPoint.x.toFixed(2)}" y="${childPoint.y.toFixed(2)}" text-anchor="middle" dominant-baseline="middle" font-size="${childArcLength > 76 ? 10 : 9}" font-weight="600" fill="#1f1a17">${compactLabel(child.label, childArcLength > 76 ? 14 : 10)}</text>`
            );
            if (childArcLength > 64) {
              parts.push(
                `<text x="${childPoint.x.toFixed(2)}" y="${(childPoint.y + 12).toFixed(2)}" text-anchor="middle" dominant-baseline="middle" font-size="9" fill="#4d3c23">${formatKilobytes(child.bytes || 0)}</text>`
              );
            }
          });

        cursor += categorySweep;
      });

      svg.innerHTML = parts.join("");
    }

    function render(data) {
      const service = data.service;
      const snapshot = data.snapshot;
      const storage = snapshot.storage;
      const state = snapshot.state;

      document.getElementById("status-dot").className = service.running ? "dot live" : "dot";
      document.getElementById("status-text").textContent = service.running ? "Meridian is running" : "Meridian is stopped";
      document.getElementById("build-stamp").textContent = `UI build: ${data.dashboard.build}`;
      document.getElementById("status-detail").textContent = service.detail;
      document.getElementById("service-url").textContent = service.service_url;
      document.getElementById("service-url").href = service.service_url;
      document.getElementById("client-count").textContent = `${data.dashboard.active_clients} active`;
      document.getElementById("idle-timeout").textContent = `${data.dashboard.idle_timeout_seconds}s idle`;
      document.getElementById("service-pid").textContent = service.pid ?? "-";

      document.getElementById("embedding-backend").textContent = cleanEmbeddingLabel(state.embedding_backend);
      document.getElementById("embedding-summary").textContent = `${state.storage_backend} - ${state.mode} mode`;
      document.getElementById("db-size").textContent = formatBytes(storage.db_file_bytes);
      document.getElementById("db-meta").textContent = state.memory.store_path;
      document.getElementById("stored-bytes").textContent = formatBytes(storage.stored_content_bytes);
      document.getElementById("stored-meta").textContent = `${storage.breakdown.length} groups`;
      document.getElementById("document-count").textContent = String(storage.document_count);
      document.getElementById("memory-count").textContent = `${storage.memory_count} memory records • ${storage.episode_count} episodes`;
      document.getElementById("semantic-count").textContent = `${snapshot.recent_context.semantic.length} shown`;
      document.getElementById("episodic-count").textContent = `${snapshot.recent_context.episodic.length} shown`;

      renderBreakdown(storage.breakdown);
      renderUsageChart(snapshot.usage);
      renderCategories(snapshot.categories);
      renderContextList("semantic-list", snapshot.recent_context.semantic, "No semantic memories yet.");
      renderContextList("episodic-list", snapshot.recent_context.episodic, "No recent episodes yet.", "created_at");
    }

    async function refresh() {
      const response = await fetch("/api/status");
      const data = await response.json();
      render(data);
    }

    async function invoke(url) {
      setBusy(true);
      try {
        const response = await fetch(url, { method: "POST" });
        if (!response.ok) {
          const payload = await response.json();
          throw new Error(payload.error || "Request failed");
        }
        await refresh();
      } catch (error) {
        alert(error.message);
      } finally {
        setBusy(false);
      }
    }

    startButton.addEventListener("click", () => invoke("/api/service/start"));
    stopButton.addEventListener("click", () => invoke("/api/service/stop"));
    refreshButton.addEventListener("click", refresh);

    async function sendHeartbeat() {
      try {
        await fetch("/api/session/heartbeat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: clientId })
        });
      } catch (_) {
      }
    }

    function unregister() {
      const payload = JSON.stringify({ client_id: clientId });
      if (navigator.sendBeacon) {
        navigator.sendBeacon("/api/session/unregister", new Blob([payload], { type: "application/json" }));
        return;
      }
      fetch("/api/session/unregister", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true
      }).catch(() => {});
    }

    window.addEventListener("pagehide", unregister);
    window.addEventListener("beforeunload", unregister);

    sendHeartbeat();
    refresh();
    setInterval(sendHeartbeat, 4000);
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()

