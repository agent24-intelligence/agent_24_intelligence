"""Research pipeline API route."""

import asyncio
import os

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from agent_pipeline import run_pipeline
from events import emit_event

router = APIRouter()

# 개별 단계에는 타임아웃이 있어도, 애매한/무의미한 입력이 여러 selected_topics로
# 쪼개지면 그 타임아웃들이 누적돼서 사실상 무한정 돌 수 있다. 전체 파이프라인에
# 상한선을 하나 더 걸어서 절대 안 끝나는 상황 자체를 막는다.
PIPELINE_TIMEOUT_S = float(os.environ.get("PIPELINE_TIMEOUT_S", "100"))


class AnalyzeRequest(BaseModel):
    topic: str
    scholar_query: str | None = None
    adoption_queries: list[str] | None = None
    max_results: int = Field(default=10, ge=1, le=20)

    @field_validator("topic")
    @classmethod
    def topic_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("topic은 빈 문자열일 수 없습니다.")
        return v


@router.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    try:
        return await asyncio.wait_for(
            run_pipeline(
                request.topic,
                scholar_query=request.scholar_query,
                adoption_queries=request.adoption_queries,
                max_results=request.max_results,
            ),
            timeout=PIPELINE_TIMEOUT_S,
        )
    except (asyncio.TimeoutError, TimeoutError):
        emit_event(
            "error",
            {"name": "analyze", "topic": request.topic, "reason": "pipeline_timeout", "timeout_s": PIPELINE_TIMEOUT_S},
            stage="pipeline",
            source="system",
        )
        return JSONResponse(
            status_code=504,
            content={
                "error": "pipeline_timeout",
                "message": f"분석이 {int(PIPELINE_TIMEOUT_S)}초 넘게 걸려서 중단했어요. 더 구체적인 기술/방법론으로 다시 시도해주세요.",
            },
        )
    except Exception as exc:
        emit_event(
            "error",
            {"name": "analyze", "topic": request.topic, "message": str(exc)},
            stage="pipeline",
            source="system",
        )
        return JSONResponse(
            status_code=500,
            content={"error": "analyze_failed", "message": str(exc)},
        )
