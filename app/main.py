import json
import math
import os
import socket
import time
from pathlib import Path
from typing import Dict, Optional

import redis
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel

# --- Configuration & Pod Identity ---
POD_NAME = socket.gethostname()
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", Path(__file__).parent / "config.json"))
REDIS_URL = os.getenv("REDIS_URL", "")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "app_name": "CloudSynced Media Synchronizer",
        "default_media": "Cloud_Demo_Stream_1080p.mp4",
        "sync_interval_ms": 1000,
        "max_rooms": 50,
    }


APP_CONFIG = load_config()

# --- Storage Backend (Redis for Multi-Pod K8s, Local Memory Fallback for Laptop) ---
redis_client: Optional[redis.Redis] = None
if REDIS_URL:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=2)
        redis_client.ping()
    except Exception:
        redis_client = None

local_rooms: Dict[str, dict] = {}

# --- Prometheus Monitoring Metrics ---
SYNC_REQUESTS = Counter(
    "cloudsynced_sync_requests_total",
    "Total number of media state sync updates",
    ["pod", "action"],
)
STATE_READS = Counter(
    "cloudsynced_state_reads_total",
    "Total number of state poll requests",
    ["pod"],
)
ACTIVE_ROOMS = Gauge(
    "cloudsynced_active_rooms",
    "Number of active synchronization rooms",
)

app = FastAPI(title=APP_CONFIG["app_name"], version="1.0.0")


class SyncUpdate(BaseModel):
    media_title: Optional[str] = None
    status: Optional[str] = None  # "playing" or "paused"
    timestamp: Optional[float] = None
    client_id: Optional[str] = "anonymous"


def default_room_state() -> dict:
    return {
        "media_title": APP_CONFIG["default_media"],
        "status": "paused",
        "timestamp": 0.0,
        "updated_at": time.time(),
        "updated_by": "system",
        "last_pod": POD_NAME,
    }


def get_room_state(room_id: str) -> dict:
    if redis_client:
        raw = redis_client.get(f"room:{room_id}")
        if raw:
            state = json.loads(raw)
        else:
            state = default_room_state()
            redis_client.set(f"room:{room_id}", json.dumps(state))
        ACTIVE_ROOMS.set(len(redis_client.keys("room:*")))
    else:
        if room_id not in local_rooms:
            local_rooms[room_id] = default_room_state()
        state = dict(local_rooms[room_id])
        ACTIVE_ROOMS.set(len(local_rooms))

    # Advance playback timestamp dynamically if currently playing
    if state["status"] == "playing":
        elapsed = time.time() - state["updated_at"]
        state["effective_timestamp"] = round(state["timestamp"] + elapsed, 2)
    else:
        state["effective_timestamp"] = round(state["timestamp"], 2)

    state["served_by_pod"] = POD_NAME
    state["storage_backend"] = "redis" if redis_client else "local-memory"
    return state


def save_room_state(room_id: str, state: dict) -> None:
    if redis_client:
        redis_client.set(f"room:{room_id}", json.dumps(state))
        ACTIVE_ROOMS.set(len(redis_client.keys("room:*")))
    else:
        local_rooms[room_id] = state
        ACTIVE_ROOMS.set(len(local_rooms))


# --- API Endpoints ---
@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "pod": POD_NAME,
        "storage_backend": "redis" if redis_client else "local-memory",
        "config_loaded": CONFIG_PATH.exists(),
    }


@app.get("/api/config")
def get_config():
    return {"pod": POD_NAME, "config_path": str(CONFIG_PATH), "config": APP_CONFIG}


@app.get("/api/state/{room_id}")
def read_state(room_id: str):
    STATE_READS.labels(pod=POD_NAME).inc()
    return get_room_state(room_id)


@app.post("/api/sync/{room_id}")
def update_state(room_id: str, payload: SyncUpdate):
    current = get_room_state(room_id)

    if payload.status and payload.status not in ("playing", "paused"):
        raise HTTPException(status_code=400, detail="status must be 'playing' or 'paused'")

    new_timestamp = (
        payload.timestamp
        if payload.timestamp is not None
        else current["effective_timestamp"]
    )

    updated = {
        "media_title": payload.media_title or current["media_title"],
        "status": payload.status or current["status"],
        "timestamp": max(0.0, round(float(new_timestamp), 2)),
        "updated_at": time.time(),
        "updated_by": payload.client_id or "anonymous",
        "last_pod": POD_NAME,
    }
    save_room_state(room_id, updated)
    SYNC_REQUESTS.labels(pod=POD_NAME, action=updated["status"]).inc()

    return get_room_state(room_id)


@app.get("/api/stress")
def simulate_cpu_stress(seconds: float = Query(default=2.0, ge=0.5, le=10.0)):
    """Simulates CPU load for Kubernetes HorizontalPodAutoscaler testing."""
    start = time.time()
    total = 0.0
    i = 1
    while time.time() - start < seconds:
        total += math.sqrt(i) * math.sin(i)
        i += 1
    return {
        "message": "CPU stress test completed",
        "duration_seconds": round(time.time() - start, 2),
        "iterations": i,
        "pod": POD_NAME,
    }


@app.get("/metrics")
def prometheus_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


# --- Built-in Live Demo Web UI ---
@app.get("/", response_class=HTMLResponse)
def index():
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>{APP_CONFIG["app_name"]}</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; margin: 0; padding: 30px; }}
    .card {{ max-width: 680px; margin: auto; background: #1e293b; border-radius: 12px; padding: 24px; box-shadow: 0 10px 25px rgba(0,0,0,0.4); }}
    h1 {{ margin-top: 0; color: #38bdf8; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-size: 13px; font-weight: bold; background: #334155; }}
    .playing {{ background: #16a34a; color: white; }}
    .paused {{ background: #d97706; color: white; }}
    .row {{ margin: 14px 0; }}
    input {{ padding: 8px; border-radius: 6px; border: 1px solid #475569; background: #0f172a; color: white; }}
    button {{ padding: 9px 14px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-right: 6px; background: #0284c7; color: white; }}
    button:hover {{ background: #0369a1; }}
    .danger {{ background: #dc2626; }}
    .info-box {{ background: #0f172a; padding: 14px; border-radius: 8px; margin-top: 18px; font-family: monospace; font-size: 13px; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>CloudSynced</h1>
    <p>Distributed Media State Synchronizer</p>

    <div class="row">
      Room ID: <input id="room" value="main-room" />
      Client Name: <input id="client" value="client-1" />
    </div>

    <div class="row">
      Media Title: <input id="media" size="32" value="{APP_CONFIG["default_media"]}" />
      <button onclick="sendSync({{media_title: document.getElementById('media').value}})">Set Media</button>
    </div>

    <div class="row">
      Status: <span id="statusBadge" class="badge paused">paused</span>
      &nbsp;|&nbsp; Timestamp: <strong id="ts">0.00s</strong>
    </div>

    <div class="row">
      <button onclick="sendSync({{status: 'playing'}})">Play</button>
      <button onclick="sendSync({{status: 'paused'}})">Pause</button>
      <button onclick="seekBy(10)">+10s</button>
      <button onclick="sendSync({{status: 'paused', timestamp: 0}})">Reset</button>
      <button class="danger" onclick="triggerStress()">Simulate CPU Spike</button>
    </div>

    <div class="info-box" id="cloudInfo">Loading cluster state...</div>
  </div>

  <script>
    let latestTimestamp = 0;

    async function fetchState() {{
      const room = document.getElementById("room").value || "main-room";
      try {{
        const res = await fetch(`/api/state/${{encodeURIComponent(room)}}`);
        const data = await res.json();
        latestTimestamp = data.effective_timestamp;
        document.getElementById("ts").textContent = `${{data.effective_timestamp.toFixed(2)}}s`;
        const badge = document.getElementById("statusBadge");
        badge.textContent = data.status;
        badge.className = `badge ${{data.status}}`;
        document.getElementById("cloudInfo").innerHTML =
          `Served By Pod : <b>${{data.served_by_pod}}</b><br>` +
          `Last Updated By: ${{data.updated_by}} (via pod: ${{data.last_pod}})<br>` +
          `Media Title   : ${{data.media_title}}<br>` +
          `Storage Mode  : ${{data.storage_backend}}`;
      }} catch (err) {{
        document.getElementById("cloudInfo").textContent = "Reconnecting to LoadBalancer...";
      }}
    }}

    async function sendSync(changes) {{
      const room = document.getElementById("room").value || "main-room";
      changes.client_id = document.getElementById("client").value || "web-client";
      await fetch(`/api/sync/${{encodeURIComponent(room)}}`, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(changes)
      }});
      fetchState();
    }}

    function seekBy(seconds) {{
      sendSync({{ timestamp: latestTimestamp + seconds }});
    }}

    async function triggerStress() {{
      document.getElementById("cloudInfo").innerHTML += "<br>Running 3s CPU stress on pod...";
      await fetch("/api/stress?seconds=3");
      fetchState();
    }}

    fetchState();
    setInterval(fetchState, {APP_CONFIG["sync_interval_ms"]});
  </script>
</body>
</html>
"""
