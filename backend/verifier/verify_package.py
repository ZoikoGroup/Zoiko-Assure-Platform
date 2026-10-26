#!/usr/bin/env python3
"""Zoiko Assure standalone offline package verifier (ZA-EVD-001 §18).

Usage:  python verify_package.py <package.zip> [--trusted-key signer.pem] [--json]

Depends only on the ``cryptography`` package. Requires no access to Zoiko systems and
no tenant secret keys. Reports PASS / PASS_WITH_WARNINGS / FAIL with reason codes.
It verifies cryptographic and structural integrity only — never legal compliance.

Packages are untrusted input (this also runs behind the public POST /api/packages/verify-upload). The archive is
read in memory only, never extracted to disk, and every limit below is enforced on actual decompressed bytes as well
as on the archive's declared sizes. Each limit can be overridden with the environment variable named next to it.
A package that breaks a limit, is malformed or is incomplete gets a deterministic FAIL, never PASS.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import struct
import sys
import zipfile
import zlib

LEDGER_DOMAIN = b"ZOIKO-ASSURE-EVIDENCE-LEDGER-v1"
STATEMENT = "Cryptographic/structural verification only. This verifier never asserts legal compliance."

MiB = 1024 * 1024
RATIO_FLOOR = MiB  # the compression-ratio limit applies to members at least this large (small JSON compresses well)
READ_CHUNK = 64 * 1024
# Members every package contains (ZA-EVD-001 §17); signer-public-key.pem is also required without a trusted key.
REQUIRED = ("package-manifest.json", "SIGNATURE", "ledger-proofs/tenant-chain.json", "merkle-proofs/tenant-head-inclusion.json",
            "timestamp/checkpoint.json", "assurance/snapshots.json", "verification-report.json")


def _limit(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def limits() -> dict[str, int]:
    """Resource limits for one untrusted package, read from the environment on each call."""
    return {"archive": _limit("ZA_VERIFY_MAX_ARCHIVE_BYTES", 64 * MiB),   # the uploaded .zip itself
            "members": _limit("ZA_VERIFY_MAX_MEMBERS", 5000),
            "member": _limit("ZA_VERIFY_MAX_MEMBER_BYTES", 32 * MiB),    # one decompressed member (evidence payloads are <= 25 MB)
            "total": _limit("ZA_VERIFY_MAX_TOTAL_BYTES", 256 * MiB),     # all decompressed bytes together
            "ratio": _limit("ZA_VERIFY_MAX_RATIO", 100),                 # decompressed:compressed, for members >= 1 MiB
            "json": _limit("ZA_VERIFY_MAX_JSON_BYTES", 16 * MiB)}        # one JSON member parsed into memory


class Rejected(Exception):
    """The package cannot be verified safely; `code` becomes its reason code."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


# What a malformed package can raise while it is read or checked (bad ZIP data or CRC, broken deflate stream, JSON or
# text that does not decode, missing keys or wrong types). Anything else is a verifier bug and is not swallowed.
MALFORMED = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, KeyError, IndexError, TypeError, ValueError,
             AttributeError)


def _unsafe_name(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or ":" in name or "\x00" in name:
        return True
    return any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))


class _Archive:
    """An untrusted package checked up front from its directory, then read member by member within the limits."""

    def __init__(self, data: bytes, lim: dict[str, int]):
        if len(data) > lim["archive"]:
            raise Rejected("LIMIT_EXCEEDED", f"archive larger than {lim['archive']} bytes")
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            infos = self.zip.infolist()
        except (zipfile.BadZipFile, EOFError, ValueError, NotImplementedError):
            raise Rejected("PARSE", "not a readable ZIP archive") from None
        if len(infos) > lim["members"]:
            raise Rejected("LIMIT_EXCEEDED", f"more than {lim['members']} members")
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):  # ZA-EVD-001 §18 step 1: no duplicate/ambiguous member paths
            raise Rejected("PARSE", "duplicate member paths")
        declared = 0
        for i in infos:
            # orig_filename is the name as stored: on Windows zipfile rewrites "\" to "/" in `filename`.
            if _unsafe_name(i.filename) or _unsafe_name(i.orig_filename):
                raise Rejected("PARSE", "unsafe member path (absolute, parent reference or backslash)")
            if i.flag_bits & 0x1:
                raise Rejected("UNSUPPORTED", "encrypted member")
            if i.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise Rejected("UNSUPPORTED", "unsupported compression method")
            kind = stat.S_IFMT(i.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise Rejected("UNSUPPORTED", "symbolic link or special entry")
            if i.file_size > lim["member"]:
                raise Rejected("LIMIT_EXCEEDED", f"a member is larger than {lim['member']} bytes")
            if i.file_size >= RATIO_FLOOR and i.file_size > lim["ratio"] * max(i.compress_size, 1):
                raise Rejected("LIMIT_EXCEEDED", f"a member exceeds the {lim['ratio']}:1 compression ratio")
            declared += i.file_size
        if declared > lim["total"]:
            raise Rejected("LIMIT_EXCEEDED", f"members total more than {lim['total']} bytes")
        self.names, self.lim, self.total, self.cache = set(names), lim, 0, {}

    def read(self, name: str, as_json: bool = False) -> bytes:
        """One member, decompressed in chunks and counted, so declared sizes cannot hide a larger payload."""
        if name in self.cache:
            return self.cache[name]
        cap = min(self.lim["member"], self.lim["json"]) if as_json else self.lim["member"]
        out = bytearray()
        with self.zip.open(name) as f:  # names are unique, so this is the member that was validated
            while chunk := f.read(READ_CHUNK):
                out += chunk
                if len(out) > cap:
                    raise Rejected("LIMIT_EXCEEDED", f"a member is larger than {cap} bytes")
                if self.total + len(out) > self.lim["total"]:
                    raise Rejected("LIMIT_EXCEEDED", f"members total more than {self.lim['total']} bytes")
        self.total += len(out)
        self.cache[name] = data = bytes(out)
        return data

    def json(self, name: str):
        return json.loads(self.read(name, as_json=True))


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def ledger_hash(tenant_id: str, seq: int, prev: str, body: dict) -> str:
    return hashlib.sha256(LEDGER_DOMAIN + tenant_id.encode() + struct.pack(">Q", seq) + bytes.fromhex(prev)
                          + canonical(body)).hexdigest()


def leaf_hash(domain: str, tenant_id: str, seq: int, head_hash: str) -> str:
    return hashlib.sha256(b"\x00" + domain.encode() + tenant_id.encode() + struct.pack(">Q", seq)
                          + bytes.fromhex(head_hash)).hexdigest()


def node(l: str, r: str) -> str:
    return hashlib.sha256(b"\x01" + bytes.fromhex(l) + bytes.fromhex(r)).hexdigest()


def _verify_sig(pem: bytes, data: bytes, sig_hex: str) -> bool:
    from cryptography.hazmat.primitives import serialization
    try:
        serialization.load_pem_public_key(pem).verify(bytes.fromhex(sig_hex.strip()), data)
        return True
    except Exception:
        return False


def verify(zip_bytes: bytes, trusted_key_pem: bytes | None = None) -> dict:
    steps, warnings, reasons = [], [], []
    current = ["1. Parse"]  # the step being checked, named in a FAIL raised part-way through

    def fail(code: str, detail: str) -> dict:
        # Deterministic FAIL that names the step but never echoes package contents or internal paths.
        steps.append({"step": current[0], "result": "FAIL", "detail": detail})
        return {"result": "FAIL", "reason_codes": [code], "warnings": warnings, "steps": steps, "package_id": None,
                "statement": STATEMENT}

    try:
        archive = _Archive(zip_bytes, limits())
        missing = [n for n in REQUIRED if n not in archive.names]
        if trusted_key_pem is None and "signer-public-key.pem" not in archive.names:
            missing.append("signer-public-key.pem")
        if missing:
            raise Rejected("PARSE", "missing required members: " + ", ".join(missing))
        return _verify(archive, trusted_key_pem, steps, warnings, reasons, current)
    except Rejected as e:
        return fail(e.code, e.detail)
    except MALFORMED as e:
        return fail("MALFORMED", f"malformed package content ({type(e).__name__})")


def _verify(archive: _Archive, trusted_key_pem: bytes | None, steps: list, warnings: list, reasons: list,
            current: list) -> dict:
    names = archive.names

    def step(name, ok, detail=""):
        steps.append({"step": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            reasons.append(name)
        return ok

    mbytes = archive.read("package-manifest.json", as_json=True)
    manifest = json.loads(mbytes)
    step("1. Parse", manifest.get("format") == "ZA-EVD-PKG/1.0", manifest.get("format"))

    current[0] = "2. Package signature"
    pem = trusted_key_pem or archive.read("signer-public-key.pem")
    if trusted_key_pem is None:
        warnings.append("SIGNER_KEY_FROM_PACKAGE: supply --trusted-key to pin the signer independently")
    step("2. Package signature", _verify_sig(pem, mbytes, archive.read("SIGNATURE").decode()), manifest.get("signer_key_id"))

    current[0] = "3. Member digests"
    expected = set(manifest["members"]) | {"package-manifest.json", "SIGNATURE"}
    unexpected = [n for n in names if n not in expected]
    bad = [p for p, h in manifest["members"].items() if p not in names or hashlib.sha256(archive.read(p)).hexdigest() != h]
    step("3. Member digests", not bad and not unexpected, f"mismatched={bad} unexpected={unexpected}")

    current[0] = "4. Manifest event hashes"
    chain = archive.json("ledger-proofs/tenant-chain.json")
    tenant = chain["tenant_id"]
    ev_ok = all(ledger_hash(tenant, e["ledger_seq"], e["previous_event_hash"], e["canonical_event_body"]) == e["event_hash"]
                for e in chain["events"])
    step("4. Manifest event hashes", ev_ok, f"{len(chain['events'])} events")

    current[0] = "5. Ledger chain"
    prev, seq_ok = chain["genesis_hash"], True
    for i, e in enumerate(chain["events"], start=1):
        if e["ledger_seq"] != i or e["previous_event_hash"] != prev:
            seq_ok = False
            break
        prev = e["event_hash"]
    head = chain["checkpointed_head"]
    selected = set(manifest.get("evidence_ids", []))
    covered = {e["canonical_event_body"].get("evidence_id") for e in chain["events"]
               if e["canonical_event_body"].get("event_type") == "MANIFEST_CREATED"}
    step("5. Ledger chain", seq_ok and prev == head["head_hash"] and len(chain["events"]) == head["head_seq"]
         and selected <= covered, f"head_seq={head['head_seq']}")

    current[0] = "6. Merkle proof"
    mp = archive.json("merkle-proofs/tenant-head-inclusion.json")
    h = leaf_hash(mp["leaf_domain"], mp["tenant_id"], mp["head_seq"], mp["head_hash"])
    leaf_ok = h == mp["leaf"] and mp["head_hash"] == head["head_hash"]
    for s in mp["path"]:
        h = node(h, s["sibling"]) if s["position"] == "RIGHT" else node(s["sibling"], h)
    step("6. Merkle proof", leaf_ok and h == mp["merkle_root"], mp["merkle_root"][:16] + "…")

    current[0] = "7. Checkpoint signature"
    ts = archive.json("timestamp/checkpoint.json")
    cp_ok = _verify_sig(pem, canonical(ts["manifest"]), ts["cell_signature"]) and ts["manifest"]["merkle_root"] == mp["merkle_root"]
    step("7. Checkpoint signature", cp_ok, ts["manifest"]["checkpoint_id"])

    if ts.get("tsa_token"):
        steps.append({"step": "8. RFC 3161", "result": "PASS", "detail": "token present"})
    else:
        steps.append({"step": "8. RFC 3161", "result": "WARN", "detail": ts.get("tsa_status")})
        warnings.append(f"TIMESTAMP_NOT_AVAILABLE: checkpoint TSA status {ts.get('tsa_status')}")

    current[0] = "9. Assurance references"
    snaps = archive.json("assurance/snapshots.json")
    ref_ok = all(hashlib.sha256(canonical(s["dependency_manifest"])).hexdigest() == s["dependency_digest"] for s in snaps)
    step("9. Assurance references", ref_ok and {s["snapshot_id"] for s in snaps} == set(manifest.get("snapshot_ids", [])),
         f"{len(snaps)} snapshots")

    current[0] = "10. Report"
    report = archive.json("verification-report.json")
    for e in report.get("evidence", []):
        if not e.get("payload_included"):
            warnings.append(f"PAYLOAD_NOT_INCLUDED: {e['evidence_id']} ({e.get('note')})")
    warnings.append("HISTORICAL_HMAC_NOT_EXTERNALLY_RECOMPUTED: relies on signed generator statement (tenant key not exported)")

    result = "FAIL" if reasons else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    steps.append({"step": "10. Report", "result": result, "detail": ""})
    return {"result": result, "reason_codes": reasons, "warnings": warnings, "steps": steps,
            "package_id": manifest.get("package_id"), "statement": STATEMENT}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    key = None
    if "--trusted-key" in sys.argv:
        key = open(sys.argv[sys.argv.index("--trusted-key") + 1], "rb").read()
    with open(sys.argv[1], "rb") as f:
        data = f.read(limits()["archive"] + 1)  # one byte over the limit is enough for verify() to refuse it
    out = verify(data, key)
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
    else:
        for s in out["steps"]:
            print(f"[{s['result']:^5}] {s['step']}  {s['detail']}")
        for w in out["warnings"]:
            print(f"  warning: {w}")
        print(f"\nRESULT: {out['result']}")
    sys.exit(0 if out["result"] != "FAIL" else 1)
