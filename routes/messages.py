"""
Message management and file upload routes.
"""
import os
import uuid
import shutil
from fastapi import APIRouter, UploadFile, File, HTTPException

router = APIRouter(prefix="/api", tags=["messages"])

MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "media")
os.makedirs(MEDIA_DIR, exist_ok=True)

# HIGH-02: security limits for file uploads
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",   # images
    ".mp4", ".mov", ".avi", ".mkv", ".webm",    # videos
    ".pdf", ".zip", ".doc", ".docx", ".txt",    # documents
    ".mp3", ".ogg", ".wav",                     # audio
}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a media file (photo, video, document)."""
    # HIGH-02: validate extension before reading full content
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{ext}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )

    # HIGH-02: read content and enforce size limit
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) // (1024*1024)} MB). Maximum is {MAX_UPLOAD_BYTES // (1024*1024)} MB."
        )

    # Generate unique filename
    unique_name = f"{uuid.uuid4().hex[:12]}{ext}"
    file_path = os.path.join(MEDIA_DIR, unique_name)

    try:
        with open(file_path, "wb") as f:
            f.write(content)

        return {
            "success": True,
            "filename": unique_name,
            "original_name": file.filename,
            "path": file_path,
            "size": len(content)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

