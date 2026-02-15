from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
EXTRACTION_SERVICE_URL = os.getenv("EXTRACTION_SERVICE_URL", "http://extraction-service:8001")

app = FastAPI(title="Seocho Custom Chat Platform")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


async def _proxy(method: str, path: str, payload: Dict[str, Any] | None = None):
    url = f"{EXTRACTION_SERVICE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.request(method, url, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}") from exc

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = {"error": resp.text}
        raise HTTPException(status_code=resp.status_code, detail=detail)

    try:
        return resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Invalid upstream JSON: {exc}") from exc


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "custom-chat-platform"}


@app.get("/api/config")
async def api_config():
    databases = []
    try:
        payload = await _proxy("GET", "/databases")
        databases = payload.get("databases", [])
    except HTTPException:
        databases = []
    return {
        "api_base": EXTRACTION_SERVICE_URL,
        "default_mode": "semantic",
        "databases": databases,
    }


@app.post("/api/chat/send")
async def api_chat_send(request: Request):
    payload = await request.json()
    data = await _proxy("POST", "/platform/chat/send", payload=payload)
    return JSONResponse(content=data)


@app.get("/api/chat/session/{session_id}")
async def api_chat_session(session_id: str):
    data = await _proxy("GET", f"/platform/chat/session/{session_id}")
    return JSONResponse(content=data)


@app.delete("/api/chat/session/{session_id}")
async def api_chat_session_reset(session_id: str):
    data = await _proxy("DELETE", f"/platform/chat/session/{session_id}")
    return JSONResponse(content=data)

