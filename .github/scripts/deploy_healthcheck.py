#!/usr/bin/env python3
"""Post-deployment verification for a Zoiko Assure environment (see docs/ci-cd.md).

Uses only endpoints the backend already exposes; it does not need, and never sends, credentials.

  backend   health          GET  {api}/health               200 and status "ok"
            api reachable   GET  {api}/dictionary           200 JSON
            database        GET  {api}/watermark            200 with an integer record_seq (reads the database)
            auth boundary   GET  {api}/me                   401 with no credentials
                            GET  {api}/me  (bad bearer)     401
                            GET  {api}/me  (X-Actor-Id)     401 "Sign in required": dev identity header disabled
            demo mode off   GET  {api}/auth/demo-accounts   404
  frontend  reachable       GET  {frontend}/login           200 HTML
            api config      the /login client bundle contains the expected NEXT_PUBLIC_API_URL
            frontend->api   CORS preflight from the frontend origin is allowed by the backend

Exit 0 only when every selected check passes. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

UA = "zoiko-assure-deploy-check/1"
results: list[dict] = []


def http(method: str, url: str, headers: dict | None = None, timeout: float = 15.0):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(5_000_000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read(100_000)


def record(name: str, ok: bool, detail: str) -> bool:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return ok


def as_json(body: bytes):
    try:
        return json.loads(body)
    except ValueError:
        return None


def check(name: str, fn) -> bool:
    try:
        ok, detail = fn()
    except Exception as e:  # network errors are failures, reported without request internals
        ok, detail = False, f"{type(e).__name__}: {str(e)[:200]}"
    return record(name, ok, detail)


def wait_for_health(api: str, attempts: int, interval: float) -> bool:
    last = "not attempted"
    for i in range(1, attempts + 1):
        try:
            status, _, body = http("GET", f"{api}/health")
            data = as_json(body) or {}
            if status == 200 and data.get("status") == "ok":
                return record("backend health", True, f"200 status=ok cell={data.get('cell_id')} "
                                                      f"dictionary={data.get('dictionary_version')} (attempt {i})")
            last = f"HTTP {status}"
        except Exception as e:
            last = f"{type(e).__name__}"
        if i < attempts:
            time.sleep(interval)
    return record("backend health", False, f"not healthy after {attempts} attempt(s), last: {last}")


def backend_checks(api: str) -> None:
    def reachable():
        s, _, b = http("GET", f"{api}/dictionary")
        return s == 200 and isinstance(as_json(b), dict), f"HTTP {s}"

    def database():
        s, _, b = http("GET", f"{api}/watermark")
        d = as_json(b) or {}
        return s == 200 and isinstance(d.get("record_seq"), int), f"HTTP {s} record_seq={d.get('record_seq')!r}"

    def boundary(headers: dict, expect_detail: str | None = None):
        def run():
            s, _, b = http("GET", f"{api}/me", headers)
            detail = (as_json(b) or {}).get("detail")
            ok = s == 401 and (expect_detail is None or detail == expect_detail)
            return ok, f"HTTP {s} detail={detail!r}"
        return run

    def demo_off():
        s, _, _ = http("GET", f"{api}/auth/demo-accounts")
        return s == 404, f"HTTP {s} (404 means ZA_DEMO_MODE is off)"

    check("backend api reachable", reachable)
    check("database connectivity", database)
    check("auth boundary: no credentials", boundary({}))
    check("auth boundary: invalid bearer token", boundary({"Authorization": "Bearer deploy-check-invalid-token"}))
    check("auth boundary: dev identity header disabled",
          boundary({"X-Actor-Id": "deploy-check-probe"}, expect_detail="Sign in required"))
    check("demo mode disabled", demo_off)


def frontend_checks(frontend: str, api: str, expect_api: str) -> None:
    html = {"body": b""}

    def reachable():
        s, h, b = http("GET", f"{frontend}/login")
        html["body"] = b
        ctype = {k.lower(): v for k, v in h.items()}.get("content-type", "")
        return s == 200 and "text/html" in ctype, f"HTTP {s} {ctype}"

    def api_config():
        srcs = list(dict.fromkeys(re.findall(rb'src="([^"]*/_next/static/[^"]+\.js)"', html["body"])))[:80]
        if not srcs:
            return False, "no client scripts found on /login"
        needle = expect_api.encode()
        for src in srcs:
            s, _, b = http("GET", urljoin(frontend + "/", src.decode()))
            if s == 200 and needle in b:
                return True, f"bundle uses NEXT_PUBLIC_API_URL={expect_api}"
        return False, f"{expect_api} not found in {len(srcs)} client script(s): wrong NEXT_PUBLIC_API_URL at build time"

    def cors():
        parts = urlsplit(frontend)
        origin = f"{parts.scheme}://{parts.netloc}"
        s, h, _ = http("OPTIONS", f"{api}/me", {"Origin": origin, "Access-Control-Request-Method": "GET",
                                                "Access-Control-Request-Headers": "authorization"})
        allow = {k.lower(): v for k, v in h.items()}.get("access-control-allow-origin")
        return 200 <= s < 300 and allow == origin, f"HTTP {s} allow-origin={allow!r} for {origin}"

    if check("frontend reachable", reachable):
        check("frontend api configuration", api_config)
    check("frontend -> backend (CORS)", cors)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api-base", required=True, help="backend API base, e.g. https://api.example.org/api")
    ap.add_argument("--frontend-url", help="frontend origin; required unless --scope backend")
    ap.add_argument("--expect-api-base", help="NEXT_PUBLIC_API_URL the frontend was built with (default: --api-base)")
    ap.add_argument("--scope", choices=["backend", "all"], default="all")
    ap.add_argument("--attempts", type=int, default=20, help="health attempts while the rollout settles")
    ap.add_argument("--interval", type=float, default=6.0)
    ap.add_argument("--report", help="write the results as JSON to this path")
    args = ap.parse_args()
    api = args.api_base.rstrip("/")
    if args.scope == "all" and not args.frontend_url:
        ap.error("--frontend-url is required with --scope all")

    if wait_for_health(api, args.attempts, args.interval):
        backend_checks(api)
        if args.scope == "all":
            frontend_checks(args.frontend_url.rstrip("/"), api, (args.expect_api_base or args.api_base).rstrip("/"))

    failed = [r for r in results if not r["ok"]]
    verdict = "FAIL" if failed else "PASS"
    print(f"Verification: {verdict} ({len(results) - len(failed)}/{len(results)} checks passed)")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump({"verdict": verdict, "api_base": api, "frontend_url": args.frontend_url, "checks": results}, fh,
                      indent=2)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### Deployment verification ({args.scope}): {verdict}\n\n| check | result | detail |\n|---|---|---|\n")
            for r in results:
                fh.write(f"| {r['check']} | {'PASS' if r['ok'] else 'FAIL'} | {r['detail']} |\n")
            fh.write("\n")
    for r in failed:
        print(f"::error title=Deployment verification::{r['check']}: {r['detail']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
