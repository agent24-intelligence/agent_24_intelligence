"""
Raw API Stream이 붙는 SSE 엔드포인트.

GET /api/stream 에 접속하면 지금까지의 최근 이벤트(히스토리) + 이후 실시간 이벤트를
가공 없이 순서대로 text/event-stream으로 흘려보낸다.
"""

import asyncio
import json

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from events import clear_history, subscribe, unsubscribe

router = APIRouter()

_HEARTBEAT_SECONDS = 15


@router.get("/api/stream")
async def stream(request: Request):
    sub_id, history, queue = await subscribe()

    async def event_source():
        try:
            for event in history:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    # 연결 유지용 heartbeat. 데이터 이벤트가 아니므로 프론트에서 무시된다.
                    yield ": heartbeat\n\n"
        finally:
            unsubscribe(sub_id)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 프록시 버퍼링 방지 (nginx 등 뒤에 있을 경우 대비)
        },
    )


@router.delete("/api/stream/history")
async def delete_stream_history():
    clear_history()
    return {"ok": True}
