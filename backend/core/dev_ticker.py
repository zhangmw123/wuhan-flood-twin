"""沙盘时钟后台线程（FastAPI 启动时调用，dev_server 内置同名逻辑）。"""
import threading
import time

from backend.core.store import get_store


def _run():
    while True:
        try:
            get_store().tick(3.0)
        except Exception:
            pass
        time.sleep(1.0)


def start_ticker():
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
