"""统一启动器：选运行时 → 起服务 → 端口就绪后自动打开浏览器。

被 start.bat / start.sh 调用，也可直接运行：
    python scripts/launch.py [--port 8000] [--no-browser]

设计目的：把「判断环境、挑运行时、等服务起来、开页面」这些逻辑放进 Python，
避免 BAT 里写复杂的中文/嵌套引号逻辑（Windows cmd 按 GBK 解析，UTF-8 的 bat 会闪退）。
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def wait_port(port: int, timeout: float = 90.0, proc: subprocess.Popen | None = None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        if proc is not None and proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def has_fastapi() -> bool:
    return all(importlib.util.find_spec(m) for m in ("fastapi", "uvicorn"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    port = args.port

    print("=" * 52)
    print("  江汉流域防汛与武汉内涝二三维孪生平台")
    print("=" * 52)

    # 已运行：不重复启动，直接打开页面
    if port_open(port):
        url = f"http://localhost:{port}"
        print(f"\n[提示] 端口 {port} 已有服务在运行，直接打开 {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    # 检查必要的构建产物
    if not os.path.exists(os.path.join(ROOT, "data", "processed", "meta.json")):
        print("\n[1/3] 未发现预处理数据，正在生成（约 2 分钟）...")
        rc = subprocess.call([sys.executable, os.path.join(ROOT, "scripts", "01_prepare_data.py")], cwd=ROOT)
        if rc != 0:
            print("[失败] 数据预处理出错，请查看上方日志。")
            return rc
    else:
        print("\n[1/3] 数据就绪")

    dist = os.path.join(ROOT, "frontend", "dist", "index.html")
    if not os.path.exists(dist):
        print("[2/3] 前端未构建，请先执行：cd frontend && npm install && npm run build")
        print("      或使用开发模式：cd frontend && npm run dev（5173 端口，已配 API 代理）")
        return 1
    print("[2/3] 前端构建产物就绪")

    # 选择运行时
    if has_fastapi():
        print("[3/3] 运行时：FastAPI 生产入口")
        cmd = [sys.executable, "-m", "uvicorn", "backend.main:app",
               "--host", "0.0.0.0", "--port", str(port)]
    else:
        print("[3/3] 运行时：零依赖服务器（未检测到 FastAPI，功能完全一致）")
        cmd = [sys.executable, os.path.join(ROOT, "backend", "dev_server.py"), "--port", str(port)]

    url = f"http://localhost:{port}"
    print(f"\n  访问地址：{url}")
    print(f"  API 文档：{url}/docs" if has_fastapi() else "")
    print("  关闭服务：Ctrl+C\n")
    print("-" * 52)

    proc = subprocess.Popen(cmd, cwd=ROOT)
    ready = wait_port(port, timeout=120, proc=proc)
    if ready and not args.no_browser:
        # 等首屏静态资源就绪再开，避免浏览器显示连接失败
        def _open():
            time.sleep(1.0)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()
        print(f"[OK] 服务已就绪，浏览器正在打开 {url}")
    elif not ready and proc.poll() is not None:
        print("[失败] 服务启动异常退出，请查看上方日志（常见：端口被占用、依赖缺失）。")
        return proc.returncode or 1

    try:
        return proc.wait()
    except KeyboardInterrupt:
        print("\n[停止] 正在关闭服务...")
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0


if __name__ == "__main__":
    sys.exit(main())
