"""Liner API client for search, agent, research, and visualization endpoints."""

import asyncio
import json
import os
from typing import Any

import httpx

from events import emit_event


class LinerClient:
    """Async client that exposes Liner JSON and SSE APIs to the pipeline."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 8.0,
        disable_timeouts: bool = False,
    ):
        self.base_url = (base_url or os.environ.get("LINER_API_BASE_URL", "https://platform.liner.com")).rstrip("/")
        self.api_key = api_key or os.environ.get("LINER_API_KEY", "")
        self.timeout = timeout
        self.disable_timeouts = disable_timeouts
        self.search_agent_timeout = float(os.environ.get("SEARCH_AGENT_TIMEOUT_S", "4"))
        self.visualization_timeout = float(os.environ.get("VISUALIZATION_TIMEOUT_S", "4"))

    async def search_web(
        self,
        query: str,
        *,
        lang: str | None = None,
        country_code: str | None = None,
        date_range: str | None = None,
        max_results: int = 10,
        stage: str = "adoption_scout",
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        return await self._search(
            mode="web",
            query=query,
            lang=lang,
            country_code=country_code,
            date_range=date_range,
            max_results=max_results,
            stage=stage,
            timeout_s=timeout_s,
        )

    async def search_scholar(
        self,
        query: str,
        *,
        lang: str | None = None,
        max_results: int = 10,
        stage: str = "scholar_scout",
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        return await self._search(
            mode="scholar",
            query=query,
            lang=lang,
            max_results=max_results,
            stage=stage,
            timeout_s=timeout_s,
        )

    async def search_agent(
        self,
        messages: list[dict[str, str]],
        *,
        mode: str = "general",
        lang: str = "ko",
        request_id: str | None = None,
        stage: str = "adversarial_verifier",
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        body = {
            "messages": messages,
            "lang": lang,
            "mode": mode,
            "request_id": request_id,
        }
        body = {key: value for key, value in body.items() if value is not None}
        return await self._stream_request(
            path="/api/v1/agents/search",
            body=body,
            stage=stage,
            name="search_agent",
            timeout_s=None if self.disable_timeouts else (timeout_s if timeout_s is not None else self.search_agent_timeout),
        )

    async def deep_research(
        self,
        messages: list[dict[str, str]],
        *,
        lang: str = "ko",
        timeout_s: float | None = 5,
        request_id: str | None = None,
        stage: str = "conditional_deep_research",
    ) -> dict[str, Any]:
        body = {
            "messages": messages,
            "lang": lang,
            "request_id": request_id,
        }
        body = {key: value for key, value in body.items() if value is not None}
        return await self._stream_request(
            path="/api/v1/agents/deep-research",
            body=body,
            stage=stage,
            name="deep_research",
            timeout_s=timeout_s,
            accept_sse=True,
        )

    async def visualize(
        self,
        query: str,
        *,
        is_search_context: bool = True,
        max_results: int = 10,
        appearance: str = "light",
        date_range: str | None = None,
        stage: str = "gap_map",
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        body = {
            "query": query,
            "appearance": appearance,
        }
        if is_search_context:
            body.update(
                {
                    "is_search_context": True,
                    "max_results": max_results,
                    "date_range": date_range,
                }
            )
        body = {key: value for key, value in body.items() if value is not None}
        return await self._stream_request(
            path="/api/v1/tools/visualization",
            body=body,
            stage=stage,
            name="visualization",
            timeout_s=timeout_s if timeout_s is not None else self.visualization_timeout,
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
        timeout_s: float | None = None,
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
        effective_timeout = None if self.disable_timeouts else (timeout_s if timeout_s is not None else self.timeout)
        call_event = emit_event(
            "tool_call",
            {"name": "search", "mode": mode, "method": "POST", "url": url, "body": body, "timeout_s": effective_timeout},
            stage=stage,
            source="liner",
        )

        try:
            async with httpx.AsyncClient(timeout=effective_timeout) as client:
                response = await client.post(
                    url,
                    headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
                    json=body,
                )
                response_body = response.json()
                response.raise_for_status()
        except (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError):
            emit_event(
                "note",
                {
                    "name": "search",
                    "mode": mode,
                    "call_id": call_event["id"],
                    "reason": "budget_timeout",
                    "timeout_kind": "budget",
                    "timeout_s": effective_timeout,
                },
                stage=stage,
                source="liner",
            )
            emit_event(
                "tool_result",
                {"name": "search", "mode": mode, "call_id": call_event["id"], "timed_out": True, "timeout_kind": "budget"},
                stage=stage,
                source="liner",
            )
            return {"results": [], "totalCount": 0, "timed_out": True, "timeout_kind": "budget"}
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

    async def _stream_request(
        self,
        *,
        path: str,
        body: dict[str, Any],
        stage: str,
        name: str,
        timeout_s: float | None,
        accept_sse: bool = False,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        call_event = emit_event(
            "tool_call",
            {"name": name, "method": "POST", "url": url, "body": body, "timeout_s": timeout_s},
            stage=stage,
            source="liner",
        )
        headers = {"x-api-key": self.api_key, "Content-Type": "application/json"}
        if accept_sse or name in {"search_agent", "visualization"}:
            headers["Accept"] = "text/event-stream"

        events: list[dict[str, Any]] = []

        async def _consume(response: httpx.Response) -> None:
            async for line in response.aiter_lines():
                event = _parse_sse_line(line)
                if event is None:
                    continue
                events.append(event)
                event_type = event.get("type")
                if event_type:
                    emit_event(event_type, event, stage=stage, source="liner")
                else:
                    emit_event("sse_line", event, stage=stage, source="liner")

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", url, headers=headers, json=body) as response:
                    response.raise_for_status()
                    # asyncio.timeout() requires Python 3.11+; wait_for() works on 3.10 too,
                    # which is what ships on some team members' machines.
                    if timeout_s is None:
                        await _consume(response)
                    else:
                        await asyncio.wait_for(_consume(response), timeout=timeout_s)
        except (asyncio.TimeoutError, TimeoutError):
            emit_event(
                "note",
                {
                    "name": name,
                    "call_id": call_event["id"],
                    "reason": "budget_timeout",
                    "timeout_kind": "budget",
                    "timeout_s": timeout_s,
                    "events_received": len(events),
                },
                stage=stage,
                source="liner",
            )
            emit_event(
                "tool_result",
                {
                    "name": name,
                    "call_id": call_event["id"],
                    "timed_out": True,
                    "timeout_kind": "budget",
                    "events_received": len(events),
                },
                stage=stage,
                source="liner",
            )
            return {"events": events, "timed_out": True, "timeout_kind": "budget"}
        except Exception as exc:
            emit_event(
                "error",
                {"name": name, "call_id": call_event["id"], "message": str(exc)},
                stage=stage,
                source="liner",
            )
            raise

        result = {"events": events, "timed_out": False}
        emit_event(
            "tool_result",
            {
                "name": name,
                "call_id": call_event["id"],
                "events_received": len(events),
                "event_types": [event.get("type") for event in events if event.get("type")],
            },
            stage=stage,
            source="liner",
        )
        return result


def _parse_sse_line(line: str) -> dict[str, Any] | None:
    """Parse one Liner SSE data line and stop at the documented stream sentinel."""
    if not line.startswith("data:"):
        return None
    data = line.split(":", 1)[1].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Liner SSE data: {data}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"unexpected Liner SSE payload: {payload!r}")
    return payload
