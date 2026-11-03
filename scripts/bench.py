"""并发压测脚本（标准库实现，零依赖）。

用法：
    python scripts/bench.py                      # 默认 http://127.0.0.1:8000
    python scripts/bench.py --concurrency 16 --requests 200
    python scripts/bench.py --markdown           # 输出可直接粘进 README 的表格

统计口径：
  · 每个接口独立按 concurrency 个线程打 requests 次，不计网络抖动以外的客户端开销
  · P95 为 95 分位延迟；QPS = 成功请求数 / 实际墙钟耗时
  · 错误率统计非 2xx 与超时（默认 30s）
"""
import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

DEFAULT_CASES = [
    # (名称, 方法, 路径, body, 超时秒)
    ("健康检查", "GET", "/api/v1/health", None, 10),
    ("站点列表(60点)", "GET", "/api/v1/monitor/stations", None, 15),
    ("建筑图层(视域)", "GET", "/api/v1/layers/buildings?bbox=114.33,30.53,114.36,30.56", None, 30),
    ("道路图层(视域)", "GET", "/api/v1/layers/roads?bbox=114.33,30.53,114.36,30.56", None, 30),
    ("内涝灾情推演", "POST", "/api/v1/analysis/inundation-impact",
     {"rainfall_mm": 120, "duration_hours": 3}, 60),
    ("避险路径规划", "POST", "/api/v1/analysis/evacuation-route",
     {"origin_lng": 114.3436, "origin_lat": 30.5494}, 60),
    ("缓冲分析(500m)", "GET", "/api/v1/layers/buffer?lng=114.3436&lat=30.5494&radius_m=500", None, 30),
]


def one_request(base, method, path, body, timeout):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return (time.perf_counter() - t0) * 1000.0, r.status
    except urllib.error.HTTPError as e:
        return (time.perf_counter() - t0) * 1000.0, e.code
    except Exception:
        return (time.perf_counter() - t0) * 1000.0, 0


def run_case(base, name, method, path, body, timeout, concurrency, total):
    lat, errs = [], 0
    lock = threading.Lock()
    idx = {"i": 0}

    def worker():
        nonlocal errs
        while True:
            with lock:
                if idx["i"] >= total:
                    return
                idx["i"] += 1
            ms, code = one_request(base, method, path, body, timeout)
            with lock:
                if 200 <= code < 300:
                    lat.append(ms)
                else:
                    errs += 1

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.perf_counter() - t0

    if not lat:
        return {"name": name, "ok": 0, "err": errs, "qps": 0, "p50": 0, "p95": 0, "p99": 0, "wall": wall}
    lat.sort()

    def pct(p):
        k = min(len(lat) - 1, int(round(p / 100.0 * (len(lat) - 1))))
        return lat[k]

    return {
        "name": name, "ok": len(lat), "err": errs,
        "qps": len(lat) / wall if wall > 0 else 0,
        "p50": pct(50), "p95": pct(95), "p99": pct(99),
        "mean": statistics.fmean(lat), "wall": wall,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--requests", type=int, default=40)
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    # 预热：避免首次请求把数据加载/连接建立算进延迟
    print(f"预热 {args.base} ...", flush=True)
    for _ in range(3):
        one_request(args.base, "GET", "/api/v1/health", None, 15)
    print(f"并发 {args.concurrency} · 每接口 {args.requests} 次\n", flush=True)

    rows = []
    for name, method, path, body, timeout in DEFAULT_CASES:
        r = run_case(args.base, name, method, path, body, timeout,
                     args.concurrency, args.requests)
        rows.append(r)
        print(f"{name:<20} QPS {r['qps']:7.1f}  P50 {r['p50']:7.0f}ms  "
              f"P95 {r['p95']:7.0f}ms  P99 {r['p99']:7.0f}ms  错误 {r['err']}",
              flush=True)

    total_ok = sum(r["ok"] for r in rows)
    total_err = sum(r["err"] for r in rows)
    print(f"\n合计成功 {total_ok} 次，失败 {total_err} 次，错误率 "
          f"{total_err / max(1, total_ok + total_err) * 100:.2f}%")

    if args.markdown:
        print("\n| 接口 | QPS | P50 | P95 | P99 | 错误 |")
        print("| --- | ---: | ---: | ---: | ---: | ---: |")
        for r in rows:
            print(f"| {r['name']} | {r['qps']:.1f} | {r['p50']:.0f} ms | "
                  f"{r['p95']:.0f} ms | {r['p99']:.0f} ms | {r['err']} |")
        print(f"\n> 压测参数：并发 {args.concurrency}，每接口 {args.requests} 次请求，"
              f"单机单进程，数据全量加载于内存。")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"concurrency": args.concurrency, "requests": args.requests,
                       "cases": rows}, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.json_out}")

    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
