"""FastAPI app: dashboard API, GitHub App webhook and the built dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, github_app

STATIC = Path(__file__).parent / "static"


def create_app(local_root: Path | None = None) -> FastAPI:
    from contextlib import asynccontextmanager

    from ..store import Store

    mcp_app = None
    if local_root and Store(local_root).exists:
        from ..mcp_server import http_app

        mcp_app = http_app(Store(local_root))

    @asynccontextmanager
    async def lifespan(_app):
        # Mounted sub-apps don't get lifespan events; run the MCP session manager's here.
        if mcp_app is None:
            yield
            return
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(title="Why", version="0.1.0", docs_url="/api/docs", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])
    if local_root:
        api.registry.add("local", local_root)
    github_app.load_existing()
    from .. import registry

    app.include_router(api.router)
    app.include_router(github_app.router)
    app.include_router(registry.router)
    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    @app.get("/snapshot.json", include_in_schema=False)
    def snapshot() -> JSONResponse:
        # Served live so the dashboard's static-fallback path also works against a running server.
        repos = list(api.registry.repos)
        if not repos:
            return JSONResponse({"error": "no repos"}, status_code=404)
        st = api.registry.store(repos[0])
        return JSONResponse(api.repo_snapshot(st, repo_id=repos[0]))

    if (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets", check_dir=False), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = STATIC / path
            if path and f.is_file() and STATIC in f.resolve().parents and f.name != "index.html":
                return FileResponse(f, headers={"Cache-Control": "no-cache"})
            # Always revalidate the shell: it names the hashed asset files of the current build.
            return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/", include_in_schema=False)
        def placeholder() -> HTMLResponse:
            return HTMLResponse("<h1>Why</h1><p>Dashboard not built. Run <code>cd web && npm i && npm run build</code>."
                                "</p><p>API: <a href='/api/docs'>/api/docs</a></p>")
    return app


def app_from_env() -> FastAPI:
    """Entry point for `uvicorn why.server.app:app_from_env --factory` in hosted mode."""
    import os

    root = os.environ.get("WHY_LOCAL_REPO")
    return create_app(Path(root) if root else None)
