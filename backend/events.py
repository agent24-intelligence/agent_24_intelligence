"""
전역 이벤트 브로드캐스터.

파이프라인 코드는 이 모듈의 emit_event()만 호출하면 된다.
Raw API Stream 화면(SSE)이 구독 중인 모든 클라이언트에게 그대로, 가공 없이 전달된다.

설계 원칙(AGENTS.md 참고):
- 이벤트는 무조건 그대로 흘려보낸다. 여기서 필드를 걸러내거나 재해석하지 않는다.
- 알 수 없는 타입이 와도 죽지 않는다. type/payload만 있으면 통과시킨다.
- 실패도 이벤트로 흘려보낸다(type="error"). 조용히 삼키지 않는다.
"""

import asyncio
import itertools
import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HISTORY_LIMIT = 200  # 새로 접속한 세컨드 화면이 최근 흐름을 바로 볼 수 있도록 최근 N개 보관
_DEFAULT_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "raw_api_stream.jsonl"
_history: deque = deque(maxlen=_HISTORY_LIMIT)
_subscribers: dict[int, asyncio.Queue] = {}
_next_id = itertools.count()


def emit_event(type: str, payload: Any = None, stage: str | None = None, source: str | None = None) -> dict:
    """이벤트 한 건을 만들어 모든 구독자에게 브로드캐스트한다.

    type: 원본 API/파이프라인이 쓰는 이벤트 이름 그대로 (예: tool_call, tool_result,
          text-delta, reasoning-delta, data-search-chunks, error, note, 처음 보는 이름도 그대로 허용)
    payload: 원본 응답/데이터를 가공하지 않고 그대로 넣는다.
    stage: 파이프라인 단계 이름 (예: scholar_scout). 없으면 None.
    source: liner / openai / system / manual_test 등. 없으면 None.
    """
    event = {
        "id": next(_next_id),
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "source": source,
        "type": type,
        "payload": payload,
    }
    _history.append(event)
    _append_event_log(event)
    for queue in list(_subscribers.values()):
        # 큐가 꽉 차서 못 넣더라도 스트림 전체가 죽으면 안 되므로 무시하고 계속 진행
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event


async def subscribe():
    """새 구독자를 등록하고 (최근 히스토리 스냅샷, 실시간 큐)를 반환한다."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    sub_id = next(_next_id)
    _subscribers[sub_id] = queue
    return sub_id, list(_history), queue


def unsubscribe(sub_id: int) -> None:
    _subscribers.pop(sub_id, None)


def _append_event_log(event: dict) -> None:
    """Raw API Stream 이벤트를 파일에도 남긴다.

    기본 경로: logs/raw_api_stream.jsonl
    변경: RAW_API_STREAM_LOG_PATH 환경변수
    """
    log_path = Path(os.environ.get("RAW_API_STREAM_LOG_PATH") or _DEFAULT_LOG_PATH)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:
        print(f"raw api stream log write failed: {exc}", flush=True)
