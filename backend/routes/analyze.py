"""Research pipeline API route."""

import asyncio
import os

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from agent_pipeline import run_pipeline
from events import emit_event
from openai_agents import InputPreflightResult, ResearchAgents

router = APIRouter()
preflight_agents = ResearchAgents()

# 개별 단계에는 타임아웃이 있어도, 애매한/무의미한 입력이 여러 selected_topics로
# 쪼개지면 그 타임아웃들이 누적돼서 사실상 무한정 돌 수 있다. 전체 파이프라인에
# 상한선을 하나 더 걸어서 절대 안 끝나는 상황 자체를 막는다.
PIPELINE_TIMEOUT_S = float(os.environ.get("PIPELINE_TIMEOUT_S", "100"))


class TopicRequest(BaseModel):
    topic: str

    @field_validator("topic")
    @classmethod
    def topic_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("topic은 빈 문자열일 수 없습니다.")
        return v


class AnalyzeRequest(TopicRequest):
    scholar_query: str | None = None
    adoption_queries: list[str] | None = None
    max_results: int = Field(default=10, ge=1, le=20)


class PreflightFailure(RuntimeError):
    """The cheap input gate failed, so the expensive pipeline must not run."""


async def _run_preflight(topic: str) -> InputPreflightResult:
    emit_event(
        "note",
        {"text": "입력 사전 검사: 오타 보정 가능 여부와 주제 유효성을 확인합니다."},
        stage="input_preflight",
        source="system",
    )
    try:
        result = await preflight_agents.preflight(topic)
        if result.status == "rejected":
            result.resolved_topic = ""
            result.message = "추천 검색어가 없어요. 검색어를 다시 확인해 주세요."
            result.recommendations = []
        elif not result.resolved_topic.strip() or not result.recommendations:
            raise ValueError("rejected가 아닌 입력에는 분석 주제와 추천 검색어가 필요합니다.")
    except Exception as exc:
        emit_event(
            "error",
            {"name": "input_preflight", "topic": topic, "message": str(exc)},
            stage="input_preflight",
            source="system",
        )
        raise PreflightFailure(str(exc)) from exc

    if result.status == "rejected":
        text = "추천 검색어를 만들 수 없어 분석을 중단합니다."
    elif result.status == "auto_corrected":
        text = "오타를 보정하고 추천 검색어와 함께 파이프라인을 진행합니다."
    elif result.status == "needs_calibration":
        text = "유효한 주제지만 구체화가 필요해 추천 검색어를 반환합니다."
    else:
        text = "입력 주제에 대한 추천 검색어를 생성했습니다."
    emit_event(
        "note",
        {"text": text, "status": result.status, "recommendations": result.recommendations},
        stage="input_preflight",
        source="system",
    )
    return result


def _preflight_response(result: InputPreflightResult) -> dict:
    return {
        "status": result.status,
        "topic": result.original_topic,
        "resolved_topic": result.resolved_topic,
        "reason_code": result.reason_code,
        "message": result.message,
        "recommendations": result.recommendations,
        "preflight": result.model_dump(),
    }


@router.post("/api/suggestions")
async def suggestions(request: TopicRequest):
    try:
        result = await _run_preflight(request.topic)
        return _preflight_response(result)
    except PreflightFailure as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": "input_preflight_failed",
                "message": str(exc),
            },
        )


@router.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    resolved_topic = request.topic
    try:
        preflight = await _run_preflight(request.topic)
        if preflight.status in {"rejected", "needs_calibration"}:
            return _preflight_response(preflight)

        resolved_topic = preflight.resolved_topic
        result = await asyncio.wait_for(
            run_pipeline(
                resolved_topic,
                scholar_query=request.scholar_query,
                adoption_queries=request.adoption_queries,
                max_results=request.max_results,
            ),
            timeout=PIPELINE_TIMEOUT_S,
        )
        result.update(
            {
                "status": "completed",
                "input_topic": request.topic,
                "recommendations": preflight.recommendations,
                "preflight": preflight.model_dump(),
            }
        )
        return result

    except (asyncio.TimeoutError, TimeoutError):
        emit_event(
            "error",
            {"name": "analyze", "topic": resolved_topic, "reason": "pipeline_timeout", "timeout_s": PIPELINE_TIMEOUT_S},
            stage="pipeline",
            source="system",
        )
        return JSONResponse(
            status_code=504,
            content={
                "error": "pipeline_timeout",
                "message": f"분석이 {int(PIPELINE_TIMEOUT_S)}초를 넘겨 중단되었습니다. 더 구체적인 기술/방법론으로 다시 시도해주세요.",
            },
        )
    except PreflightFailure as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": "input_preflight_failed",
                "message": str(exc),
            },
        )
    except Exception as exc:
        emit_event(
            "error",
            {"name": "analyze", "topic": resolved_topic, "message": str(exc)},
            stage="pipeline",
            source="system",
        )
        return JSONResponse(
            status_code=500,
            content={"error": "analyze_failed", "message": str(exc)},
        )
