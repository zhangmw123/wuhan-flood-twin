"""AI 防汛调度 Copilot：自然语言 → 空间查询 → 地图指令。

两种工作方式：
1. 配置了 LLM_API_KEY：调用 OpenAI 兼容接口的 Function Calling，由模型决定调用哪个工具；
2. 未配置或调用失败：使用内置中文规则解析器（同样返回 reply + map_actions），保证演示不中断。
"""
import json
import re
import urllib.request

from ..core import geo
from ..core.config import settings
from ..core.store import get_store
from . import analysis

DISTRICTS = ["江岸区", "江汉区", "硚口区", "汉阳区", "武昌区", "洪山区", "青山区",
             "东西湖区", "汉南区", "蔡甸区", "江夏区", "黄陂区", "新洲区"]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_stations",
            "description": "按行政区与积水深度筛选易渍水监测点，返回站点列表及最严重点位坐标。",
            "parameters": {
                "type": "object",
                "properties": {
                    "district": {"type": "string", "description": "行政区名称，如 武昌区"},
                    "min_depth_cm": {"type": "number", "description": "最小积水深度(厘米)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buffer_analysis",
            "description": "以某个坐标为中心生成半径 N 米的受灾影响缓冲区，统计圈内建筑栋数、重点单位与最近避难所。",
            "parameters": {
                "type": "object",
                "properties": {
                    "lng": {"type": "number"}, "lat": {"type": "number"},
                    "radius_m": {"type": "number", "description": "缓冲区半径(米)"},
                },
                "required": ["lng", "lat", "radius_m"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evacuation_route",
            "description": "为指定坐标规划避开积水路段的最近应急避难场所转移路线。",
            "parameters": {
                "type": "object",
                "properties": {"lng": {"type": "number"}, "lat": {"type": "number"}},
                "required": ["lng", "lat"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inundation_impact",
            "description": "按累计降雨量推演内涝范围并统计受淹建筑、阻断道路数量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "rainfall_mm": {"type": "number"}, "duration_hours": {"type": "number"},
                    "district": {"type": "string"},
                },
                "required": ["rainfall_mm"],
            },
        },
    },
]


# ------------------------------------------------------------------ 规则解析


def _find_district(text):
    for d in DISTRICTS:
        if d in text or d.replace("区", "") in text:
            return d
    return None


def _find_number(text, units=("cm", "厘米", "公分", "米", "m", "mm", "毫米"), default=None):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(cm|厘米|公分|米|m|mm|毫米)", text)
    if m:
        v = float(m.group(1))
        u = m.group(2)
        if u in ("米", "m"):
            return v * 100 if "缓冲" not in text else v
        if u in ("mm", "毫米"):
            return v
        return v
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else default


def _parse_radius(text, default=500):
    """从"500米""0.5公里""800m"中解析缓冲半径。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(公里|km|千米)", text)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(米|m)(?![m])", text)
    if m:
        return int(float(m.group(1)))
    m = re.search(r"(\d+)\s*(?![.\d])", text)
    if m:
        v = float(m.group(1))
        return int(v * 10) if v < 50 else int(v)
    return default


def _fmt_station(s, depth):
    return f"{s['name']}（{s['district']}，{depth}cm）"


def rule_based(prompt):
    store = get_store()
    text = prompt.strip()
    district = _find_district(text)
    actions = []
    parts = []

    # 意图：最近避难所 / 转移路线
    if any(k in text for k in ("避难", "疏散", "转移", "撤离")):
        worst = max(store.waterlogging, key=lambda s: store.depths.get(s["station_code"], 0))
        route = analysis.evacuation_route(worst["lng"], worst["lat"])
        if route["code"] == 200:
            d = route["data"]
            sh = d["target_shelter"]
            actions.append({"action": "SHOW_ROUTE", "payload": {
                "route": d["route_geojson"], "origin": d["origin"],
                "target": [sh.get("lng"), sh.get("lat")], "shelter": sh,
            }})
            actions.append({"action": "FLY_TO", "payload": {
                "center": d["origin"], "zoom": 15, "pitch": 55}})
            parts.append(
                f"已以当前积水最严重的【{worst['name']}】（{store.depths.get(worst['station_code'], 0)}cm）为起点，"
                f"规划至最近避难所【{sh['name']}】的转移路线：全程 {d['target_shelter']['distance_m']} 米，"
                f"步行约 {d['estimated_time_min']} 分钟，已避开 {d['avoid_blocked_roads']} 条积水阻断路段。"
            )
        else:
            parts.append("当前范围内未找到可用避难所，建议扩大搜索范围。")

    # 意图：缓冲区分析
    elif any(k in text for k in ("缓冲", "周边", "影响范围", "圈选")):
        radius = _parse_radius(text)
        cands = [s for s in store.waterlogging if (not district or s["district"] == district)]
        if not cands:
            cands = store.waterlogging
        target = max(cands, key=lambda s: store.depths.get(s["station_code"], 0))
        depth = store.depths.get(target["station_code"], 0)
        res = analysis.buffer_analysis(target["lng"], target["lat"], radius)
        actions.append({"action": "FLY_TO", "payload": {"center": [target["lng"], target["lat"]], "zoom": 16, "pitch": 55}})
        actions.append({"action": "DRAW_BUFFER", "payload": {
            "center": [target["lng"], target["lat"]], "radius_m": radius,
            "geojson": res["buffer_geojson"]}})
        if res["nearest_shelter"]:
            actions.append({"action": "HIGHLIGHT_SHELTER", "payload": {
                "name": res["nearest_shelter"]["name"], "shelter": res["nearest_shelter"],
            }})
        parts.append(
            f"已在【{target['name']}】（当前积水 {depth}cm）周边生成 {radius} 米受灾影响缓冲区："
            f"圈内共 {res['building_count']} 栋建筑（均高 {res['avg_elevation']}m，总面积 "
            f"{res['building_total_area_m2'] / 1e4:.1f} 万㎡）、{res['poi_count']} 处重点单位。"
            + (f"最近避难所为【{res['nearest_shelter']['name']}】，直线距离 {res['nearest_shelter']['distance_m']} 米。"
               if res["nearest_shelter"] else "圈内暂无避难场所。")
        )

    # 意图：灾情推演
    elif any(k in text for k in ("推演", "评估", "受灾", "灾情", "内涝")):
        rain = _find_number(text, default=120) or 120
        if rain < 30:
            rain = rain * 10
        dur = 3.0
        m = re.search(r"(\d+)\s*(小时|h)", text)
        if m:
            dur = float(m.group(1))
        res = analysis.impact_analysis(rain, dur, district)["data"]
        parts.append(
            f"按 {rain:.0f}mm/{dur:.0f}h 雨量情景推演：淹没区 {res['inundated_area_km2']} km²，"
            f"受淹建筑 {res['affected_building_count']} 栋，阻断道路 {res['blocked_road_count']} 条"
            f"（{res['blocked_road_length_km']} km），威胁重点单位 {res['threatened_key_poi_count']} 处。"
        )
        actions.append({"action": "SET_RAINFALL", "payload": {"rainfall_mm": rain, "duration_h": dur}})

    # 默认意图：筛选站点
    else:
        threshold = 40.0
        m = re.search(r"(\d+(?:\.\d+)?)\s*(cm|厘米)", text)
        if m:
            threshold = float(m.group(1))
        cands = []
        for s in store.waterlogging:
            if district and s["district"] != district:
                continue
            depth = store.depths.get(s["station_code"], 0)
            if depth >= threshold:
                cands.append((s, depth))
        cands.sort(key=lambda x: -x[1])
        if cands:
            names = "、".join(_fmt_station(s, d) for s, d in cands[:5])
            parts.append(
                f"{district or '全市'}当前积水超过 {threshold:.0f}cm 的监测点共 {len(cands)} 处，最严重为：{names}。"
                f"已将视角定位至【{cands[0][0]['name']}】。"
            )
            top = cands[0][0]
            actions.append({"action": "FLY_TO", "payload": {"center": [top["lng"], top["lat"]], "zoom": 16, "pitch": 58}})
            if any(k in text for k in ("缓冲", "500", "周边")):
                pass
        else:
            parts.append(
                f"{district or '全市'}当前没有积水超过 {threshold:.0f}cm 的监测点，"
                f"全市平均小时雨强 {sum(store.district_rain.values()) / max(1, len(store.district_rain)):.1f}mm。"
            )

    if district and "区" in text and not any(a["action"] == "FLY_TO" for a in actions):
        bb = store.district_bbox.get(district)
        if bb:
            actions.append({"action": "FLY_TO", "payload": {
                "center": [(bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2], "zoom": 12.5, "pitch": 45}})

    return {"reply": "".join(parts), "map_actions": actions, "provider": "rule-engine"}


# ------------------------------------------------------------------ 大模型


def _call_llm(prompt, context):
    body = {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": (
                "你是武汉市防汛抗旱指挥中心的调度助手。你可以调用工具完成空间查询与地图操控。"
                "涉及坐标时必须先调用 query_stations 获取真实点位，不要编造坐标。"
                "回答简洁专业，使用中文。当前态势：" + json.dumps(context, ensure_ascii=False))
            },
            {"role": "user", "content": prompt},
        ],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        settings.LLM_BASE_URL.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {settings.LLM_API_KEY}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def _exec_tool(name, args):
    store = get_store()
    if name == "query_stations":
        d = args.get("district")
        th = float(args.get("min_depth_cm") or 30)
        cands = [(s, store.depths.get(s["station_code"], 0)) for s in store.waterlogging
                 if (not d or s["district"] == d)]
        cands = [c for c in cands if c[1] >= th]
        cands.sort(key=lambda x: -x[1])
        return {
            "count": len(cands),
            "stations": [{"name": s["name"], "district": s["district"], "depth_cm": dep,
                          "lng": s["lng"], "lat": s["lat"], "pump": s.get("pump_name")}
                         for s, dep in cands[:10]],
        }
    if name == "buffer_analysis":
        res = analysis.buffer_analysis(args["lng"], args["lat"], args["radius_m"])
        return {k: v for k, v in res.items() if k not in ("buildings",)}
    if name == "evacuation_route":
        return analysis.evacuation_route(args["lng"], args["lat"])
    if name == "inundation_impact":
        return analysis.impact_analysis(args.get("rainfall_mm", 120),
                                        args.get("duration_hours", 3),
                                        args.get("district"))["data"]
    return {"error": "unknown tool"}


def _actions_from_tool(name, args, result):
    if name == "query_stations" and result.get("stations"):
        top = result["stations"][0]
        return [{"action": "FLY_TO", "payload": {"center": [top["lng"], top["lat"]], "zoom": 16, "pitch": 58}}]
    if name == "buffer_analysis":
        return [
            {"action": "FLY_TO", "payload": {"center": args["lng"], "lat": args["lat"],
                                             "center2": None, "zoom": 16, "pitch": 55}},
            {"action": "DRAW_BUFFER", "payload": {"center": [args["lng"], args["lat"]],
                                                  "radius_m": args["radius_m"],
                                                  "geojson": result.get("buffer_geojson")}},
        ]
    return []


def chat(prompt):
    store = get_store()
    if not settings.LLM_API_KEY:
        return rule_based(prompt)

    context = {
        "mode": store.mode, "hour": round(store.hour, 1),
        "hankou_level": store.hankou_level,
        "warning_level": settings.HANKOU_WARNING_LEVEL,
        "active_stations": len([d for d in store.depths.values() if d >= 30]),
    }
    try:
        resp = _call_llm(prompt, context)
        msg = resp["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        actions, facts = [], []
        for c in calls[:3]:
            fn = c["function"]["name"]
            args = json.loads(c["function"].get("arguments") or "{}")
            result = _exec_tool(fn, args)
            facts.append({"tool": fn, "args": args, "result": result})
            actions.extend(_actions_from_tool(fn, args, result))
        reply = msg.get("content") or ""
        if not reply.strip():
            reply = rule_based(prompt)["reply"]
        return {"reply": reply, "map_actions": actions, "facts": facts, "provider": "llm"}
    except Exception as exc:
        fb = rule_based(prompt)
        fb["provider"] = f"rule-engine(fallback: {type(exc).__name__})"
        return fb
