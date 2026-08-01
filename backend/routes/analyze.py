"""Research pipeline API route."""

import asyncio
from typing import Any
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from agent_pipeline import ResearchPipeline, build_deadline_result
from events import emit_event
from input_gate import (
    contains_hangul as _contains_hangul,
    looks_too_broad_for_demo as _looks_too_broad_for_demo,
    normalize_topic_for_gate as _normalize_topic_for_gate,
)
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
    accepted_recommendation: bool = False


class PreflightFailure(RuntimeError):
    """The cheap input gate failed, so the expensive pipeline must not run."""


def _calibrate_broad_resolved_topic(result: InputPreflightResult) -> InputPreflightResult:
    """Catch broad topics even when the model first corrected a typo."""
    if result.status == "rejected":
        return result
    topic_for_message = (result.resolved_topic or result.original_topic).strip()
    resolved = _normalize_topic_for_gate(topic_for_message)
    if _looks_too_broad_for_demo(resolved):
        result.status = "needs_calibration"
        result.reason_code = "too_broad"
        result.message = "입력하신 주제는 범위가 넓어요. 아래 추천 중 하나를 선택하거나 더 구체적으로 입력해 주세요."
        result.recommendations = result.recommendations or _broad_topic_recommendations(topic_for_message)
        return result
    return result


def _broad_topic_recommendations(topic: str) -> list[str]:
    topic = topic.strip()
    return [
        f"{topic} 적용 대상 성능 지표",
        f"{topic} 배포 환경 비용 절감",
        f"{topic} 산업 도입 사례 사용 주체",
    ]


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
        if request.accepted_recommendation:
            emit_event(
                "note",
                {"text": "추천 검색어 선택: 추가 구체화 검사 없이 바로 분석을 시작합니다.", "topic": request.topic},
                stage="input_preflight",
                source="system",
            )
            preflight = InputPreflightResult(
                status="ready",
                reason_code="accepted_recommendation",
                original_topic=request.topic,
                resolved_topic=request.topic,
                message="추천 검색어를 선택해 바로 분석합니다.",
                recommendations=[],
            )
        else:
            # _run_preflight already enforces and recovers from its own stage budget.
            # Wrapping it in another wait_for with the same timeout creates a race:
            # the outer timeout can fire first and be misreported as a full-pipeline
            # deadline instead of falling back to the original topic.
            preflight = await _run_preflight(request.topic, request_runtime)

        if preflight.status == "rejected":
            return _preflight_response(preflight)
        if preflight.status == "needs_calibration":
            emit_event(
                "note",
                {
                    "text": "주제 구체화가 필요해 추천 검색어만 반환하고 분석을 중단합니다.",
                    "status": preflight.status,
                    "reason_code": preflight.reason_code,
                },
                stage="input_preflight",
                source="system",
            )
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
        _start_final_synthesis(pipeline, result, request_runtime)
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
        _start_final_synthesis(pipeline, result, request_runtime)
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


def _start_final_synthesis(pipeline: ResearchPipeline, result: dict[str, Any], runtime: RuntimeConfig) -> None:
    if result.get("status") != "completed" or result.get("label") in {"unconfirmed_field"}:
        return
    run_id = f"analysis_{uuid4().hex[:12]}"
    result["run_id"] = run_id
    result["final_synthesis"] = {"status": "streaming", "text": ""}
    payload = _final_synthesis_payload(result)
    timeout_s = None if runtime.disable_timeouts else runtime.final_synthesis_timeout_s
    asyncio.create_task(_run_final_synthesis_stream(pipeline, payload, run_id, timeout_s))


async def _run_final_synthesis_stream(
    pipeline: ResearchPipeline,
    payload: dict[str, Any],
    run_id: str,
    timeout_s: float | None,
) -> None:
    try:
        await pipeline.agents.stream_final_synthesis(payload, run_id=run_id, timeout_s=timeout_s)
    except AgentBudgetTimeout:
        return
    except Exception as exc:
        emit_event(
            "error",
            {"name": "final_synthesis_writer", "run_id": run_id, "message": str(exc)},
            stage="final_synthesis",
            source="system",
        )


def _final_synthesis_payload(result: dict[str, Any]) -> dict[str, Any]:
    candidate = result.get("gap_candidate") or {}
    return {
        "topic": result.get("topic"),
        "label": result.get("label"),
        "analysis_status": result.get("analysis_status"),
        "scores": result.get("scores", {}),
        "gap_types": result.get("gap_types", []),
        "rationale": result.get("rationale", ""),
        "connected_points": result.get("connected_points", []),
        "gap_points": result.get("gap_points", []),
        "potential_points": result.get("potential_points", []),
        "confirmed_barriers": result.get("confirmed_barriers", []),
        "inferred_barriers": result.get("inferred_barriers", []),
        "opportunity_suggestions": result.get("opportunity_suggestions", []),
        "research_cluster": candidate.get("research_cluster", {}),
        "candidate_connections": candidate.get("candidate_connections", [])[:5],
        "links": candidate.get("links", [])[:5],
        "score_breakdown": candidate.get("score_breakdown", {}),
        "vocabulary": result.get("vocabulary", {}),
        "topic_anchor": result.get("topic_anchor", {}),
        "queries": result.get("queries", {}),
        "deep_research": _compact_deep_research(result.get("deep_research", {})),
        "academic_evidence": [_compact_record(record) for record in result.get("academic_evidence", [])[:6]],
        "adoption_evidence": [_compact_record(record) for record in result.get("adoption_evidence", [])[:6]],
    }


def _compact_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    fields = (
        "source_title",
        "source_url",
        "citation_count",
        "subject_raw",
        "subject_canonical",
        "relation",
        "usage_context",
        "adoption_stage",
        "technology_raw",
        "technology_canonical",
        "use_case_raw",
        "use_case_canonical",
        "context_raw",
        "context_canonical",
        "expected_value_raw",
        "expected_value_canonical",
        "canonical_claim",
        "evidence_span",
        "explicit_barriers",
    )
    return {field: record[field] for field in fields if record.get(field) not in (None, "", [])}


def _compact_deep_research(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in ("used", "timed_out", "status", "reason", "review")
        if value.get(key) not in (None, "", {})
    }
