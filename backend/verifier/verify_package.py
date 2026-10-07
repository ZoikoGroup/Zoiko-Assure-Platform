#!/usr/bin/env python3
"""Zoiko Assure standalone offline package verifier (ZA-EVD-001 §18).

Usage:  python verify_package.py <package.zip> [--trusted-key signer.pem] [--json]

Depends only on the ``cryptography`` package. Requires no access to Zoiko systems and
no tenant secret keys. Reports PASS / PASS_WITH_WARNINGS / FAIL with reason codes.
It verifies cryptographic and structural integrity only — never legal compliance.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import zipfile

LEDGER_DOMAIN = b"ZOIKO-ASSURE-EVIDENCE-LEDGER-v1"


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

    def step(name, ok, detail=""):
        steps.append({"step": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            reasons.append(name)
        return ok

    try:
        z = zipfile.ZipFile(__import__("io").BytesIO(zip_bytes))
        names = z.namelist()
    except Exception as e:
        return {"result": "FAIL", "reason_codes": ["PARSE"], "steps": [{"step": "1. Parse", "result": "FAIL", "detail": str(e)}]}
    if len(names) != len(set(names)) or "package-manifest.json" not in names or "SIGNATURE" not in names:
        return {"result": "FAIL", "reason_codes": ["PARSE"], "steps": [{"step": "1. Parse", "result": "FAIL",
                                                                        "detail": "missing/duplicate members"}]}
    mbytes = z.read("package-manifest.json")
    manifest = json.loads(mbytes)
    step("1. Parse", manifest.get("format") == "ZA-EVD-PKG/1.0", manifest.get("format"))

    pem = trusted_key_pem or z.read("signer-public-key.pem")
    if trusted_key_pem is None:
        warnings.append("SIGNER_KEY_FROM_PACKAGE: supply --trusted-key to pin the signer independently")
    step("2. Package signature", _verify_sig(pem, mbytes, z.read("SIGNATURE").decode()), manifest.get("signer_key_id"))

    expected = set(manifest["members"]) | {"package-manifest.json", "SIGNATURE"}
    unexpected = [n for n in names if n not in expected]
    bad = [p for p, h in manifest["members"].items() if p not in names or hashlib.sha256(z.read(p)).hexdigest() != h]
    step("3. Member digests", not bad and not unexpected, f"mismatched={bad} unexpected={unexpected}")

    chain = json.loads(z.read("ledger-proofs/tenant-chain.json"))
    tenant = chain["tenant_id"]
    ev_ok = all(ledger_hash(tenant, e["ledger_seq"], e["previous_event_hash"], e["canonical_event_body"]) == e["event_hash"]
                for e in chain["events"])
    step("4. Manifest event hashes", ev_ok, f"{len(chain['events'])} events")

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

    mp = json.loads(z.read("merkle-proofs/tenant-head-inclusion.json"))
    h = leaf_hash(mp["leaf_domain"], mp["tenant_id"], mp["head_seq"], mp["head_hash"])
    leaf_ok = h == mp["leaf"] and mp["head_hash"] == head["head_hash"]
    for s in mp["path"]:
        h = node(h, s["sibling"]) if s["position"] == "RIGHT" else node(s["sibling"], h)
    step("6. Merkle proof", leaf_ok and h == mp["merkle_root"], mp["merkle_root"][:16] + "…")

    ts = json.loads(z.read("timestamp/checkpoint.json"))
    cp_ok = _verify_sig(pem, canonical(ts["manifest"]), ts["cell_signature"]) and ts["manifest"]["merkle_root"] == mp["merkle_root"]
    step("7. Checkpoint signature", cp_ok, ts["manifest"]["checkpoint_id"])

    if ts.get("tsa_token"):
        steps.append({"step": "8. RFC 3161", "result": "PASS", "detail": "token present"})
    else:
        steps.append({"step": "8. RFC 3161", "result": "WARN", "detail": ts.get("tsa_status")})
        warnings.append(f"TIMESTAMP_NOT_AVAILABLE: checkpoint TSA status {ts.get('tsa_status')}")

    snaps = json.loads(z.read("assurance/snapshots.json"))
    ref_ok = all(hashlib.sha256(canonical(s["dependency_manifest"])).hexdigest() == s["dependency_digest"] for s in snaps)
    step("9. Assurance references", ref_ok and {s["snapshot_id"] for s in snaps} == set(manifest.get("snapshot_ids", [])),
         f"{len(snaps)} snapshots")

    report = json.loads(z.read("verification-report.json"))
    for e in report.get("evidence", []):
        if not e.get("payload_included"):
            warnings.append(f"PAYLOAD_NOT_INCLUDED: {e['evidence_id']} ({e.get('note')})")
    warnings.append("HISTORICAL_HMAC_NOT_EXTERNALLY_RECOMPUTED: relies on signed generator statement (tenant key not exported)")

    result = "FAIL" if reasons else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    steps.append({"step": "10. Report", "result": result, "detail": ""})
    return {"result": result, "reason_codes": reasons, "warnings": warnings, "steps": steps,
            "package_id": manifest.get("package_id"), "statement":
            "Cryptographic/structural verification only. This verifier never asserts legal compliance."}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    key = None
    if "--trusted-key" in sys.argv:
        key = open(sys.argv[sys.argv.index("--trusted-key") + 1], "rb").read()
    out = verify(open(sys.argv[1], "rb").read(), key)
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
    else:
        for s in out["steps"]:
            print(f"[{s['result']:^5}] {s['step']}  {s['detail']}")
        for w in out["warnings"]:
            print(f"  warning: {w}")
        print(f"\nRESULT: {out['result']}")
    sys.exit(0 if out["result"] != "FAIL" else 1)
