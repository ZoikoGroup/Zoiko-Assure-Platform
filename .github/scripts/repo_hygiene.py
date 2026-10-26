#!/usr/bin/env python3
"""Repository hygiene gate: committed secrets and private runtime state (see docs/ci-cd.md).

Checks every file git would ship (tracked files, plus untracked files that are not ignored so the same check works
as a local pre-commit run) for:

  * .env files other than *.example / *.sample / *.template templates
  * private key material (PEM/OpenSSH/PGP private key blocks) and private-key / keystore / credential files
  * a small set of high-confidence credential token formats, and database URLs with a non-placeholder password
  * SQLite databases (by extension or by file header) and anything under backend/var/
  * committed local environments and build output (virtualenvs, node_modules, .next)

Only paths, line numbers and rule names are printed - never the matched content. Standard library only; the git
command used is the read-only `git ls-files`.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]  # this file lives in <repo>/.github/scripts/

TEMPLATE_SUFFIXES = (".example", ".sample", ".template")

SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx", ".ppk", ".tfstate", ".secret",
                   ".secrets")
SECRET_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".pypirc", ".htpasswd", "credentials.json",
                "credentials.csv", "client_secret.json", "terraform.tfstate.backup"}
SECRET_DIRS = {"secrets", ".secret", ".secrets"}
SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".db-journal")
SQLITE_MAGIC = b"SQLite format 3\x00"
RUNTIME_DIRS = (("backend/var/", "private runtime data (backend/var)"),)
RUNTIME_PARTS = {".venv": "virtualenv", "venv": "virtualenv", "node_modules": "node_modules", ".next": "Next.js build"}

PRIVATE_KEY_RE = re.compile(rb"-----BEGIN ((RSA|DSA|EC|OPENSSH|ENCRYPTED|PGP) )?PRIVATE KEY( BLOCK)?-----")
TOKEN_RES = {
    "AWS access key id": re.compile(rb"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(rb"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "Slack token": re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{20,}"),
    "Stripe live key": re.compile(rb"\b[rs]k_live_[0-9A-Za-z]{20,}"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "Anthropic API key": re.compile(rb"\bsk-ant-[A-Za-z0-9_\-]{30,}"),
}
DB_URL_RE = re.compile(rb"\b(postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqp)(?:\+[a-z0-9]+)?://"
                       rb"[^\s:/@'\"`]+:([^\s@'\"`]+)@")
PLACEHOLDER_RE = re.compile(rb"^(\*+|\.\.\.|<[^>]*>|\$\{[^}]*\}|\$[A-Z_]+|\{[^}]*\}|password|example|x{3,}|"
                            rb"(CHANGE_?ME|placeholder|redacted)[A-Za-z0-9_]*)$", re.IGNORECASE)
MAX_SCAN_BYTES = 2_000_000


def candidate_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root,
                         capture_output=True, check=True).stdout
    return sorted({p for p in out.decode("utf-8", "surrogateescape").split("\0") if p})


def is_env_file(name: str) -> bool:
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def line_of(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    ap.add_argument("paths", nargs="*", help="check only these root-relative paths instead of the git file list")
    args = ap.parse_args()
    root = args.root.resolve()

    errors: list[str] = []
    warnings: list[str] = []
    files = sorted({p.replace("\\", "/") for p in args.paths}) if args.paths else candidate_files(root)
    scanned = 0
    for rel in files:
        p = PurePosixPath(rel)
        path = root / rel
        if not path.is_file():  # deleted in the working tree but still in the index
            continue
        name = p.name
        lower = name.lower()

        if is_env_file(name) and not lower.endswith(TEMPLATE_SUFFIXES):
            errors.append(f"{rel}: committed .env file (commit a *.example template instead)")

        if lower.endswith(SECRET_SUFFIXES) or lower in SECRET_NAMES or any(part in SECRET_DIRS for part in p.parts[:-1]):
            errors.append(f"{rel}: private key, keystore or credential file")
        if lower.startswith("service-account") and lower.endswith(".json"):
            errors.append(f"{rel}: cloud service-account credential file")

        for prefix, why in RUNTIME_DIRS:
            if rel.startswith(prefix):
                errors.append(f"{rel}: {why}")
        for part in p.parts[:-1]:
            if part in RUNTIME_PARTS:
                errors.append(f"{rel}: committed {RUNTIME_PARTS[part]} directory content")
                break

        if lower.endswith(SQLITE_SUFFIXES):
            errors.append(f"{rel}: database file")
            continue
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(16)
        if head == SQLITE_MAGIC:
            errors.append(f"{rel}: SQLite database (by file header)")
            continue
        if size > MAX_SCAN_BYTES:
            continue
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            continue  # binary
        scanned += 1
        m = PRIVATE_KEY_RE.search(data)
        if m:
            errors.append(f"{rel}:{line_of(data, m.start())}: private key block")
        for rule, rx in TOKEN_RES.items():
            m = rx.search(data)
            if m:
                errors.append(f"{rel}:{line_of(data, m.start())}: {rule}")
        for m in DB_URL_RE.finditer(data):
            if not PLACEHOLDER_RE.match(m.group(2)):
                errors.append(f"{rel}:{line_of(data, m.start())}: database URL with an embedded password")

    report = [f"Repository hygiene: {len(files)} path(s) checked, {scanned} text file(s) scanned for secrets."]
    report += [f"WARNING {w}" for w in warnings]
    report += [f"FAIL    {e}" for e in errors]
    report.append("Result: FAIL" if errors else "Result: PASS")
    print("\n".join(report))
    for w in warnings:
        print(f"::warning title=Repository hygiene::{w}")
    for e in errors:
        print(f"::error title=Repository hygiene::{e}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### Repository hygiene\n\n" + "\n".join(f"- {line}" for line in report) + "\n\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
