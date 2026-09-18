from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv

from app.q1_streaming.router import router as q1_router
from app.q2_paper_inference.router import router as q2_router
from app.q3_rag_qa.router import router as q3_router
from app.q5_alert.router import router as q5_router
from app.q6_multi_paper_rag.router import router as q6_router

load_dotenv()

app = FastAPI(title="PaperPilot — AI Engineer Track")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(q1_router)
app.include_router(q2_router)
app.include_router(q3_router)
app.include_router(q5_router)
app.include_router(q6_router)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ---- Single-process serving: backend also serves the built frontend ----
# `frontend/dist` (built by `npm run build`) is served on the same port,
# so the whole system runs as ONE process: `uvicorn app.main:app`.
# API routes above take precedence (registered first); the catch-all below
# only handles non-/api paths (SPA fallback for /, /q1, ...).
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    if DIST.is_dir():
        return FileResponse(DIST / "index.html")
    return JSONResponse(
        {"detail": "frontend not built — run `npm run build` in frontend/"},
        status_code=503,
    )


@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    if full_path.startswith("api"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if DIST.is_dir():
        candidate = DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
    return JSONResponse({"detail": "Not Found"}, status_code=404)
