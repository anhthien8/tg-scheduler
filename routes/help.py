"""Read-only API for the bundled user guide."""
import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/help", tags=["Help"])
_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


@lru_cache(maxsize=1)
def _load_help() -> dict:
    content_path = _DOCS_DIR / "help-content.md"
    toc_path = _DOCS_DIR / "help-toc.json"
    missing = [path.name for path in (content_path, toc_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(missing))

    content = content_path.read_text(encoding="utf-8")
    toc = json.loads(toc_path.read_text(encoding="utf-8"))
    if not isinstance(toc, list):
        raise ValueError("help-toc.json must contain a JSON array")
    return {"content": content, "toc": toc}


@router.get("")
async def get_help():
    try:
        return _load_help()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Không thể tải tài liệu hướng dẫn: {exc}",
        ) from exc
