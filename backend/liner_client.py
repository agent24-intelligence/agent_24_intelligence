"""Liner Search API client used by the research pipeline."""

import os
from typing import Any

import httpx

from events import emit_event


class LinerClient:
    """Small async client for Liner's Web and Scholar Search endpoints."""

    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 90.0):
        self.base_url = (base_url or os.environ.get("LINER_API_BASE_URL", "https://platform.liner.com")).rstrip("/")
        self.api_key = api_key or os.environ.get("LINER_API_KEY", "")
        self.timeout = timeout

    async def search_web(
        self,
        query: str,
        *,
        lang: str | None = None,
        country_code: str | None = None,
        date_range: str | None = None,
        max_results: int = 10,
        stage: str = "adoption_scout",
    ) -> dict[str, Any]:
        return await self._search(
            mode="web",
            query=query,
            lang=lang,
            country_code=country_code,
            date_range=date_range,
            max_results=max_results,
            stage=stage,
        )

    async def search_scholar(
        self,
        query: str,
        *,
        lang: str | None = None,
        max_results: int = 10,
        stage: str = "scholar_scout",
    ) -> dict[str, Any]:
        return await self._search(
            mode="scholar",
            query=query,
            lang=lang,
            max_results=max_results,
            stage=stage,
        )

    async def _search(
        self,
        *,
        mode: str,
        query: str,
        lang: str | None,
        max_results: int,
        stage: str,
        country_code: str | None = None,
        date_range: str | None = None,
    ) -> dict[str, Any]:
        path = f"/api/v1/tools/search/{mode}"
        body = {
            "query": query,
            "lang": lang,
            "country_code": country_code,
            "date_range": date_range,
            "max_results": max_results,
        }
        body = {key: value for key, value in body.items() if value is not None}
        url = f"{self.base_url}{path}"
        call_event = emit_event(
            "tool_call",
            {"name": "search", "mode": mode, "method": "POST", "url": url, "body": body},
            stage=stage,
            source="liner",
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                    json=body,
                )
                response_body = response.json()
                response.raise_for_status()
        except Exception as exc:
            emit_event(
                "error",
                {"name": "search", "mode": mode, "call_id": call_event["id"], "message": str(exc)},
                stage=stage,
                source="liner",
            )
            raise

        emit_event(
            "tool_result",
            {"name": "search", "mode": mode, "call_id": call_event["id"], "status": response.status_code, "body": response_body},
            stage=stage,
            source="liner",
        )
        return response_body
