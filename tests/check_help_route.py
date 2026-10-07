"""One-shot check: /api/help requires the API key and returns T1 content + toc."""
import json
import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

os.environ.setdefault("TG_SKIP_ENGINES", "1")
os.environ["DASHBOARD_SECRET_KEY"] = "test-help-key"
os.environ["DISABLE_AUTH"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

with TestClient(main.app) as client:
    no_key = client.get("/api/help")
    assert no_key.status_code == 403, f"expected 403 without key, got {no_key.status_code}"

    ok = client.get("/api/help", headers={"X-API-Key": "test-help-key"})
    assert ok.status_code == 200, f"expected 200 with key, got {ok.status_code} {ok.text[:200]}"
    data = ok.json()
    assert "## 1. Giới thiệu" in data["content"], "missing T1 heading in content"
    assert "## 17. Bảng thuật ngữ ngắn" in data["content"], "missing last T1 heading"
    assert len(data["toc"]) > 10, f"toc too small: {len(data['toc'])}"
    assert all({"title", "anchor", "level"} <= set(item) for item in data["toc"]), "toc item shape"

print(json.dumps({
    "status_without_key": no_key.status_code,
    "status_with_key": ok.status_code,
    "content_chars": len(data["content"]),
    "toc_entries": len(data["toc"]),
    "first_toc": data["toc"][0]["title"],
}, ensure_ascii=False))
print("OK")
