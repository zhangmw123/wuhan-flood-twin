"""真实感知数据接入层（Sensor Adapter）。

现状与定位
----------
本平台默认的积水深度由「区级水量平衡模型」推演得出，属于**模型值**。
真实数据（水位计 / 视频积水识别 / 交警封路 / 水务局接口）不对外公开，
因此这里把「接入点」做成标准适配器：只要把观测值喂进 `store.apply_readings()`，
前端、接口、空间分析全部无需改动，`/monitor/stations` 的 `metadata.data_source`
会自动从 `model` 变为 `observed`，界面上不再显示「推演」。

三种来源（由 `.env` 的 `INGEST_SOURCE` 控制）
--------------------------------------------
off   默认。不接入，只用模型推演。
csv   回放 CSV，用于历史场次复盘或无网现场演示。
      文件格式：timestamp,station_code,depth_cm[,level_m]
http  定时拉取外部 JSON 接口（生产接法）。

站点编码以 `data/processed/wuhan_waterlogging.geojson` 的 `station_code` 为准，
未知编码会被忽略并计数，避免脏数据污染推演状态。
"""
from __future__ import annotations

import csv
import json
import threading
import time
import urllib.request
from datetime import datetime

from ..core.config import settings
from ..core.store import get_store

_LOCK = threading.Lock()
_STATE = {
    "enabled": False,
    "source": "off",
    "status": "idle",          # idle | running | error
    "last_push_at": None,
    "pushed_stations": 0,
    "unknown_codes": 0,
    "last_error": None,
    "detail": "",
}
_THREAD: threading.Thread | None = None


def status() -> dict:
    with _LOCK:
        return dict(_STATE)


def _push(readings: dict, source: str) -> int:
    """把观测值写入仓储；返回成功匹配的站点数。"""
    store = get_store()
    known = {s["station_code"] for s in store.waterlogging}
    accepted = {k: v for k, v in readings.items() if k in known}
    unknown = len(readings) - len(accepted)
    store.apply_readings(accepted, source=source)
    with _LOCK:
        _STATE.update({
            "status": "running",
            "last_push_at": datetime.now().isoformat(timespec="seconds"),
            "pushed_stations": len(accepted),
            "unknown_codes": unknown,
            "last_error": None,
        })
    return len(accepted)


# ---------------------------------------------------------------- CSV 回放


def _load_csv(path: str):
    """解析 CSV，返回按时间排序的 (timestamp, {code: depth_cm}) 序列。"""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ts = (row.get("timestamp") or "").strip()
            code = (row.get("station_code") or "").strip()
            depth = row.get("depth_cm")
            if not ts or not code or depth in (None, ""):
                continue
            try:
                dt = datetime.strptime(ts.replace("T", " "), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    dt = datetime.strptime(ts.replace("T", " "), "%Y-%m-%d %H:%M")
                except ValueError:
                    continue
            try:
                rows.append((dt, code, float(depth)))
            except ValueError:
                continue
    rows.sort(key=lambda r: r[0])
    return rows


def _run_csv(path: str, speed: float):
    rows = _load_csv(path)
    if not rows:
        with _LOCK:
            _STATE.update({"status": "error", "last_error": f"CSV 无可解析数据：{path}"})
        return

    with _LOCK:
        _STATE.update({"status": "running", "detail": f"CSV 回放 {len(rows)} 行，{speed}x"})

    latest: dict[str, float] = {}
    idx = 0
    t0 = rows[0][0]
    started = time.time()

    while True:
        virtual = (time.time() - started) * speed  # 已回放的数据秒数
        changed = False
        while idx < len(rows) and (rows[idx][0] - t0).total_seconds() <= virtual:
            _, code, depth = rows[idx]
            latest[code] = depth
            idx += 1
            changed = True
        if changed:
            _push(latest, f"csv:{path.split('/')[-1]}")
        if idx >= len(rows):
            with _LOCK:
                _STATE["detail"] = "回放结束（保持末帧）"
            # 结束后保持末帧，不重复推送
            time.sleep(3600)
        time.sleep(0.5)


# ---------------------------------------------------------------- HTTP 拉取


def _normalize(payload) -> dict:
    """兼容多种返回结构，抽取 {station_code: depth_cm}。"""
    items = None
    if isinstance(payload, dict):
        for key in ("readings", "data", "items", "list", "result"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        if items is None:
            # 形如 {"WLO001": 12.5, ...}
            flat = {k: v for k, v in payload.items() if isinstance(v, (int, float))}
            if flat:
                return {str(k): float(v) for k, v in flat.items()}
    elif isinstance(payload, list):
        items = payload
    if not items:
        return {}

    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        code = it.get("station_code") or it.get("code") or it.get("id")
        depth = None
        for key in ("depth_cm", "depth", "water_depth_cm", "value"):
            if it.get(key) is not None:
                depth = it[key]
                break
        if code is None or depth is None:
            continue
        try:
            out[str(code)] = float(depth)
        except (TypeError, ValueError):
            continue
    return out


def _fetch_once(url: str, token: str = "") -> dict:
    headers = {"Accept": "application/json", "User-Agent": "wuhan-flood-twin/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    raw = urllib.request.urlopen(req, timeout=15).read()
    return _normalize(json.loads(raw.decode("utf-8")))


def _run_http(url: str, interval: float, token: str):
    with _LOCK:
        _STATE.update({"status": "running", "detail": f"HTTP 轮询 {url}，间隔 {interval}s"})
    while True:
        try:
            readings = _fetch_once(url, token)
            if readings:
                _push(readings, f"http:{url.split('/')[2] if '//' in url else url}")
            else:
                with _LOCK:
                    _STATE["last_error"] = "接口返回无可识别的读数"
        except Exception as exc:  # noqa: BLE001 - 采集失败不应中断服务
            with _LOCK:
                _STATE.update({"status": "error", "last_error": f"{type(exc).__name__}: {exc}"})
        time.sleep(max(5.0, interval))


# ---------------------------------------------------------------- 启动


def start_ingest() -> dict:
    """按配置启动采集线程（幂等）。返回当前状态。"""
    global _THREAD
    source = (settings.INGEST_SOURCE or "off").lower()
    if source == "off":
        with _LOCK:
            _STATE.update({"enabled": False, "source": "off", "status": "disabled",
                           "detail": "未配置真实数据源，当前为模型推演值"})
        return status()

    if _THREAD and _THREAD.is_alive():
        return status()

    if source == "csv":
        path = settings.INGEST_CSV
        if not path:
            with _LOCK:
                _STATE.update({"enabled": False, "source": "csv", "status": "error",
                               "last_error": "INGEST_SOURCE=csv 但未设置 INGEST_CSV"})
            return status()
        target, args = _run_csv, (path, settings.INGEST_SPEED)
    elif source == "http":
        if not settings.INGEST_URL:
            with _LOCK:
                _STATE.update({"enabled": False, "source": "http", "status": "error",
                               "last_error": "INGEST_SOURCE=http 但未设置 INGEST_URL"})
            return status()
        target, args = _run_http, (settings.INGEST_URL, settings.INGEST_INTERVAL_SEC, settings.INGEST_TOKEN)
    else:
        with _LOCK:
            _STATE.update({"enabled": False, "source": source, "status": "error",
                           "last_error": f"未知 INGEST_SOURCE={source}"})
        return status()

    with _LOCK:
        _STATE.update({"enabled": True, "source": source, "status": "starting", "detail": "启动中"})
    _THREAD = threading.Thread(target=target, args=args, daemon=True, name="ingest")
    _THREAD.start()
    return status()
