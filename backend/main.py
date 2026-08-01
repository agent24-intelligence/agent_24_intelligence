import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from routes import analyze, demo, proxy, stream  # noqa: E402 (dotenv must load first)

logger = logging.getLogger("agent24")

app = FastAPI(title="AGENT24 backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 해커톤 로컬 데모용. 배포 안 하므로 그대로 둔다.
    allow_methods=["*"],
    allow_headers=["*"],
)


# 파이프라인 중 처리되지 않은 예외(네트워크 오류, 이상한 입력에서 터지는 KeyError 등)가
# Starlette 기본 500(plain text "Internal Server Error")으로 새면 프론트에서
# res.json()이 SyntaxError로 죽는다. 항상 JSON을 돌려주도록 여기서 잡는다.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"서버 처리 중 오류가 발생했습니다: {exc}"})


app.include_router(stream.router)
app.include_router(proxy.router)
app.include_router(demo.router)
app.include_router(analyze.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
