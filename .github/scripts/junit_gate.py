#!/usr/bin/env python3
"""CI gate over a pytest JUnit XML report (see docs/ci-cd.md).

pytest exits 0 when tests are skipped, so a database that silently fell back to SQLite (conftest.py does that when
ZA_TEST_DATABASE_URL is unset) would leave the PostgreSQL/RLS proofs skipped and the job green. This gate reads the
report and fails unless the run matches what the job is for:

  * any failure or error fails the gate, and so does an empty or missing report;
  * --require MODULE=N   at least N tests from MODULE ran and every one of them passed (none skipped);
  * --no-skips           any skipped test fails the gate;
  * --allow-skip-reason  skips are tolerated only with exactly this reason (repeatable); any other skip fails.

It prints passed / failed / skipped / errors, appends them to the GitHub step summary, and can redact the
passwords of database URLs held in named environment variables from the report before it is uploaded.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit


def _cases(report: Path):
    root = ET.parse(report).getroot()
    for case in root.iter("testcase"):
        kids = {child.tag: child for child in case}
        if "error" in kids:
            status = "error"
        elif "failure" in kids:
            status = "failed"
        elif "skipped" in kids:
            status = "skipped"
        else:
            status = "passed"
        reason = (kids.get("skipped").get("message") or "") if status == "skipped" else ""
        yield {"classname": case.get("classname") or "", "name": case.get("name") or "", "status": status,
               "reason": reason.strip()}


def _in_module(case: dict, module: str) -> bool:
    module = module.removesuffix(".py").replace("/", ".").split(".")[-1]
    parts = case["classname"].split(".") + case["name"].split(".")
    return module in parts


def _redact(report: Path, env_names: list[str]) -> int:
    secrets = []
    for name in env_names:
        value = os.environ.get(name, "")
        if not value:
            continue
        password = urlsplit(value).password if "://" in value else value
        if password and len(password) >= 8:
            secrets.append(password)
    if not secrets:
        return 0
    text = report.read_text(encoding="utf-8")
    hits = sum(text.count(s) for s in secrets)
    for s in secrets:
        text = text.replace(s, "***")
    report.write_text(text, encoding="utf-8")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--label", default="pytest")
    ap.add_argument("--require", action="append", default=[], metavar="MODULE=N")
    ap.add_argument("--no-skips", action="store_true")
    ap.add_argument("--allow-skip-reason", action="append", default=[])
    ap.add_argument("--redact-env", action="append", default=[], metavar="ENV_NAME")
    ap.add_argument("--summary-json", type=Path)
    args = ap.parse_args()

    problems: list[str] = []
    if not args.report.is_file():
        problems.append(f"no test report at {args.report}: the tests did not run")
        cases = []
    else:
        redacted = _redact(args.report, args.redact_env)
        if redacted:
            print(f"Redacted {redacted} credential occurrence(s) from the report before upload.")
        try:
            cases = list(_cases(args.report))
        except ET.ParseError as e:
            problems.append(f"unreadable test report {args.report}: {e}")
            cases = []

    counts = {"passed": sum(c["status"] == "passed" for c in cases),
              "failed": sum(c["status"] == "failed" for c in cases),
              "skipped": sum(c["status"] == "skipped" for c in cases),
              "errors": sum(c["status"] == "error" for c in cases),
              "total": len(cases)}

    if args.report.is_file() and not cases and not problems:
        problems.append("the report contains no tests")
    bad = [c for c in cases if c["status"] in ("failed", "error")]
    if bad:
        problems.append(f"{len(bad)} test(s) failed or errored")

    skipped = [c for c in cases if c["status"] == "skipped"]
    if args.no_skips and skipped:
        problems.append(f"{len(skipped)} test(s) skipped; this job requires every test to execute")
    elif args.allow_skip_reason:
        unexpected = [c for c in skipped if c["reason"] not in args.allow_skip_reason]
        if unexpected:
            problems.append(f"{len(unexpected)} test(s) skipped for an unexpected reason")
        skipped = unexpected

    required = []
    for spec in args.require:
        module, _, n = spec.partition("=")
        want = int(n or 1)
        mine = [c for c in cases if _in_module(c, module)]
        ok = sum(c["status"] == "passed" for c in mine)
        required.append((module, ok, want, len(mine)))
        if ok < want or ok != len(mine):
            problems.append(f"{module}: {ok} passed of {len(mine)} run, {want} required to execute and pass "
                            f"(skipped or missing tests mean the verification did not happen)")

    lines = [f"### {args.label}", "", "| passed | failed | skipped | errors | total |", "|---|---|---|---|---|",
             f"| {counts['passed']} | {counts['failed']} | {counts['skipped']} | {counts['errors']} | {counts['total']} |"]
    for module, ok, want, ran in required:
        lines.append(f"- required `{module}`: {ok} passed / {ran} run (needs {want})")
    for c in bad[:25]:
        lines.append(f"- {c['status'].upper()}: `{c['classname']}::{c['name']}`")
    for c in skipped[:25]:
        lines.append(f"- SKIPPED: `{c['classname']}::{c['name']}` ({c['reason'] or 'no reason'})")
    lines.append("")
    lines.append("**Gate: FAIL**" if problems else "**Gate: PASS**")
    for p in problems:
        lines.append(f"- {p}")
    report_md = "\n".join(lines)
    print(report_md)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report_md + "\n\n")
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps({"label": args.label, **counts, "gate": "FAIL" if problems else "PASS",
                                                 "problems": problems,
                                                 "required": [{"module": m, "passed": ok, "ran": ran, "needed": w}
                                                              for m, ok, w, ran in required]}, indent=2),
                                     encoding="utf-8")
    for p in problems:
        print(f"::error title={args.label}::{p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
