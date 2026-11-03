#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""00_fetch_districts.py —— 下载武汉市 13 个行政区划边界（阿里云 DataV.GeoAtlas，免 Key）"""
import json
import os
import urllib.request

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "processed")
URL = "https://geo.datav.aliyun.com/areas_v3/bound/420100_full.json"


def main():
    os.makedirs(OUT, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=40).read()
    obj = json.loads(data.decode("utf-8"))
    path = os.path.join(OUT, "wuhan_districts.geojson")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, separators=(",", ":"))
    names = [f["properties"]["name"] for f in obj["features"]]
    print(f"✅ 已下载 {len(names)} 个行政区 -> {path}")
    print("   " + "、".join(names))


if __name__ == "__main__":
    main()
