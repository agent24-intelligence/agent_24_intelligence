"""
API 테스트 지원용 프록시.

프론트에서 API 키를 들고 있지 않아도 되도록, 서버가 .env의 키로 대신 요청을 보내고
받은 응답을 가공 없이 그대로 돌려준다. 요청/응답은 동시에 Raw API Stream 이벤트로도
브로드캐스트되므로, 수동 테스트 호출도 세컨드 화면에서 그대로 보인다.

용도: Liner/OpenAI 엔드포인트의 실제 응답 스키마·SSE 이벤트 이름을 코드 안 짜고 바로 확인.
"""

import os

import httpx
from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, StreamingResponse

from events import emit_event

router = APIRouter()

_TARGETS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "auth_header": lambda: {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"},
    },
    "liner": {
        "base_url": os.environ.get("LINER_API_BASE_URL", "https://platform.liner.com"),
        # Liner는 Authorization: Bearer가 아니라 x-api-key 헤더를 씀 (공식 문서 기준)
        "auth_header": lambda: {"x-api-key": os.environ.get("LINER_API_KEY", "")},
    },
}


@router.post("/api/proxy/{target}")
async def proxy(target: str, request: Request):
    if target not in _TARGETS:
        return JSONResponse(status_code=400, content={"error": f"unknown target '{target}', use one of {list(_TARGETS)}"})

    body = await request.json()
    method = body.get("method", "POST").upper()
    path = body.get("path", "")
    extra_headers = body.get("headers") or {}
    json_body = body.get("json")

    cfg = _TARGETS[target]
    url = cfg["base_url"].rstrip("/") + "/" + path.lstrip("/")
    headers = {**cfg["auth_header"](), "Content-Type": "application/json", **extra_headers}

    call_event = emit_event(
        "tool_call",
        {"target": target, "method": method, "url": url, "body": json_body},
        source="manual_test",
    )

    try:
        client = httpx.AsyncClient(timeout=90.0)
        req = client.build_request(method, url, headers=headers, json=json_body)
        resp = await client.send(req, stream=True)
    except Exception as exc:
        emit_event("error", {"target": target, "call_id": call_event["id"], "message": str(exc)}, source="manual_test")
        return JSONResponse(status_code=502, content={"error": str(exc)})

    content_type = resp.headers.get("content-type", "")

    if "text/event-stream" in content_type:
        async def relay():
            try:
                async for line in resp.aiter_lines():
                    emit_event("sse_line", {"target": target, "call_id": call_event["id"], "line": line}, source="manual_test")
                    yield line + "\n"
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(relay(), media_type="text/event-stream")

    raw_body = await resp.aread()
    await resp.aclose()
    await client.aclose()
    try:
        parsed = resp.json()
    except Exception:
        parsed = raw_body.decode("utf-8", errors="replace")

    emit_event(
        "tool_result",
        {"target": target, "call_id": call_event["id"], "status": resp.status_code, "body": parsed},
        source="manual_test",
    )

    return JSONResponse(status_code=200, content={"status": resp.status_code, "headers": dict(resp.headers), "body": parsed})
