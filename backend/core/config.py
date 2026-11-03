"""全局配置：支持环境变量覆盖，本地开发默认零依赖模式。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(BASE_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "data")
PROCESSED_DIR = os.path.join(DATA_DIR, "processed")
TILE_DIR = os.path.join(DATA_DIR, "tiles")


def env(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


class Settings:
    # ---- 运行模式 ----
    # local  : 纯 Python 几何引擎（无需 Docker / PostGIS，开箱即用）
    # postgis: 使用 PostGIS 做空间分析（docker compose 生产模式）
    SPATIAL_ENGINE = env("SPATIAL_ENGINE", "local")
    DATABASE_URL = env("DATABASE_URL", "")
    REDIS_URL = env("REDIS_URL", "")

    # 瓦片：local = 预生成 GeoJSON 分片；martin = Martin 发布的 MVT
    TILE_SOURCE = env("TILE_SOURCE", "local")
    MARTIN_URL = env("MARTIN_URL", "http://localhost:3000")

    # ---- 业务参数 ----
    CITY_CENTER = (114.3055, 30.5928)
    CITY_BBOX = (113.68, 29.97, 115.08, 31.37)
    HANKOU_WARNING_LEVEL = 27.30          # 武汉关警戒水位(m)
    HANKOU_GUARANTEE_LEVEL = 29.73        # 保证水位(m)
    RUNOFF_COEFF = 0.72                   # 综合径流系数
    FLOOD_SPREAD_FACTOR = 8.5             # 积水深度(cm) -> 淹没半径(m) 的经验扩散系数
    NATURAL_DRAIN_CM_H = 0.6              # 自然消退速率(cm/h)
    ROAD_BLOCK_MIN_LEN_M = 25.0           # 路段进入淹没区超过该长度才判定为阻断

    # ---- 实时气象 ----
    OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
    WEATHER_TTL_SEC = 600

    # ---- 真实感知数据接入（感知层适配器）----
    # off  : 只用模型推演（默认，演示/演练场景）
    # csv  : 回放本地 CSV（历史场次复盘、现场无网络时）
    # http : 定时拉取外部接口（水位计/视频识别/交警封路平台）
    INGEST_SOURCE = env("INGEST_SOURCE", "off")
    INGEST_CSV = env("INGEST_CSV", "")
    INGEST_URL = env("INGEST_URL", "")
    INGEST_INTERVAL_SEC = float(env("INGEST_INTERVAL_SEC", "60"))
    INGEST_SPEED = float(env("INGEST_SPEED", "60"))      # CSV 回放倍速：60 = 1 分钟数据 1 秒放完
    INGEST_TOKEN = env("INGEST_TOKEN", "")               # http 拉取的 Bearer Token（可选）

    # ---- 大模型 ----
    LLM_API_KEY = env("LLM_API_KEY", "")
    LLM_BASE_URL = env("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_MODEL = env("LLM_MODEL", "deepseek-chat")

    # ---- 服务 ----
    HOST = env("HOST", "0.0.0.0")
    PORT = int(env("PORT", "8000"))
    CORS_ORIGINS = env("CORS_ORIGINS", "*").split(",")

    # 2016·7·6 特大暴雨沙盘：24 小时逐时雨强(mm/h)，峰值 88mm
    SCENARIO_2016 = [
        6, 9, 14, 22, 31, 38, 44, 52, 61, 70, 78, 84,
        88, 86, 79, 68, 55, 42, 31, 24, 18, 13, 9, 7,
    ]


settings = Settings()
