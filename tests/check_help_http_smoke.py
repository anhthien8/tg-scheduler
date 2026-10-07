"""Smoke test: boot the real app on a non-production port (8899, TG_RUNTIME_MODE=web)
and hit GET /api/help over real HTTP, exactly like acceptance asks — without touching
port 8888 (production) or the combined-mode DATA_DIR singleton lock.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

PORT = 8899
os.environ["HOST"] = "127.0.0.1"
os.environ["PORT"] = str(PORT)
os.environ["TG_RUNTIME_MODE"] = "web"       # no Telethon/engines, no DATA_DIR lock
os.environ["TG_SKIP_ENGINES"] = "1"
os.environ["DASHBOARD_SECRET_KEY"] = "builder-test-key-123"
os.environ["DISABLE_AUTH"] = "0"

import uvicorn  # noqa: E402

import main  # noqa: E402

server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning"))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
try:
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("server did not start in time")

    base = f"http://127.0.0.1:{PORT}"

    try:
        urllib.request.urlopen(f"{base}/api/help", timeout=5)
        raise AssertionError("expected 403 without X-API-Key")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403, f"expected 403, got {exc.code}"

    req = urllib.request.Request(f"{base}/api/help", headers={"X-API-Key": "builder-test-key-123"})
    with urllib.request.urlopen(req, timeout=5) as res:
        assert res.status == 200, f"expected 200, got {res.status}"
        data = json.loads(res.read().decode("utf-8"))
    assert "## 1. Giới thiệu" in data["content"]
    assert len(data["toc"]) == 56

    print(json.dumps({"port": PORT, "no_key_status": 403, "with_key_status": 200,
                       "toc_entries": len(data["toc"])}, ensure_ascii=False))
    print("OK")
finally:
    server.should_exit = True
    thread.join(timeout=5)
