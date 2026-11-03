"""FastAPI 生产入口：与零依赖 dev_server 共用同一套业务处理层。

local 模式（默认）下空间计算在纯 Python 引擎中完成；
SPATIAL_ENGINE=postgis 时启用 PostGIS 仓储（见 core/database.py 与 sql/init.sql）。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from backend.api import handlers  # noqa: E402
from backend.core.config import PROJECT_DIR, settings  # noqa: E402
from backend.core.store import get_store  # noqa: E402

# 大要素集响应（视域建筑可达 3000+ 要素 / 900KB）下，orjson 比默认编码器快 3~5 倍。
# 未安装时自动回退，保证零依赖模式仍可运行。
try:
    from fastapi.responses import ORJSONResponse as _DefaultResponse
except Exception:  # pragma: no cover
    _DefaultResponse = JSONResponse

app = FastAPI(title="Wuhan Flood Digital Twin API", version="1.0.0",
              default_response_class=_DefaultResponse)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImpactBody(BaseModel):
    rainfall_mm: float = 120
    duration_hours: float = 3
    target_district: str | None = None
    use_state: bool = False


class RouteBody(BaseModel):
    origin_lng: float
    origin_lat: float
    avoid_waterlogging: bool = True


class CopilotBody(BaseModel):
    prompt: str


class ControlBody(BaseModel):
    mode: str | None = None
    hour: float | None = None
    playing: bool | None = None


@app.on_event("startup")
def _warmup():
    get_store()
    from backend.core.dev_ticker import start_ticker  # 时钟线程

    start_ticker()
    from backend.services.ingest import start_ingest  # 真实感知数据（未配置则跳过）

    start_ingest()


# ---------------- 态势 ----------------

@app.get("/api/v1/health")
def health():
    return handlers.get_health()


@app.get("/api/v1/meta")
def meta():
    return handlers.get_meta()


@app.get("/api/v1/monitor/stations")
def stations(mode: str | None = None, district: str | None = None):
    return handlers.get_stations(mode, district)


@app.get("/api/v1/monitor/alerts")
def alerts():
    return handlers.get_alerts()


@app.get("/api/v1/monitor/districts")
def districts():
    return handlers.get_districts_stats()


@app.get("/api/v1/monitor/water-level")
def water_level():
    return handlers.get_water_level()


@app.get("/api/v1/monitor/control")
def get_control():
    return handlers.get_control()


@app.post("/api/v1/control")
def post_control(body: ControlBody):
    return handlers.post_control(body.model_dump(exclude_none=True))


@app.get("/api/v1/weather")
def weather():
    return handlers.get_weather()


# ---------------- SSE ----------------

@app.get("/api/v1/stream/alerts")
async def stream_alerts():
    async def gen():
        store = get_store()
        last = set()
        while True:
            store.tick(3.0)
            items = store.alerts()
            fresh = [a["station_code"] for a in items if a["station_code"] not in last]
            last = {a["station_code"] for a in items}
            payload = {
                "timestamp": handlers.time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "mode": store.mode,
                "hour": round(store.hour, 2),
                "hankou_level_m": store.hankou_level,
                "count": len(items),
                "alerts": items[:12],
                "new_alert_codes": fresh,
            }
            yield "event: flood_alert\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


# ---------------- 感知数据接入（真实水位计/视频识别/交警封路）----------------


class ReadingBody(BaseModel):
    readings: dict[str, float]
    source: str = "manual"


@app.get("/api/v1/ingest/status")
def ingest_status():
    from backend.services.ingest import status

    return {"code": 200, "data": status()}


@app.post("/api/v1/ingest/readings")
def ingest_readings(body: ReadingBody):
    """外部系统直接推送观测值：{"readings": {"WLO001": 42.5}}，服务端立即覆盖模型值。"""
    store = get_store()
    store.apply_readings(body.readings, source=body.source)
    return {"code": 200, "data": {"applied": len(store.observed), "source": body.source,
                                  "observed_age_sec": 0}}


@app.delete("/api/v1/ingest/readings")
def ingest_clear():
    """撤销真实数据覆盖，回到模型推演值。"""
    get_store().apply_readings({}, source="manual")
    return {"code": 200, "data": {"applied": 0}}


# ---------------- 图层与瓦片 ----------------

# 注意：静态路径必须注册在 /{kind} 之前，否则会被路径参数吞掉（曾导致 500）
@app.get("/api/v1/layers/buffer")
def buffer(lng: float, lat: float, radius_m: float = 500):
    return handlers.get_buffer(lng, lat, radius_m)


@app.get("/api/v1/layers/{kind}")
def layer(kind: str, bbox: str | None = None, limit: int = Query(20000, le=40000)):
    box = [float(v) for v in bbox.split(",")] if bbox else None
    return handlers.get_layer(kind, box, limit)


@app.get("/api/v1/tiles/{kind}/{z}/{x}/{y}")
def tile(kind: str, z: int, x: int, y: int):
    return handlers.get_tile(kind, z, x, y)


# ---------------- 分析与助手 ----------------

@app.post("/api/v1/analysis/inundation-impact")
def impact(body: ImpactBody):
    return handlers.post_impact(body.model_dump())


@app.post("/api/v1/analysis/evacuation-route")
def route(body: RouteBody):
    return handlers.post_evacuation(body.model_dump())


# ---------------- 简报导出 ----------------

@app.get("/api/v1/report/briefing")
def briefing(rainfall_mm: float = 120, duration_hours: float = 3,
             district: str | None = None, fmt: str = "json"):
    """防汛会商简报。

    fmt=json 返回正文与摘要（前端用 Blob 下载，两个运行时都支持）；
    fmt=md / fmt=html 直接返回文件流，便于浏览器下载或打印成 PDF。
    """
    r = handlers.get_briefing(rainfall_mm, duration_hours, district)
    if fmt in ("md", "html"):
        data = r["data"]
        from urllib.parse import quote

        from fastapi.responses import HTMLResponse, PlainTextResponse

        fname = quote(data["filename"])
        if fmt == "md":
            return PlainTextResponse(
                data["markdown"], media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})
        from backend.services.report import markdown_to_html

        html = markdown_to_html(data["markdown"])
        return HTMLResponse(html, headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{fname.replace('.md', '.html')}"})
    return r


@app.post("/api/v1/copilot/chat")
def copilot(body: CopilotBody):
    return handlers.post_copilot(body.model_dump())


# 前端静态资源（构建产物存在时同端口托管）
DIST = os.path.join(PROJECT_DIR, "frontend", "dist")
if os.path.isdir(DIST):
    app.mount("/", StaticFiles(directory=DIST, html=True), name="static")
