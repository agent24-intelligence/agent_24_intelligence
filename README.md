# AGENT:24 — Track 03 (Liner API Agent)

Research-to-Reality Radar. 학술 근거와 산업 도입 증거 사이의 불일치를 찾아 적용 갭을 판정하는 리서치 에이전트.

세부 기획은 `docs/` 참고. 빌드 원칙과 코드 스타일은 `AGENTS.md` 참고.

## 구조

```
backend/    FastAPI. 이벤트 브로드캐스트 + SSE + API 테스트 프록시.
frontend/   React(Vite). Raw API Stream 화면 + API 테스트 화면.
```

## 실행

### backend

```
cd backend
pip install -r requirements.txt
python3 -m uvicorn main:app --reload --port 8000
```

루트의 `.env`에 `OPENAI_API_KEY`, `LINER_API_KEY`, `LINER_API_BASE_URL`을 채워둔다 (`.env.example` 참고, 커밋 금지).

### frontend

```
cd frontend
npm install
npm run dev
```

`http://localhost:5173` — 기본 화면. 탭으로 Raw API Stream / API 테스트 전환.
`http://localhost:5173/?clean=1` — 세컨드 화면(라이브 데모)용. 탭 없이 스트림만 나온다.

## 파이프라인 → Raw API Stream 연동

파이프라인 코드에서 각 단계마다 이렇게 호출하면 그대로 화면에 뜬다. 별도 등록/설정 없음.

```python
from events import emit_event

emit_event("tool_call", {"name": "search", "params": {...}}, stage="scholar_scout", source="liner")
emit_event("tool_result", {...}, stage="scholar_scout", source="liner")
emit_event("note", {"text": "1차 스캔 근거 부족 → Deep Research로 승격"}, stage="conditional_deep_research", source="system")
emit_event("error", {"reason": "timeout"}, stage="conditional_deep_research", source="liner")
```

`type`은 원본 이벤트 이름을 그대로 쓰면 된다 (Liner SSE 이벤트명 포함). 화면은 모르는 타입이 와도 죽지 않고 원문 그대로 보여준다.

`backend/routes/demo.py`는 실제 파이프라인이 붙기 전까지 화면 개발용으로 쓰는 모의 실행 스텁이다. 실제 파이프라인이 `emit_event`를 호출하기 시작하면 지워도 된다.

## API 테스트 지원

`API 테스트` 탭에서 OpenAI/Liner 엔드포인트에 직접 요청을 보내고 원문 응답을 바로 확인할 수 있다. 키는 서버(.env)에서만 쓰이고 프론트로 내려가지 않는다. 이 수동 호출도 Raw API Stream에 같이 찍힌다.
