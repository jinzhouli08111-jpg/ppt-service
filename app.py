# -*- coding: utf-8 -*-
"""PPT Master 转换服务

POST /api/build  → 生成 pptx 并直接返回二进制
GET  /api/health → 健康检查（Render 用）
GET  /           → 服务说明
"""
from __future__ import annotations

import io
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, PlainTextResponse

import pipeline

app = FastAPI(title="PPT Master Service", version="1.0")

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
SERVICE_TOKEN = os.environ.get("SERVICE_TOKEN", "")   # 可选：简单鉴权


@app.get("/", response_class=PlainTextResponse)
def index():
    return (
        "PPT Master Service\n\n"
        "POST /api/build\n"
        "  { topic, audience, pages, style, outline? }\n"
        "  -> application/vnd.openxmlformats-officedocument.presentationml.presentation\n\n"
        "GET /api/health\n"
    )


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "has_key": bool(API_KEY),
        "skill": os.path.isdir(os.path.join(os.path.dirname(__file__), "skill", "scripts")),
    }


@app.post("/api/build")
async def build(request: Request):
    # 可选鉴权：配了 SERVICE_TOKEN 就校验
    if SERVICE_TOKEN:
        token = request.headers.get("x-service-token", "")
        if token != SERVICE_TOKEN:
            return JSONResponse({"error": "未授权"}, status_code=401)

    if not API_KEY:
        return JSONResponse({"error": "服务端未配置 DEEPSEEK_API_KEY"}, status_code=500)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体不是合法 JSON"}, status_code=400)

    topic = str(body.get("topic") or "").strip()
    outline = body.get("outline")
    if not topic and not outline:
        return JSONResponse({"error": "缺少 topic 或 outline"}, status_code=400)

    audience = str(body.get("audience") or "普通读者").strip()
    style = str(body.get("style") or "old-money").strip()
    extra = str(body.get("extra") or "").strip()

    try:
        pages = int(body.get("pages") or 8)
    except Exception:
        pages = 8
    pages = max(1, min(20, pages))

    try:
        data, meta = pipeline.build_pptx(
            topic=topic, audience=audience, pages=pages,
            style=style, api_key=API_KEY,
            outline=outline, extra=extra,
        )
    except pipeline.PipelineError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return JSONResponse({"error": "服务异常：" + str(e)}, status_code=500)

    safe = "".join(c for c in (meta["title"] or "deck") if c not in '\\/:*?"<>|')[:40] or "deck"
    # HTTP 头只能用 latin-1，中文标题必须 URL 编码后传递
    from urllib.parse import quote
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": f'attachment; filename="deck.pptx"',
            "X-Deck-Title": quote(safe),
            "X-Deck-Pages": str(meta["pages"]),
            "X-Deck-Seconds": str(meta["runs"]),
            "X-Deck-Checker": "ok" if meta["checker_ok"] else "warn",
            "X-Deck-Fallback": ",".join(str(x) for x in meta["fallback_pages"]) or "none",
        },
    )
