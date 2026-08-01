from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from routes import analyze, demo, proxy, stream  # noqa: E402 (dotenv must load first)

app = FastAPI(title="AGENT24 backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 해커톤 로컬 데모용. 배포 안 하므로 그대로 둔다.
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stream.router)
app.include_router(proxy.router)
app.include_router(demo.router)
app.include_router(analyze.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
