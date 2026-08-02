import pytest
from fastapi.testclient import TestClient
from main import app

pytestmark = pytest.mark.asyncio

async def test_gzip_compression_large_response(client):
    # index.html is > 1000 bytes. Requesting '/' with Accept-Encoding: gzip
    headers = {"Accept-Encoding": "gzip"}
    response = client.get("/", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"

async def test_gzip_compression_large_static_file(client):
    # index.html served directly under /static
    headers = {"Accept-Encoding": "gzip"}
    response = client.get("/static/index.html", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"

async def test_gzip_compression_small_response(client):
    # A small response (like /api/auth/status) should not be compressed
    headers = {"Accept-Encoding": "gzip"}
    response = client.get("/api/auth/status", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("content-encoding") != "gzip"

async def test_static_files_cache_control(client):
    # Request a static file from /static
    response = client.get("/static/index.html")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "public, max-age=3600"
    
    # Request another static file (e.g. style.css)
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "public, max-age=3600"

async def test_static_files_not_found(client):
    # Request a non-existent static file
    response = client.get("/static/non_existent_file.html")
    assert response.status_code == 404
    # The Cache-Control header should NOT be added or set for 404
    assert response.headers.get("cache-control") != "public, max-age=3600"
