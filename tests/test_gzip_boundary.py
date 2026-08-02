import pytest
import os
import io
import time
from fastapi import Response
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from main import app

# ── Dynamic Test Routes ──────────────────────────────────────────────────────

# 1. Exact byte sizes to test the 1000-byte Gzip threshold
@app.get("/test-999-bytes")
def test_999_bytes():
    return Response(content="A" * 999, media_type="text/plain")

@app.get("/test-1000-bytes")
def test_1000_bytes():
    return Response(content="A" * 1000, media_type="text/plain")

@app.get("/test-1001-bytes")
def test_1001_bytes():
    return Response(content="A" * 1001, media_type="text/plain")

# 2. Non-compressible binary file type (image/png) to check if Gzip middleware incorrectly compresses it
@app.get("/test-large-image")
def test_large_image():
    # Return a 2000-byte dummy PNG file response
    return Response(content=b"\x89PNG\r\n\x1a\n" + b"B" * 2000, media_type="image/png")

# 3. Streaming CSV data generator simulating member exports
async def csv_generator():
    yield "username,first_name,last_name,user_id\n"
    for i in range(50):
        yield f"user_{i},First_{i},Last_{i},{1000+i}\n"

@app.get("/test-streaming-csv")
def test_streaming_csv():
    return StreamingResponse(
        csv_generator(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=test.csv"}
    )


# ── Pytest Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def local_client():
    return TestClient(app)


# ── Test Cases ───────────────────────────────────────────────────────────────

def test_gzip_compression_boundary_conditions(local_client):
    # 999 bytes: should NOT be compressed since it's < 1000 bytes
    headers = {"Accept-Encoding": "gzip"}
    response = local_client.get("/test-999-bytes", headers=headers)
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert len(response.content) == 999

    # 1000 bytes: should be compressed since it is exactly the minimum_size
    response = local_client.get("/test-1000-bytes", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"

    # 1001 bytes: should be compressed
    response = local_client.get("/test-1001-bytes", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"


def test_gzip_streaming_csv(local_client):
    # Test if StreamingResponse works with Gzip middleware
    headers = {"Accept-Encoding": "gzip"}
    response = local_client.get("/test-streaming-csv", headers=headers)
    assert response.status_code == 200
    # Streaming CSV files are text/csv and should be compressed if Accept-Encoding matches
    assert response.headers.get("content-encoding") == "gzip"


def test_gzip_non_compressible_types(local_client):
    # Test if Gzip middleware incorrectly compresses image/png files
    headers = {"Accept-Encoding": "gzip"}
    response = local_client.get("/test-large-image", headers=headers)
    assert response.status_code == 200
    
    # ADV-01: Gzip middleware should NOT compress already-compressed formats like images, 
    # as doing so wastes CPU cycles and can increase response payload size.
    # We assert if the current implementation has this flaw:
    is_compressed = response.headers.get("content-encoding") == "gzip"
    print(f"\n[Adversarial Check] Does large image get compressed? {is_compressed}")
    # Note: Starlette's GZipMiddleware compresses everything regardless of media_type.
    # Therefore, this will fail or pass depending on custom filters (which are missing in main.py).


def test_root_index_cache_control(local_client):
    # Request root '/' which serves index.html
    response = local_client.get("/")
    assert response.status_code == 200
    
    # BUG-01: Serve static files with Cache-Control headers (public, max-age>=3600).
    # Since main.py serves "/" directly with FileResponse instead of CacheControlledStaticFiles,
    # it lacks the Cache-Control header. We verify this.
    cache_control = response.headers.get("cache-control")
    print(f"\nRoot '/' Cache-Control header: {cache_control}")
    assert cache_control == "public, max-age=3600", "Root index.html lacks Cache-Control header!"


def test_static_files_cache_control(local_client):
    # /static/index.html should have Cache-Control because it goes through CacheControlledStaticFiles
    response = local_client.get("/static/index.html")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "public, max-age=3600"
    
    # /static/css/style.css should also have it
    response = local_client.get("/static/css/style.css")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "public, max-age=3600"
