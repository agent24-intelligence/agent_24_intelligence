"""Research pipeline API route."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent_pipeline import run_pipeline

router = APIRouter()


class AnalyzeRequest(BaseModel):
    topic: str
    scholar_query: str | None = None
    adoption_queries: list[str] | None = None
    max_results: int = Field(default=10, ge=1, le=20)


@router.post("/api/analyze")
async def analyze(request: AnalyzeRequest):
    return await run_pipeline(
        request.topic,
        scholar_query=request.scholar_query,
        adoption_queries=request.adoption_queries,
        max_results=request.max_results,
    )
