"""Zoiko Assure control-plane API (modular monolith, ZA-ARCH-001 §17)."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import auth
from .db import AdminSession, check_database
from .limits import BodyLimit
from .routers import ai, applicability, assurance, catalog, core, dashboard, evidence, integration, platform, rules

@asynccontextmanager
async def lifespan(_: FastAPI):
    from .pg_bootstrap import bootstrap
    from .seed import seed_if_empty
    from .auth import ensure_demo_credentials
    from .accounts import bootstrap_accounts
    # Before anything else: reach the configured database with the owner role, or stop with a clear reason. The runtime
    # role is checked after bootstrap, which provisions it (on a fresh database it does not exist before then).
    check_database(runtime=False)
    bootstrap()  # owner role: runtime role, tables + row-level security (PostgreSQL)
    logging.getLogger("uvicorn.error").info("Database: %s", check_database())
    if auth.DEMO_MODE:  # demo/dev only: synthetic Customer Zero fixture + demo sign-in; never on a normal start
        with AdminSession() as db:  # owner role: demo seed (not subject to RLS)
            seed_if_empty(db)
            ensure_demo_credentials(db)
    with AdminSession() as db:  # one-time Super Admin / Organisation Admin accounts from the backend environment
        bootstrap_accounts(db)
    yield


app = FastAPI(lifespan=lifespan, title="Zoiko Assure API", version="1.0.0",
              description="Regulatory obligation and proof infrastructure — deterministic applicability, "
                          "bitemporal assurance, evidence integrity and IPE.")

# Body cap for the public package verifier, enforced as the upload streams in. Added before CORS so CORS stays the outer
# layer and a 413 still carries CORS headers the browser can read.
app.add_middleware(BodyLimit, paths={"/api/packages/verify-upload": evidence.verify_upload_body_limit})
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("ZA_CORS", "http://localhost:5173").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, core.router, catalog.router, rules.router, applicability.router, assurance.router, evidence.router,
          integration.router, ai.router, dashboard.router, platform.router):
    app.include_router(r, prefix="/api")


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})
