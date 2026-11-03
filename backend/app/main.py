"""Zoiko Assure control-plane API (modular monolith, ZA-ARCH-001 §17)."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import auth
from .db import check_database
from .limits import BodyLimit
from .routers import ai, applicability, assurance, catalog, core, dashboard, evidence, integration, platform, rules
from .routers import iam as iam_routes
from .security import assign_operation_ids, dev_header_violation, unguarded_operations

# Roadmap 4d (I-08): the development identity header never runs outside an explicit development or test environment.
if _dev_header := dev_header_violation():
    raise RuntimeError(_dev_header)

@asynccontextmanager
async def lifespan(_: FastAPI):
    from .db import IS_POSTGRES, Base, engine
    from .migrate import provision_data, require_current
    from .pg_bootstrap import verify_context_key, verify_runtime_role
    # Start-up never creates or alters the schema (DAT-REQ-023) and holds no owner or migration credential (DAT-REQ-004,
    # DAT §13). `python -m app.migrate upgrade` migrates, provisions the runtime role and the context key, and creates
    # the bootstrap accounts and (ZA_DEMO_MODE=1) the demo fixture (docs/database-migrations.md). Here, read-only and as
    # the runtime role: the schema is at this build's head revision, the runtime role is least-privilege and the
    # database verifies this process's context key; otherwise stop.
    if IS_POSTGRES:
        database = check_database()
        revision = require_current(engine=engine)
        with engine.connect() as conn:
            verify_runtime_role(conn)
            verify_context_key(conn)
        logging.getLogger("uvicorn.error").info("Database: %s; schema revision %s", database, revision)
    else:  # isolated SQLite test runs only (ZA_ALLOW_SQLITE=1): one database, no roles, no RLS
        Base.metadata.create_all(engine)
        provision_data()
    yield


app = FastAPI(lifespan=lifespan, title="Zoiko Assure API", version="1.0.0",
              description="Regulatory obligation and proof infrastructure — deterministic applicability, "
                          "bitemporal assurance, evidence integrity and IPE.")

# Body cap for the public package verifier, enforced as the upload streams in. Added before CORS so CORS stays the outer
# layer and a 413 still carries CORS headers the browser can read.
app.add_middleware(BodyLimit, paths={"/api/packages/verify-upload": evidence.verify_upload_body_limit})
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("ZA_CORS", "http://localhost:5173").split(","),
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

ROUTERS = (auth.router, core.router, catalog.router, rules.router, applicability.router, assurance.router, evidence.router,
           integration.router, ai.router, dashboard.router, platform.router, iam_routes.router)
assign_operation_ids(ROUTERS)  # API-REQ-031: one stable operationId per operation, before the routes are mounted
for r in ROUTERS:
    app.include_router(r, prefix="/api")

# Deny by default (IAM-REQ-007, roadmap Stage 4a): every route, reads included, declares its operation's authorization
# (app/security.py `authorize` / `authenticated`) or is listed as public with its reason.
if _unguarded := unguarded_operations(ROUTERS):
    raise RuntimeError("Refusing to start: routes without an authorization requirement: " + "; ".join(_unguarded))


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})
