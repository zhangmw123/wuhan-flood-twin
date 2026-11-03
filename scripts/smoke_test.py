"""端到端接口自检（零依赖，仅用标准库）。

用途：交付前/部署后用一条命令确认「数据 → 空间引擎 → 接口 → SSE」全链路可用。
用法：
    python scripts/smoke_test.py                 # 默认 http://localhost:8000
    python scripts/smoke_test.py http://host:8000
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
RESULTS = []


def call(method: str, path: str, body=None, timeout=40):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw.decode("utf-8")), (time.time() - t) * 1000


def check(name: str, fn):
    t = time.time()
    try:
        detail = fn()
        RESULTS.append((True, name, (time.time() - t) * 1000, detail))
    except Exception as exc:  # noqa: BLE001 - 自检脚本需要汇总所有失败项
        RESULTS.append((False, name, (time.time() - t) * 1000, f"{type(exc).__name__}: {exc}"))


def tile_xy(lng, lat, z=12):
    n = 2 ** z
    x = int((lng + 180.0) / 360.0 * n)
    rad = math.radians(lat)
    y = int((1.0 - math.log(math.tan(rad) + 1 / math.cos(rad)) / math.pi) / 2.0 * n)
    return z, x, y


def main():
    print(f"目标服务：{BASE}\n" + "-" * 74)

    check("健康检查", lambda: (lambda d: f"engine={d['engine']} 建筑={d['buildings']} 加载={d['load_seconds']}s")(
        call("GET", "/api/v1/health")[0]))
    check("元数据", lambda: (lambda d: f"城市={d['data']['city']} 数据版本={d['data']['dataset']['generated_at'][:16]}" )(
        call("GET", "/api/v1/meta")[0]))
    check("监测站点", lambda: (lambda d: f"{len(d['features'])} 个站点, 告警 {d['metadata']['alert_station_count']}")(
        call("GET", "/api/v1/monitor/stations")[0]))
    check("分区统计", lambda: f"{len(call('GET', '/api/v1/monitor/districts')[0]['data'])} 个行政区")
    check("水位过程线", lambda: (lambda d: f"汉口 峰值 {max(x['level_m'] for x in d['data']['series'])}m / {len(d['data']['series'])} 点")(
        call("GET", "/api/v1/monitor/water-level")[0]))
    check("图层-建筑", lambda: (lambda d: f"{len(d['features'])} 个要素")(
        call("GET", "/api/v1/layers/buildings?bbox=114.30,30.55,114.34,30.59")[0]))
    check("图层-水系", lambda: (lambda d: f"{len(d['features'])} 个水体")(
        call("GET", "/api/v1/layers/water")[0]))
    check("矢量瓦片", lambda: (lambda zxy: f"z{zxy[0]}/{zxy[1]}/{zxy[2]} -> {len(call('GET', f'/api/v1/tiles/buildings/{zxy[0]}/{zxy[1]}/{zxy[2]}')[0]['features'])} 要素")(
        tile_xy(114.32, 30.57)))
    check("灾情评估(120mm/3h)", lambda: (lambda d: (lambda x: f"淹没 {x['inundated_area_km2']}km² 受淹建筑 {x['affected_building_count']} 阻断路 {x['blocked_road_count']} 用时 {x['compute_ms']}ms")(
        d["data"]))(call("POST", "/api/v1/analysis/inundation-impact", {"rainfall_mm": 120, "duration_hours": 3})[0]))
    check("缓冲分析(500m)", lambda: (lambda d: f"建筑 {d['building_count']} 栋 / 均高 {d['avg_elevation']}m / 避难所 {d['shelter_count']}")(
        call("GET", "/api/v1/layers/buffer?lng=114.3436&lat=30.5494&radius_m=500")[0]["data"]))
    check("避险路径", lambda: (lambda d: f"至 {d['target_shelter']['name']} {d['target_shelter']['distance_m']}m / {d['estimated_time_min']}min / 避障 {d['avoid_blocked_roads']} 段")(
        call("POST", "/api/v1/analysis/evacuation-route", {"origin_lng": 114.3436, "origin_lat": 30.5494})[0]["data"]))
    check("AI Copilot", lambda: (lambda d: f"返回 {len(d['map_actions'])} 条地图指令 [{', '.join(a['action'] for a in d['map_actions'])}]")(
        call("POST", "/api/v1/copilot/chat", {"prompt": "筛选武昌区积水超过30cm的点，飞过去并分析周边500米建筑"})[0]))
    check("感知接入状态", lambda: (lambda d: f"source={d['source']} status={d['status']}")(
        call("GET", "/api/v1/ingest/status")[0]["data"]))

    def ingest_roundtrip():
        call("POST", "/api/v1/ingest/readings",
             {"readings": {"WL-江汉-032": 66.0}, "source": "smoke-test"})
        md = call("GET", "/api/v1/monitor/stations")[0]["metadata"]
        call("DELETE", "/api/v1/ingest/readings")
        if md.get("data_source") != "observed":
            raise RuntimeError("注入后 data_source 未切换为 observed")
        return f"注入生效（覆盖 {md['observed_station_count']} 站），已撤销回模型值"

    check("实测数据注入/撤销", ingest_roundtrip)

    def briefing_check():
        d = call("GET", "/api/v1/report/briefing?rainfall_mm=120&duration_hours=3")[0]["data"]
        md = d["markdown"]
        for key in ("防汛会商简报", "雨情汛情", "处置建议", "数据说明"):
            if key not in md:
                raise RuntimeError(f"简报缺少章节 {key}")
        s = d["summary"]
        if s["max_depth_cm"] <= 0 or s["zone_count"] <= 0:
            raise RuntimeError("简报摘要数值异常")
        return (f"编号 {d['filename'].split('_')[-1].replace('.md','')} · "
                f"{s['zone_count']} 淹没区 / 最深 {s['max_depth_cm']}cm")

    check("会商简报生成", briefing_check)

    check("模式切换", lambda: (lambda d: f"mode={d['data']['mode']}")(call("POST", "/api/v1/control", {"mode": "simulation"})[0]))

    # SSE：连接后 8 秒内至少收到一帧
    def sse():
        req = urllib.request.Request(BASE + "/api/v1/stream/alerts", headers={"Accept": "text/event-stream"})
        t0 = time.time()
        frames = 0
        with urllib.request.urlopen(req, timeout=12) as r:
            while time.time() - t0 < 8:
                line = r.readline()
                if line.startswith(b"event:"):
                    frames += 1
        if not frames:
            raise RuntimeError("8 秒内未收到 SSE 帧")
        return f"{frames} 帧 / 8s"

    check("SSE 推送", sse)

    print()
    ok = sum(1 for r in RESULTS if r[0])
    for passed, name, ms, detail in RESULTS:
        print(f"{'PASS' if passed else 'FAIL'}  {name:<22} {ms:7.0f}ms  {detail}")
    print("-" * 74)
    print(f"合计 {ok}/{len(RESULTS)} 通过" + ("，全部链路正常。" if ok == len(RESULTS) else "，存在失败项。"))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
