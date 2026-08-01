"""Research pipeline API route."""

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from agent_pipeline import ResearchPipeline, build_deadline_result
from events import emit_event
from openai_agents import AgentBudgetTimeout, InputPreflightResult, ResearchAgents
from runtime_config import AnalysisDeadline, RuntimeConfig

router = APIRouter()
preflight_agents = ResearchAgents()

RUNTIME = RuntimeConfig()
ANALYZE_TIMEOUT_S = RUNTIME.total_timeout_s


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
    fast_mode: bool = False


class PreflightFailure(RuntimeError):
    """The cheap input gate failed, so the expensive pipeline must not run."""


_BROAD_TOPIC_RECOMMENDATIONS = [
    (
        {"ai", "인공지능", "artificial intelligence"},
        ["LLM 환각(hallucination) 탐지", "온디바이스 sLLM 양자화", "의료 영상 이상 탐지"],
    ),
    (
        {"llm", "large language model", "large language models", "거대 언어 모델", "거대언어모델", "대규모 언어 모델", "대규모언어모델"},
        ["LLM 환각(hallucination) 탐지", "온디바이스 sLLM 양자화", "LLM 프롬프트 인젝션 탐지"],
    ),
    (
        {"rag", "retrieval augmented generation", "retrieval-augmented generation"},
        ["RAG 파이프라인 캐싱 전략", "RAG 검색 결과 재랭킹", "멀티홉 RAG 질의 분해"],
    ),
    (
        {"gnn", "graph neural network", "graph neural networks", "그래프 신경망", "그래프 뉴럴넷"},
        ["GNN 기반 제품 추천 시스템", "GNN 기반 사기 거래 탐지", "지식 그래프 기반 링크 예측"],
    ),
    (
        {"diffusion model", "diffusion models", "확산 모델", "확산모델"},
        ["확산 모델 기반 영상 복원 기법", "확산 모델 기반 의료 영상 초해상도", "확산 모델 샘플링 가속 기법"],
    ),
]


def _normalize_topic_for_gate(value: str) -> str:
    normalized = value.casefold().strip()
    normalized = normalized.replace("(", " ").replace(")", " ").replace("-", " ")
    return " ".join(normalized.split())


def _calibrate_broad_resolved_topic(result: InputPreflightResult) -> InputPreflightResult:
    """Catch broad topics even when the model first corrected a typo."""
    if result.status == "rejected":
        return result
    resolved = _normalize_topic_for_gate(result.resolved_topic or result.original_topic)
    for aliases, recommendations in _BROAD_TOPIC_RECOMMENDATIONS:
        if resolved in aliases:
            result.status = "needs_calibration"
            result.reason_code = "too_broad"
            result.message = "입력하신 주제는 범위가 넓어요. 아래 추천 중 하나를 선택하거나 더 구체적으로 입력해 주세요."
            result.recommendations = recommendations
            return result
    return result


async def _run_preflight(topic: str, runtime: RuntimeConfig = RUNTIME) -> InputPreflightResult:
    emit_event(
        "note",
        {"text": "입력 사전 검사: 오타 보정 가능 여부와 주제 유효성을 확인합니다."},
        stage="input_preflight",
        source="system",
    )
    try:
        timeout_s = None if runtime.disable_timeouts else runtime.preflight_timeout_s
        result = await preflight_agents.preflight(topic, timeout_s=timeout_s)
        if result.status == "rejected":
            result.resolved_topic = ""
            result.message = "검색어를 다시 확인해 주세요."
            result.recommendations = []
        else:
            # 모델이 지시를 안 지켜서 resolved_topic/recommendations를 비워 반환하는
            # 경우가 실제로 있다. 이건 전체 요청을 503으로 죽일 만큼 치명적인 상황이
            # 아니라서(원본 입력으로 대체하거나 추천 없이도 진행 가능), 예외를 던지는
            # 대신 여기서 바로 복구한다.
            if not result.resolved_topic.strip():
                result.resolved_topic = topic
            if not result.recommendations:
                result.recommendations = []
            result = _calibrate_broad_resolved_topic(result)
    except AgentBudgetTimeout:
        emit_event(
            "note",
            {"text": "입력 확인 시간 예산이 끝나 원문 주제로 바로 분석을 시작합니다.", "timeout_kind": "budget"},
            stage="input_preflight",
            source="system",
        )
        return InputPreflightResult(
            status="ready",
            reason_code="ready",
            original_topic=topic,
            resolved_topic=topic,
            message="입력 확인 시간 예산이 끝나 원문 주제로 분석합니다.",
            recommendations=[topic],
        )
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
    request_runtime = RuntimeConfig(disable_timeouts=not request.fast_mode)
    deadline = AnalysisDeadline(request_runtime.total_timeout_s)
    pipeline = ResearchPipeline(deadline=deadline, runtime=request_runtime)
    preflight: InputPreflightResult | None = None
    try:
        # _run_preflight already enforces and recovers from its own stage budget.
        # Wrapping it in another wait_for with the same timeout creates a race:
        # the outer timeout can fire first and be misreported as a full-pipeline
        # deadline instead of falling back to the original topic.
        preflight = await _run_preflight(request.topic, request_runtime)
        if preflight.status in {"rejected", "needs_calibration"}:
            return _preflight_response(preflight)

        resolved_topic = preflight.resolved_topic
        pipeline_run = pipeline.run(
            resolved_topic,
            scholar_query=request.scholar_query,
            adoption_queries=request.adoption_queries,
            max_results=request.max_results,
        )
        result = await pipeline_run if request_runtime.disable_timeouts else await asyncio.wait_for(
            pipeline_run,
            timeout=deadline.remaining(),
        )
        timing = (
            {"timeouts_disabled": True, "budget_s": None, "remaining_s": None}
            if request_runtime.disable_timeouts
            else {"timeouts_disabled": False, "budget_s": ANALYZE_TIMEOUT_S, "remaining_s": round(deadline.remaining(), 2)}
        )
        result.update(
            {
                "status": "completed",
                "analysis_status": result.get("analysis_status", "complete"),
                "input_topic": request.topic,
                "recommendations": preflight.recommendations,
                "preflight": preflight.model_dump(),
                "timing": timing,
            }
        )
        return result

    except (asyncio.TimeoutError, TimeoutError):
        emit_event(
            "note",
            {"name": "analyze", "topic": resolved_topic, "reason": "deadline_reached", "timeout_kind": "budget", "timeout_s": ANALYZE_TIMEOUT_S},
            stage="pipeline",
            source="system",
        )
        result = pipeline.last_partial_result or build_deadline_result(
            resolved_topic,
            "전체 시간 예산 안에서 외부 근거를 충분히 확보하지 못해 잠정 결과를 반환합니다.",
        )
        result.update(
            {
                "status": "completed",
                "analysis_status": "partial",
                "input_topic": request.topic,
                "recommendations": preflight.recommendations if preflight else [],
                "preflight": preflight.model_dump() if preflight else None,
                "timing": {
                    "timeouts_disabled": request_runtime.disable_timeouts,
                    "budget_s": None if request_runtime.disable_timeouts else ANALYZE_TIMEOUT_S,
                    "remaining_s": None if request_runtime.disable_timeouts else 0,
                },
            }
        )
        return result
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
