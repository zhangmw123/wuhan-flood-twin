"""防汛会商简报生成。

把「推演结果 + 实时雨情 + 站点台账」组织成一份可直接下发/打印的会商材料，
补齐从"看得见"到"出得了文"的最后一环——这是防汛值班室的真实工作流：
研判完必须出简报，否则调度指令没有依据。

输出两种格式：
  · markdown —— 便于粘贴进 OA / 微信工作群
  · html     —— 浅色排版，浏览器直接打印成 PDF

所有数字均来自系统当前状态，不含任何硬编码结论；推演成分在文末显式声明。
"""
import datetime as _dt

from ..core.store import get_store
from ..services import analysis

# 深度阈值与处置口径（与前端站点配色保持一致）
LEVELS = [(50.0, "红色（封路）"), (30.0, "橙色（严重积水）"),
          (15.0, "黄色（明显积水）"), (0.0, "蓝色（关注）")]


def _level(depth):
    for th, name in LEVELS:
        if depth >= th:
            return name
    return "正常"


def _now():
    return _dt.datetime.now()


def _suggestions(imp, meta, breakdown):
    """按规则生成处置建议：每条都对应一个可执行动作，不写空话。"""
    out = []
    if imp["max_depth_cm"] >= 50:
        out.append(
            f"**交通管控**：最大积水深度达 {imp['max_depth_cm']:.0f}cm，已有 "
            f"{imp['blocked_road_count']} 处路段（合计 {imp['blocked_road_length_km']:.1f} km）"
            f"达到封路阈值。建议交警部门对下穿通道、立交底层优先实施双向封闭，"
            f"并通过导航平台同步推送绕行方案。"
        )
    elif imp["max_depth_cm"] >= 30:
        out.append(
            f"**交通管控**：最大积水深度 {imp['max_depth_cm']:.0f}cm，"
            f"{imp['blocked_road_count']} 段道路达到阻断计算阈值。建议对下穿通道设置警示并派员值守，"
            f"视雨情发展准备封闭。"
        )
    else:
        out.append(
            f"**交通管控**：最大积水深度 {imp['max_depth_cm']:.0f}cm，暂未达封路阈值。"
            f"建议维持常态化巡查，重点关注 {imp['blocked_road_count']} 处已达阻断计算阈值的路段。"
        )

    if imp["threatened_shelter_count"] > 0:
        names = "、".join(s["name"] for s in imp["threatened_shelters"][:3])
        out.append(
            f"**避难场所**：{imp['threatened_shelter_count']} 处应急避难场所位于淹没范围内"
            f"（{names} 等），建议提前核查场内排水与备用电源，必要时启用第二梯队场所。"
        )
    else:
        out.append("**避难场所**：现有淹没范围内未命中应急避难场所，暂无需转移安置。")

    if imp["threatened_key_poi_count"] > 0:
        names = "、".join(p["name"] for p in imp["threatened_poi"][:3])
        out.append(
            f"**重点单位**：{imp['threatened_key_poi_count']} 处重点单位受影响"
            f"（{names} 等），建议逐一电话叫应，落实地下空间挡水与人员疏散预案。"
        )

    if breakdown:
        worst = breakdown[0]
        out.append(
            f"**力量预置**：受淹建筑最集中的是 {worst['district']}（{worst['buildings']} 栋，"
            f"其中低洼 {worst['low_elev']} 栋），建议移动泵车与抢险队向该区前置。"
        )

    wl = meta.get("hankou_water_level_m") or 0
    warn = meta.get("hankou_warning_level_m") or 27.3
    if wl >= warn:
        out.append(
            f"**堤防巡查**：汉口水位 {wl:.2f}m 已超警戒 {warn:.2f}m，"
            f"建议按预案加密巡堤查险频次，重点检查历史险工险段。"
        )
    else:
        out.append(
            f"**堤防巡查**：汉口水位 {wl:.2f}m，低于警戒水位 {warn - wl:.2f}m，"
            f"维持常规巡查即可；若后续降雨持续，需关注水位上涨速率。"
        )

    if meta.get("data_source") == "observed":
        out.append(
            f"**数据说明**：当前 {meta.get('observed_station_count', 0)} 个站点使用实测数据"
            f"（来源：{meta.get('observed_provider') or '外部接入'}），"
            f"其余站点为模型推演值，研判时请优先采信实测点。"
        )
    else:
        out.append(
            "**数据说明**：当前全部站点为模型推演值（未接入实测感知），"
            "本简报用于演练与态势预判，不可直接作为调度指令依据。"
        )
    return out


def build_briefing(rainfall_mm=120.0, duration_h=3.0, district=None):
    store = get_store()
    imp = analysis.impact_analysis(rainfall_mm, duration_h, district)["data"]
    meta = store.station_meta()
    stats = analysis.district_stats()
    now = _now()

    seq = now.strftime("%Y%m%d%H%M")
    scope = district or "全市"
    # 与灾情评估同一口径：情景推演深度，而非沙盘时刻深度
    scenario = analysis.station_scenario_depths(rainfall_mm, duration_h, district)
    top = scenario[:5]
    breakdown = sorted(imp["district_breakdown"], key=lambda x: -x["buildings"])

    # 全区雨强排序（用于雨情段落）
    rain_sorted = sorted(stats, key=lambda x: -x["rain_1h_mm"])[:3]
    rain_txt = "、".join(f"{d['district']} {d['rain_1h_mm']:.1f}mm/h" for d in rain_sorted)

    L = []
    L.append(f"# 武汉市防汛会商简报")
    L.append("")
    L.append(f"**编号：WH-FX-{seq}**　　编制时间：{now.strftime('%Y年%m月%d日 %H:%M')}　　"
             f"密级：内部")
    L.append("")
    L.append(f"**研判范围**：{scope}　|　**研判情景**：累计雨量 {rainfall_mm:.0f}mm / 历时 {duration_h:.0f}h"
             f"（独立情景推演，不随沙盘时钟变化）")
    L.append("")
    L.append("---")
    L.append("")

    # 一、雨情汛情
    L.append("## 一、雨情汛情")
    L.append("")
    L.append(f"当前全市平均小时雨强 **{meta['city_avg_rain_1h_mm']:.1f} mm/h**，"
             f"雨强前三为 {rain_txt}。"
             f"汉口水文站水位 **{meta['hankou_water_level_m']:.2f} m**，"
             f"警戒水位 {meta['hankou_warning_level_m']:.2f} m，"
             f"{'**已超警戒**' if meta['hankou_water_level_m'] >= meta['hankou_warning_level_m'] else '低于警戒'}。")
    L.append("")
    if meta["alert_station_count"] or meta["danger_station_count"]:
        L.append(f"全市 {len(store.waterlogging)} 处易渍水监测点中，达橙色以上 "
                 f"{meta['danger_station_count']} 处、黄色 {meta['alert_station_count']} 处，"
                 f"需重点关注。")
    else:
        L.append(f"全市 {len(store.waterlogging)} 处易渍水监测点当前均处于蓝色关注等级以下，"
                 f"整体平稳。")
    L.append("")

    # 二、内涝态势
    L.append("## 二、内涝态势推演")
    L.append("")
    L.append("| 指标 | 数值 | 说明 |")
    L.append("| --- | ---: | --- |")
    L.append(f"| 淹没片区 | {imp['zone_count']} 处 | 按站点影响半径聚合 |")
    L.append(f"| 淹没面积 | {imp['inundated_area_km2']:.2f} km² | 占全市 8569 km² 的 "
             f"{imp['inundated_area_km2'] / 8569 * 100:.2f}% |")
    L.append(f"| 最大积水深度 | {imp['max_depth_cm']:.1f} cm | 等级：{_level(imp['max_depth_cm'])} |")
    L.append(f"| 平均积水深度 | {imp['avg_depth_cm']:.1f} cm | 淹没区内部均值 |")
    L.append(f"| 受淹建筑 | {imp['affected_building_count']} 栋 | 建筑面积 "
             f"{imp['affected_building_area_wan_m2']:.1f} 万 m² |")
    L.append(f"| 阻断道路 | {imp['blocked_road_count']} 段 | 合计 "
             f"{imp['blocked_road_length_km']:.1f} km |")
    L.append(f"| 受威胁避难场所 | {imp['threatened_shelter_count']} 处 | 位于淹没范围内 |")
    L.append(f"| 受影响重点单位 | {imp['threatened_key_poi_count']} 家 | 学校、医院、地铁口等 |")
    L.append("")

    # 三、风险点清单
    L.append("## 三、重点风险点")
    L.append("")
    L.append(f"### 3.1 积水最深处（情景 {rainfall_mm:.0f}mm/{duration_h:.0f}h）")
    L.append("")
    if top:
        L.append("| 序号 | 点位名称 | 行政区 | 积水深度 | 等级 | 联动泵站 |")
        L.append("| ---: | --- | --- | ---: | --- | --- |")
        for i, t in enumerate(top, 1):
            L.append(f"| {i} | {t['name']} | {t['district']} | "
                     f"{t['depth_cm']:.1f} cm | {_level(t['depth_cm'])} | {t['pump_name'] or '-'} |")
        L.append("")
        L.append(f"> 共 {len(scenario)} 处站点达到积水阈值，此处列出最深 5 处。")
    else:
        L.append("该情景下无站点达到积水阈值。")
    L.append("")

    if imp["threatened_shelters"]:
        L.append("### 3.2 位于淹没范围内的应急避难场所")
        L.append("")
        L.append("| 名称 | 行政区 | 容量 | 所在淹没区 | 水深 |")
        L.append("| --- | --- | ---: | --- | ---: |")
        for s in imp["threatened_shelters"][:8]:
            L.append(f"| {s['name']} | {s.get('district', '-')} | {s.get('capacity', 0)} 人 | "
                     f"{s.get('zone', '-')} | {s.get('depth_cm', 0):.1f} cm |")
        L.append("")

    if imp["threatened_poi"]:
        L.append("### 3.3 受影响重点单位（前 8 家）")
        L.append("")
        L.append("| 名称 | 类型 | 行政区 | 水深 |")
        L.append("| --- | --- | --- | ---: |")
        for p in imp["threatened_poi"][:8]:
            L.append(f"| {p['name']} | {p.get('poi_type', '-')} | {p.get('district', '-')} | "
                     f"{p.get('depth_cm', 0):.1f} cm |")
        L.append("")

    # 四、分区影响
    if breakdown:
        L.append("## 四、分区影响排序")
        L.append("")
        L.append("| 行政区 | 受淹建筑(栋) | 其中低洼(栋) | 小时雨强(mm/h) | 泵站能力(m³/s) |")
        L.append("| --- | ---: | ---: | ---: | ---: |")
        rain_map = {d["district"]: d for d in stats}
        for b in breakdown[:13]:
            r = rain_map.get(b["district"], {})
            L.append(f"| {b['district']} | {b['buildings']} | {b['low_elev']} | "
                     f"{r.get('rain_1h_mm', 0):.1f} | {r.get('pump_capacity_m3s', 0):.1f} |")
        L.append("")

    # 五、建议
    L.append("## 五、处置建议")
    L.append("")
    for i, s in enumerate(_suggestions(imp, meta, breakdown), 1):
        L.append(f"{i}. {s}")
    L.append("")

    # 六、说明
    L.append("---")
    L.append("")
    L.append("## 六、数据说明")
    L.append("")
    L.append(f"- **计算引擎**：{imp.get('engine', 'local')}（纯 Python 几何引擎 / PostGIS 双通道），"
             f"单次推演耗时 {imp.get('compute_ms', 0)} ms")
    L.append(f"- **数据来源**：行政区划（DataV）、建筑与路网（开放地图矢量，20.6 万栋 / "
             f"{len(store.roads)} 条）、湖泊水系（OpenStreetMap ODbL）、"
             f"实时雨情（Open-Meteo）、渍水点台账（公开易涝点清单 + 坐标吸附）")
    L.append(f"- **模型性质**：区级水量平衡 + 点位汇水经验模型，输出**趋势与相对排序**，"
             f"非水动力方程求解，淹没边界为包络近似，不作为工程定线依据")
    L.append(f"- **免责声明**：本简报由系统按当前设定自动生成，"
             f"仅用于防汛演练与态势预判；实际调度须以水文部门实测数据和会商结论为准")
    L.append("")
    L.append(f"*（本简报由武汉内涝二三维孪生平台自动生成 · {now.strftime('%Y-%m-%d %H:%M:%S')}）*")
    L.append("")

    md = "\n".join(L)
    return {
        "markdown": md,
        "filename": f"防汛会商简报_{scope}_{seq}.md",
        "generated_at": now.isoformat(timespec="seconds"),
        "query": {"rainfall_mm": rainfall_mm, "duration_hours": duration_h, "district": district},
        "summary": {
            "zone_count": imp["zone_count"],
            "inundated_area_km2": imp["inundated_area_km2"],
            "affected_building_count": imp["affected_building_count"],
            "blocked_road_count": imp["blocked_road_count"],
            "max_depth_cm": imp["max_depth_cm"],
            "threatened_shelter_count": imp["threatened_shelter_count"],
        },
    }


def markdown_to_html(md, title="武汉市防汛会商简报"):
    """极简 Markdown → HTML（只支持本模块产生的语法子集：标题/表格/列表/粗体/分隔线）。"""
    import html as _html
    import re

    lines = md.split("\n")
    out = []
    in_table = False
    in_ul = False

    def inline(t):
        t = _html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
        return t

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue  # 表头分隔行
            if not in_table:
                out.append("<table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False

        if not line.strip():
            if in_ul:
                out.append("</ul>")
                in_ul = False
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue

        if re.match(r"^---+$", line.strip()):
            out.append("<hr>")
            continue

        m = re.match(r"^(\d+)\.\s+(.*)$", line)
        if m:
            if not in_ul:
                out.append("<ol>")
                in_ul = "ol"
            out.append(f"<li>{inline(m.group(2))}</li>")
            continue

        m = re.match(r"^[-*]\s+(.*)$", line)
        if m:
            if in_ul != "ul":
                if in_ul:
                    out.append(f"</{in_ul}>")
                out.append("<ul>")
                in_ul = "ul"
            out.append(f"<li>{inline(m.group(1))}</li>")
            continue

        if in_ul:
            out.append(f"</{in_ul}>")
            in_ul = False
        out.append(f"<p>{inline(line)}</p>")

    if in_table:
        out.append("</table>")
    if in_ul:
        out.append(f"</{in_ul}>")

    body = "\n".join(out)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{_html.escape(title)}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  body {{ font-family: "Microsoft YaHei", "PingFang SC", serif; color: #1a1a1a;
         line-height: 1.75; max-width: 820px; margin: 0 auto; padding: 24px 16px; background: #fff; }}
  h1 {{ font-size: 24px; text-align: center; letter-spacing: 2px; margin: 8px 0 4px; }}
  h2 {{ font-size: 17px; border-left: 4px solid #c0392b; padding-left: 10px; margin: 26px 0 10px; }}
  h3 {{ font-size: 14px; color: #444; margin: 18px 0 8px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; margin: 10px 0 16px; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; }}
  th {{ background: #f2f4f7; font-weight: 600; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 22px 0; }}
  code {{ background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 12px; }}
  p, li {{ font-size: 13.5px; }}
  strong {{ color: #111; }}
  em {{ color: #888; font-style: normal; font-size: 12px; }}
</style></head><body>
{body}
</body></html>"""
