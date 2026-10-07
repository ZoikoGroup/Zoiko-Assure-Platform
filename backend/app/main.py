"""Zoiko Assure control-plane API (modular monolith, ZA-ARCH-001 §17)."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import auth
from .db import AdminSession
from .routers import ai, applicability, assurance, catalog, core, dashboard, evidence, integration, rules

@asynccontextmanager
async def lifespan(_: FastAPI):
    from .pg_bootstrap import bootstrap
    from .seed import seed_if_empty
    from .auth import ensure_demo_credentials
    bootstrap()  # owner role: tables + row-level security (PostgreSQL)
    with AdminSession() as db:  # owner role: demo seed (not subject to RLS)
        seed_if_empty(db)
        ensure_demo_credentials(db)
    yield


app = FastAPI(lifespan=lifespan, title="Zoiko Assure API", version="1.0.0",
              description="Regulatory obligation and proof infrastructure — deterministic applicability, "
                          "bitemporal assurance, evidence integrity and IPE.")

app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("ZA_CORS", "http://localhost:3100,http://127.0.0.1:3100").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, core.router, catalog.router, rules.router, applicability.router, assurance.router, evidence.router,
          integration.router, ai.router, dashboard.router):
    app.include_router(r, prefix="/api")


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})
