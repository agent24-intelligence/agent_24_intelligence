"""Research pipeline API route."""

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from agent_pipeline import run_pipeline
from events import emit_event

router = APIRouter()


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
        return await run_pipeline(
            request.topic,
            scholar_query=request.scholar_query,
            adoption_queries=request.adoption_queries,
            max_results=request.max_results,
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
