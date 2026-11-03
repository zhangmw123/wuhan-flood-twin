#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""零依赖开发服务器（Python 标准库），同时提供 REST API、SSE 与前端静态资源。

用法：
    python -m backend.dev_server            # http://localhost:8000
    python backend/dev_server.py --port 8000

如果 frontend/dist 存在，会直接在同一个端口托管大屏页面（单进程即可演示）；
否则只提供 API，前端用 npm run dev（5173）开发。
"""
import argparse
import json
import mimetypes
import os
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.api import handlers  # noqa: E402
from backend.core.config import PROJECT_DIR, settings  # noqa: E402
from backend.core.store import get_store  # noqa: E402

DIST_DIR = os.path.join(PROJECT_DIR, "frontend", "dist")
_HEARTBEAT = 3.0


# ------------------------------------------------------------------ 后台时钟


def _ticker():
    while True:
        try:
            get_store().tick(_HEARTBEAT)
        except Exception:
            traceback.print_exc()
        time.sleep(1.0)


def start_ticker():
    t = threading.Thread(target=_ticker, daemon=True)
    t.start()
    return t


# ------------------------------------------------------------------ 路由


def _route_get(path, query):
    if path == "/api/v1/health":
        return handlers.get_health()
    if path == "/api/v1/meta":
        return handlers.get_meta()
    if path == "/api/v1/monitor/stations":
        return handlers.get_stations(query.get("mode"), query.get("district"))
    if path == "/api/v1/monitor/alerts":
        return handlers.get_alerts()
    if path == "/api/v1/monitor/districts":
        return handlers.get_districts_stats()
    if path == "/api/v1/monitor/water-level":
        return handlers.get_water_level()
    if path == "/api/v1/monitor/control":
        return handlers.get_control()
    if path == "/api/v1/weather":
        return handlers.get_weather()
    if path == "/api/v1/report/briefing":
        return handlers.get_briefing(
            query.get("rainfall_mm", 120),
            query.get("duration_hours", 3),
            query.get("district") or None)
    if path == "/api/v1/layers/buffer":
        return handlers.get_buffer(query["lng"], query["lat"], query.get("radius_m", 500))
    if path.startswith("/api/v1/layers/"):
        kind = path.rsplit("/", 1)[-1]
        bbox = None
        if query.get("bbox"):
            bbox = [float(v) for v in query["bbox"].split(",")]
        return handlers.get_layer(kind, bbox, int(query.get("limit", 20000)))
    if path.startswith("/api/v1/tiles/"):
        parts = path.split("/")[4:]
        kind, z, x, y = parts[0], int(parts[1]), int(parts[2]), int(parts[3])
        return handlers.get_tile(kind, z, x, y)
    return None


def _route_post(path, body):
    if path == "/api/v1/control":
        return handlers.post_control(body)
    if path == "/api/v1/analysis/inundation-impact":
        return handlers.post_impact(body)
    if path == "/api/v1/analysis/evacuation-route":
        return handlers.post_evacuation(body)
    if path == "/api/v1/copilot/chat":
        return handlers.post_copilot(body)
    return None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FloodTwin/1.0"

    def log_message(self, fmt, *args):
        if "/api/v1/monitor/stations" in (args[0] if args else ""):
            return
        sys.stderr.write("  %s\n" % (fmt % args))

    # -------------------------------------------------- 工具

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,Authorization")

    def _json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # -------------------------------------------------- 方法

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if path == "/api/v1/stream/alerts":
            return self._sse()
        if path.startswith("/api/"):
            try:
                res = _route_get(path, query)
            except KeyError as exc:
                return self._json({"code": 400, "message": f"缺少参数 {exc}"}, 400)
            except Exception as exc:
                traceback.print_exc()
                return self._json({"code": 500, "message": str(exc)}, 500)
            if res is None:
                return self._json({"code": 404, "message": "not found"}, 404)
            return self._json(res)
        return self._static(path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        body = self._read_body()
        try:
            res = _route_post(parsed.path, body)
        except KeyError as exc:
            return self._json({"code": 400, "message": f"缺少参数 {exc}"}, 400)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"code": 500, "message": str(exc)}, 500)
        if res is None:
            return self._json({"code": 404, "message": "not found"}, 404)
        return self._json(res)

    # -------------------------------------------------- SSE

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        store = get_store()
        last_codes = set()
        try:
            while True:
                store.tick(_HEARTBEAT)
                items = store.alerts()
                fresh = [a for a in items if a["station_code"] not in last_codes]
                last_codes = {a["station_code"] for a in items}
                payload = {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                    "mode": store.mode,
                    "hour": round(store.hour, 2),
                    "hankou_level_m": store.hankou_level,
                    "count": len(items),
                    "alerts": items[:12],
                    "new_alert_codes": [a["station_code"] for a in fresh],
                }
                chunk = "event: flood_alert\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
                time.sleep(3.0)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            return

    # -------------------------------------------------- 静态资源

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        file_path = os.path.join(DIST_DIR, rel)
        if not os.path.isfile(file_path):
            if os.path.isfile(os.path.join(DIST_DIR, "index.html")):
                file_path = os.path.join(DIST_DIR, "index.html")   # SPA 回退
            else:
                msg = ("<h2>FloodTwin API 已启动</h2>"
                       "<p>前端尚未构建：请在 frontend 目录执行 <code>npm install && npm run build</code>，"
                       "或使用 <code>npm run dev</code> 以 5173 端口开发。</p>"
                       "<p>接口自检：<a href='/api/v1/health'>/api/v1/health</a></p>").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
                return
        ctype, _ = mimetypes.guess_type(file_path)
        ctype = ctype or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(file_path, "rb") as fp:
            data = fp.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if file_path.endswith(".html") else "max-age=3600")
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser(description="FloodTwin 零依赖开发服务器")
    ap.add_argument("--port", type=int, default=settings.PORT)
    ap.add_argument("--host", default=settings.HOST)
    args = ap.parse_args()

    print("=" * 62)
    print(" 江汉流域防汛抗旱与武汉城市内涝二三维孪生调度平台")
    print("=" * 62)
    print(" 正在加载空间数据 …")
    store = get_store()
    print(f" ✅ 建筑 {len(store.buildings):,} 栋 | 道路 {len(store.roads):,} 条 | "
          f"渍水点 {len(store.waterlogging)} 个 | 避难所 {len(store.shelters['features'])} 处")
    print(f" ✅ 数据加载耗时 {store.load_seconds}s | 空间引擎 {settings.SPATIAL_ENGINE}")
    start_ticker()
    has_dist = os.path.isdir(DIST_DIR)
    print(f" API     : http://localhost:{args.port}/api/v1/health")
    print(f" 大屏页面: {'http://localhost:%d/' % args.port if has_dist else '未构建（frontend/dist 不存在）'}")
    print("=" * 62)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
