"""Evidence integrity & chain of custody (ZA-EVD-001).

Acknowledge only after: encrypted payload durably stored -> MANIFEST_CREATED event
appended to the tenant ledger (prev-hash chained) in the same DB transaction.
Erasure appends events; history is never rewritten.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import CELL_ID, DATA_DIR
from ..models import (EvidenceArtifact, EvidenceLedgerEvent, EvidencePackage, Issue, LedgerCheckpoint, LegalHold,
                      TenantLedgerHead, new_id, utcnow)
from ..platform import canonical_json, digest, emit, ensure_utc, next_record_seq, sha256_hex
from . import keys

OBJECT_DIR = DATA_DIR / "objects"
WORM_DIR = DATA_DIR / "worm"
PACKAGE_DIR = DATA_DIR / "packages"
for _d in (OBJECT_DIR, WORM_DIR, PACKAGE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

LEDGER_DOMAIN = b"ZOIKO-ASSURE-EVIDENCE-LEDGER-v1"
LEAF_DOMAIN = b"ZOIKO-ASSURE-CHECKPOINT-LEAF-v1"
GENESIS_HASH = "0" * 64
MERKLE_ALGORITHM = "SHA-256/binary/sorted-leaves/odd-carry-up/v1"


def ledger_hash(tenant_id: str, seq: int, prev_hash: str, body: dict) -> str:
    return hashlib.sha256(LEDGER_DOMAIN + tenant_id.encode() + struct.pack(">Q", seq)
                          + bytes.fromhex(prev_hash) + canonical_json(body)).hexdigest()


def append_ledger_event(db: Session, *, tenant_id: str, evidence_id: str, event_type: str, body: dict,
                        actor_id: str, reason: str | None = None, batch_id: str | None = None) -> EvidenceLedgerEvent:
    """Serialized atomic append under the tenant-head lock (EVD-REQ-005)."""
    head = db.execute(select(TenantLedgerHead).where(TenantLedgerHead.tenant_id == tenant_id).with_for_update()).scalars().first()
    if head is None:
        head = TenantLedgerHead(tenant_id=tenant_id, current_seq=0, current_event_hash=GENESIS_HASH)
        db.add(head)
        db.flush()
    seq = head.current_seq + 1
    full_body = {"event_type": event_type, "evidence_id": evidence_id, "reason": reason, **body}
    full_body = json.loads(canonical_json(full_body))
    h = ledger_hash(tenant_id, seq, head.current_event_hash, full_body)
    ev = EvidenceLedgerEvent(tenant_id=tenant_id, ledger_seq=seq, evidence_id=evidence_id, event_type=event_type,
                             previous_event_hash=head.current_event_hash, event_hash=h, canonical_event_body=full_body,
                             actor_id=actor_id, reason=reason, record_batch_id=batch_id or new_id())
    db.add(ev)
    head.current_seq = seq
    head.current_event_hash = h
    head.updated_at = utcnow()
    db.flush()
    return ev


def capture_evidence(db: Session, *, tenant_id: str, entity_id: str, content: bytes, title: str, content_type: str,
                     origin_class: str, acquisition_method: str, provenance_class: str, coverage_from: datetime,
                     coverage_to: datetime, valid_until: datetime, actor_id: str, data_classification: str = "CUSTOMER_CONFIDENTIAL",
                     source_system: str | None = None, source_run_id: str | None = None,
                     source_observed_at: datetime | None = None, source_checksum: str | None = None,
                     idempotency_key: str | None = None) -> EvidenceArtifact:
    capture_key = sha256_hex(f"{tenant_id}|{source_run_id or ''}|{idempotency_key or new_id()}")
    existing = db.execute(select(EvidenceArtifact).where(EvidenceArtifact.tenant_id == tenant_id,
                                                         EvidenceArtifact.capture_key == capture_key)).scalars().first()
    if existing:
        if existing.plaintext_hmac != keys.hmac_plaintext(tenant_id, content):
            raise HTTPException(409, "IDEMPOTENCY_CONFLICT: same capture key with different payload")
        return existing
    evidence_id = new_id()
    plaintext_hmac = keys.hmac_plaintext(tenant_id, content)
    ciphertext, wrapped = keys.encrypt_payload(tenant_id, evidence_id, content)
    object_key = new_id().replace("-", "")  # opaque, non-PII object key
    path = OBJECT_DIR / f"{object_key}.bin"
    with open(path, "wb") as f:  # durable upload before manifest commit
        f.write(ciphertext)
        f.flush()
        os.fsync(f.fileno())
    try:
        seq = next_record_seq(db)
        art = EvidenceArtifact(
            evidence_id=evidence_id, tenant_id=tenant_id, entity_id=entity_id, home_cell=CELL_ID, capture_key=capture_key,
            title=title, origin_class=origin_class, acquisition_method=acquisition_method, provenance_class=provenance_class,
            source_system=source_system, source_run_id=source_run_id, coverage_from=ensure_utc(coverage_from),
            coverage_to=ensure_utc(coverage_to), source_observed_at=ensure_utc(source_observed_at),
            captured_at=utcnow(), valid_until=ensure_utc(valid_until), ledger_seq=0,
            data_classification=data_classification, content_type=content_type, size_bytes=len(content),
            plaintext_hmac=plaintext_hmac, fingerprint_key_version=keys.FINGERPRINT_KEY_VERSION,
            ciphertext_digest=hashlib.sha256(ciphertext).hexdigest(), object_key=object_key, wrapped_dek=wrapped,
            source_checksum=source_checksum, record_seq=seq, uploader_id=actor_id,
            review_status="PENDING_REVIEW" if origin_class == "HUMAN_UPLOAD" else None)
        db.add(art)
        db.flush()
        ev = append_ledger_event(db, tenant_id=tenant_id, evidence_id=evidence_id, event_type="MANIFEST_CREATED",
                                 actor_id=actor_id, body={
                                     "origin_class": origin_class, "acquisition_method": acquisition_method,
                                     "provenance_class": provenance_class, "coverage": [art.coverage_from, art.coverage_to],
                                     "plaintext_hmac": plaintext_hmac, "fingerprint_key_version": keys.FINGERPRINT_KEY_VERSION,
                                     "ciphertext_digest": art.ciphertext_digest, "size_bytes": len(content),
                                     "source_run_id": source_run_id, "source_checksum": source_checksum,
                                     "captured_at": art.captured_at})
        art.ledger_seq = ev.ledger_seq
        emit(db, event_type="evidence.captured", aggregate_type="EvidenceArtifact", aggregate_id=evidence_id,
             tenant_id=tenant_id, entity_id=entity_id, record_seq=seq,
             payload={"ledger_seq": ev.ledger_seq, "event_hash": ev.event_hash, "source_run_id": source_run_id,
                      "origin_class": origin_class})
        db.flush()
        return art
    except Exception:
        path.unlink(missing_ok=True)  # orphan cleanup: payload without committed manifest is not evidence
        raise


def lifecycle(db: Session, art: EvidenceArtifact) -> str:
    events = [e.event_type for e in db.execute(select(EvidenceLedgerEvent).where(
        EvidenceLedgerEvent.tenant_id == art.tenant_id, EvidenceLedgerEvent.evidence_id == art.evidence_id)
        .order_by(EvidenceLedgerEvent.ledger_seq)).scalars()]
    state = "ACTIVE"
    for t in events:
        if t == "SUPERSEDED":
            state = "SUPERSEDED"
        elif t == "INVALIDATED":
            state = "INVALIDATED"
        elif t == "PAYLOAD_ERASED":
            state = "ERASED"
    return state


def condition(db: Session, art: EvidenceArtifact, at: datetime | None = None) -> str:
    at = ensure_utc(at) or datetime.now(timezone.utc)
    lc = lifecycle(db, art)
    if lc == "ERASED":
        return "ERASED"
    if lc == "INVALIDATED":
        return "INVALID"
    if ensure_utc(art.valid_until) < at:
        return "STALE"
    return "CURRENT"


def active_hold(db: Session, art: EvidenceArtifact) -> LegalHold | None:
    return db.execute(select(LegalHold).where(LegalHold.evidence_id == art.evidence_id,
                                              LegalHold.status == "ACTIVE")).scalars().first()


def verify_evidence(db: Session, art: EvidenceArtifact, actor_id: str) -> dict:
    result = {"evidence_id": art.evidence_id, "checks": []}
    ok = True
    if art.object_key is None or art.wrapped_dek is None:
        result["checks"].append({"check": "PAYLOAD", "result": "PAYLOAD_ERASED"})
        result["result"] = "PAYLOAD_NOT_AVAILABLE"
        return result
    path = OBJECT_DIR / f"{art.object_key}.bin"
    if not path.exists():
        ok = False
        result["checks"].append({"check": "OBJECT_PRESENT", "result": "FAILED"})
    else:
        ct = path.read_bytes()
        d_ok = hashlib.sha256(ct).hexdigest() == art.ciphertext_digest
        result["checks"].append({"check": "CIPHERTEXT_DIGEST", "result": "VERIFIED" if d_ok else "FAILED"})
        try:
            pt = keys.decrypt_payload(art.tenant_id, art.evidence_id, ct, art.wrapped_dek)
            h_ok = keys.hmac_plaintext(art.tenant_id, pt, art.fingerprint_key_version) == art.plaintext_hmac
        except Exception:
            h_ok = False
        result["checks"].append({"check": "PLAINTEXT_HMAC", "result": "VERIFIED" if h_ok else "FAILED"})
        ok = ok and d_ok and h_ok
    result["result"] = "VERIFIED" if ok else "FAILED"
    append_ledger_event(db, tenant_id=art.tenant_id, evidence_id=art.evidence_id, event_type="VERIFIED",
                        actor_id=actor_id, body={"result": result["result"], "checks": result["checks"]})
    emit(db, event_type="evidence.verified", aggregate_type="EvidenceArtifact", aggregate_id=art.evidence_id,
         tenant_id=art.tenant_id, payload={"result": result["result"]})
    if not ok:
        _integrity_incident(db, art.tenant_id, f"Evidence {art.evidence_id} integrity verification FAILED", art.evidence_id)
    return result


def read_payload(art: EvidenceArtifact) -> bytes:
    if art.object_key is None or art.wrapped_dek is None:
        raise HTTPException(410, "Payload erased")
    ct = (OBJECT_DIR / f"{art.object_key}.bin").read_bytes()
    pt = keys.decrypt_payload(art.tenant_id, art.evidence_id, ct, art.wrapped_dek)
    if keys.hmac_plaintext(art.tenant_id, pt, art.fingerprint_key_version) != art.plaintext_hmac:
        raise HTTPException(500, "Integrity failure: plaintext HMAC mismatch")
    return pt


def erase_payload(db: Session, art: EvidenceArtifact, actor_id: str, reason: str) -> None:
    if active_hold(db, art):
        raise HTTPException(409, "Legal hold active: payload destruction blocked (EVD-REQ-020)")
    append_ledger_event(db, tenant_id=art.tenant_id, evidence_id=art.evidence_id, event_type="ERASE_REQUESTED",
                        actor_id=actor_id, reason=reason, body={})
    if art.object_key:
        (OBJECT_DIR / f"{art.object_key}.bin").unlink(missing_ok=True)
    art.wrapped_dek = None  # active-store crypto-shred of the per-evidence DEK
    art.object_key = None
    append_ledger_event(db, tenant_id=art.tenant_id, evidence_id=art.evidence_id, event_type="PAYLOAD_ERASED",
                        actor_id=actor_id, reason=reason,
                        body={"destruction": "object versions deleted; wrapped DEK destroyed",
                              "backup_note": "Immutable backups expire per retention; restores replay erasure tombstones."})
    emit(db, event_type="evidence.erased", aggregate_type="EvidenceArtifact", aggregate_id=art.evidence_id,
         tenant_id=art.tenant_id, payload={"reason": reason})


# ---------------------------------------------------------------- chain verification
def verify_chain(db: Session, tenant_id: str) -> dict:
    events = db.execute(select(EvidenceLedgerEvent).where(EvidenceLedgerEvent.tenant_id == tenant_id)
                        .order_by(EvidenceLedgerEvent.ledger_seq)).scalars().all()
    head = db.get(TenantLedgerHead, tenant_id)
    prev = GENESIS_HASH
    first_fail = None
    for i, e in enumerate(events, start=1):
        if e.ledger_seq != i or e.previous_event_hash != prev or \
                ledger_hash(tenant_id, e.ledger_seq, prev, e.canonical_event_body) != e.event_hash:
            first_fail = e.ledger_seq
            break
        prev = e.event_hash
    head_ok = head is not None and first_fail is None and head.current_seq == len(events) and head.current_event_hash == prev
    result = "VERIFIED" if (first_fail is None and (head_ok or not events)) else "FAILED"
    if head and result == "VERIFIED":
        head.last_verified_seq = head.current_seq
    if result == "FAILED":
        _integrity_incident(db, tenant_id, f"Tenant ledger verification failed at seq {first_fail or 'head'}", tenant_id)
    return {"tenant_id": tenant_id, "events_checked": len(events), "result": result, "first_failing_seq": first_fail,
            "head_seq": head.current_seq if head else 0, "head_hash": head.current_event_hash if head else None,
            "verifier_build": "za-ledger-verifier:1.0.0", "completed_at": utcnow().isoformat()}


def _integrity_incident(db, tenant_id, title, ref_id):
    emit(db, event_type="evidence.integrity_failed", aggregate_type="Integrity", aggregate_id=ref_id,
         tenant_id=tenant_id, payload={"title": title})
    db.add(Issue(tenant_id=tenant_id, title=title, issue_type="INTEGRITY_INCIDENT", severity="CRITICAL", priority="P0",
                 source_ref_type="Integrity", source_ref_id=ref_id, dedupe_key=f"integrity:{ref_id}:{title}"))


# ---------------------------------------------------------------- Merkle checkpointing
def leaf_hash(tenant_id: str, head_seq: int, head_hash: str) -> str:
    return hashlib.sha256(b"\x00" + LEAF_DOMAIN + tenant_id.encode() + struct.pack(">Q", head_seq)
                          + bytes.fromhex(head_hash)).hexdigest()


def _node(l: str, r: str) -> str:
    return hashlib.sha256(b"\x01" + bytes.fromhex(l) + bytes.fromhex(r)).hexdigest()


def merkle_root_and_proofs(leaves: list[str]) -> tuple[str, list[list[dict]]]:
    proofs: list[list[dict]] = [[] for _ in leaves]
    level = [(h, [i]) for i, h in enumerate(leaves)]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 == len(level):
                nxt.append(level[i])  # odd leaf carried up unchanged
                continue
            (lh, li), (rh, ri) = level[i], level[i + 1]
            for idx in li:
                proofs[idx].append({"position": "RIGHT", "sibling": rh})
            for idx in ri:
                proofs[idx].append({"position": "LEFT", "sibling": lh})
            nxt.append((_node(lh, rh), li + ri))
        level = nxt
    return level[0][0], proofs


def create_checkpoint(db: Session) -> LedgerCheckpoint | None:
    heads = db.execute(select(TenantLedgerHead)).scalars().all()
    changed = [h for h in heads if h.current_seq > (h.last_checkpointed_seq or 0)]
    if not changed:
        return None
    changed.sort(key=lambda h: h.tenant_id.encode())  # deterministic leaf ordering
    leaves = [{"tenant_id": h.tenant_id, "head_seq": h.current_seq, "head_hash": h.current_event_hash,
               "leaf": leaf_hash(h.tenant_id, h.current_seq, h.current_event_hash)} for h in changed]
    root, _ = merkle_root_and_proofs([l["leaf"] for l in leaves])
    now = utcnow()
    prev = db.execute(select(LedgerCheckpoint).order_by(LedgerCheckpoint.created_at.desc())).scalars().first()
    cp_id = new_id()
    manifest = {"checkpoint_id": cp_id, "cell_id": CELL_ID, "window_start": prev.window_end if prev else now,
                "window_end": now, "merkle_algorithm": MERKLE_ALGORITHM, "merkle_root": root,
                "leaf_count": len(leaves), "leaves": [l["leaf"] for l in leaves]}
    manifest_bytes = canonical_json(manifest)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    sig = keys.sign(manifest_bytes)
    tsa_url = os.environ.get("ZA_TSA_URL")
    tsa_status = "TSA_PENDING"
    tsa_note = ("No RFC 3161 TSA configured (ZA_TSA_URL). Root is cell-signed; external timestamp pending. "
                "No local time is presented as TSA time.") if not tsa_url else f"Queued for TSA {tsa_url}"
    worm = WORM_DIR / f"checkpoint-{cp_id}.json"
    worm.write_bytes(canonical_json({"manifest": manifest, "cell_signature": sig, "signer_key_id": keys.signer_key_id(),
                                     "public_key_pem": keys.public_key_pem(), "tsa_status": tsa_status}))
    os.chmod(worm, 0o444)  # write-once integrity store (dev stand-in for Object Lock)
    cp = LedgerCheckpoint(checkpoint_id=cp_id, cell_id=CELL_ID, window_start=manifest["window_start"], window_end=now,
                          leaf_count=len(leaves), leaves=json.loads(canonical_json(leaves)), merkle_algorithm=MERKLE_ALGORITHM,
                          merkle_root=root, manifest_hash=manifest_hash, cell_signature=sig,
                          signer_key_id=keys.signer_key_id(), tsa_status=tsa_status, tsa_note=tsa_note,
                          worm_object_ref=str(worm.name), status=tsa_status)
    db.add(cp)
    for h in changed:
        h.last_checkpointed_seq = h.current_seq
        h.last_checkpointed_hash = h.current_event_hash
    emit(db, event_type="evidence.chain_checkpointed", aggregate_type="LedgerCheckpoint", aggregate_id=cp_id,
         payload={"leaf_count": len(leaves), "merkle_root": root, "tsa_status": tsa_status})
    db.flush()
    return cp


def checkpoint_manifest(cp: LedgerCheckpoint) -> dict:
    return {"checkpoint_id": cp.checkpoint_id, "cell_id": cp.cell_id, "window_start": cp.window_start,
            "window_end": cp.window_end, "merkle_algorithm": cp.merkle_algorithm, "merkle_root": cp.merkle_root,
            "leaf_count": cp.leaf_count, "leaves": [l["leaf"] for l in cp.leaves]}


# ---------------------------------------------------------------- packages
def build_package(db: Session, *, tenant_id: str, entity_id: str, period_from: datetime, period_to: datetime,
                  evidence: list[EvidenceArtifact], snapshots: list[dict], evaluations: list[dict], ipe: list[dict],
                  include_payloads: bool, actor_id: str, record_cutoff_seq: int) -> EvidencePackage:
    """Signed, offline-verifiable assurance package (ZA-EVD-001 §17)."""
    cp = create_checkpoint(db) or db.execute(select(LedgerCheckpoint).order_by(LedgerCheckpoint.created_at.desc())).scalars().first()
    if cp is None:
        raise HTTPException(409, "No ledger checkpoint available")
    leaf = next((l for l in cp.leaves if l["tenant_id"] == tenant_id), None)
    if leaf is None:  # tenant head unchanged since an older checkpoint: find the latest containing it
        for older in db.execute(select(LedgerCheckpoint).order_by(LedgerCheckpoint.created_at.desc())).scalars():
            leaf = next((l for l in older.leaves if l["tenant_id"] == tenant_id), None)
            if leaf:
                cp = older
                break
    if leaf is None:
        raise HTTPException(409, "Tenant ledger has no checkpoint yet")
    idx = [l["leaf"] for l in cp.leaves].index(leaf["leaf"])
    _, proofs = merkle_root_and_proofs([l["leaf"] for l in cp.leaves])
    chain = db.execute(select(EvidenceLedgerEvent).where(EvidenceLedgerEvent.tenant_id == tenant_id,
                                                         EvidenceLedgerEvent.ledger_seq <= leaf["head_seq"])
                       .order_by(EvidenceLedgerEvent.ledger_seq)).scalars().all()
    package_id = new_id()
    members: dict[str, bytes] = {}
    verification = {"generated_at": utcnow(), "evidence": [], "redactions": [], "ledger": None,
                    "checkpoint": {"checkpoint_id": cp.checkpoint_id, "tsa_status": cp.tsa_status, "tsa_note": cp.tsa_note}}
    ledger_check = verify_chain(db, tenant_id)
    verification["ledger"] = ledger_check["result"]
    if ledger_check["result"] != "VERIFIED":
        raise HTTPException(409, "Ledger verification failed — package generation frozen (integrity incident)")
    for art in evidence:
        if art.ledger_seq > leaf["head_seq"]:
            continue
        entry = {"evidence_id": art.evidence_id, "lifecycle": lifecycle(db, art)}
        if include_payloads and art.object_key and art.wrapped_dek:
            pt = read_payload(art)  # HMAC verified internally before export (EVD-REQ-028)
            members[f"evidence/{art.evidence_id}"] = pt
            entry["plaintext_hmac_verified"] = True
            entry["payload_included"] = True
        else:
            entry["payload_included"] = False
            entry["note"] = "PAYLOAD_NOT_INCLUDED" if art.object_key else "PAYLOAD_ERASED"
        meta = {k: v for k, v in _art_meta(art).items()}
        members[f"manifests/{art.evidence_id}.json"] = canonical_json(meta)
        verification["evidence"].append(entry)
        append_ledger_event(db, tenant_id=tenant_id, evidence_id=art.evidence_id, event_type="EXPORT_INCLUDED",
                            actor_id=actor_id, body={"package_id": package_id})
    members["ledger-proofs/tenant-chain.json"] = canonical_json({
        "tenant_id": tenant_id, "genesis_hash": GENESIS_HASH, "domain": LEDGER_DOMAIN.decode(),
        "events": [{"ledger_seq": e.ledger_seq, "previous_event_hash": e.previous_event_hash, "event_hash": e.event_hash,
                    "canonical_event_body": e.canonical_event_body} for e in chain],
        "checkpointed_head": {"head_seq": leaf["head_seq"], "head_hash": leaf["head_hash"]}})
    members["merkle-proofs/tenant-head-inclusion.json"] = canonical_json({
        "tenant_id": tenant_id, "head_seq": leaf["head_seq"], "head_hash": leaf["head_hash"], "leaf": leaf["leaf"],
        "leaf_domain": LEAF_DOMAIN.decode(), "path": proofs[idx], "merkle_root": cp.merkle_root})
    members["timestamp/checkpoint.json"] = canonical_json({
        "manifest": checkpoint_manifest(cp), "cell_signature": cp.cell_signature, "signer_key_id": cp.signer_key_id,
        "tsa_status": cp.tsa_status, "tsa_token": None, "tsa_note": cp.tsa_note})
    members["assurance/snapshots.json"] = canonical_json(snapshots)
    members["assurance/applicability-evaluations.json"] = canonical_json(evaluations)
    members["assurance/ipe-proof.json"] = canonical_json(ipe)
    members["signer-public-key.pem"] = keys.public_key_pem().encode()
    members["verification-report.json"] = canonical_json(verification)
    manifest = {"format": "ZA-EVD-PKG/1.0", "package_id": package_id, "tenant_id": tenant_id, "entity_id": entity_id,
                "assurance_period": [ensure_utc(period_from), ensure_utc(period_to)], "record_cutoff_seq": record_cutoff_seq,
                "generator_version": "za-package-generator:1.0.0", "created_at": utcnow(),
                "dictionary_version": "ZA-GLS-001:1.0", "signer_key_id": keys.signer_key_id(),
                "evidence_ids": [a.evidence_id for a in evidence], "snapshot_ids": [s["snapshot_id"] for s in snapshots],
                "members": {p: hashlib.sha256(b).hexdigest() for p, b in sorted(members.items())},
                "disclaimer": "Integrity/structural verification only; this package does not assert legal compliance."}
    mbytes = canonical_json(manifest)
    sig = keys.sign(mbytes)
    path = PACKAGE_DIR / f"{package_id}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("package-manifest.json", mbytes)
        z.writestr("SIGNATURE", sig)
        for p, b in sorted(members.items()):
            z.writestr(p, b)
    path.write_bytes(buf.getvalue())
    pkg = EvidencePackage(package_id=package_id, tenant_id=tenant_id, entity_id=entity_id, period_from=period_from,
                          period_to=period_to, record_cutoff_seq=record_cutoff_seq,
                          scope={"evidence_ids": manifest["evidence_ids"], "snapshot_ids": manifest["snapshot_ids"]},
                          manifest_hash=hashlib.sha256(mbytes).hexdigest(), signer_key_id=keys.signer_key_id(),
                          file_ref=path.name, verification_report=json.loads(canonical_json(verification)),
                          created_by=actor_id)
    db.add(pkg)
    emit(db, event_type="evidence.package_signed", aggregate_type="EvidencePackage", aggregate_id=package_id,
         tenant_id=tenant_id, payload={"manifest_hash": pkg.manifest_hash, "signer_key_id": pkg.signer_key_id,
                                       "record_cutoff_seq": record_cutoff_seq})
    db.flush()
    return pkg


def _art_meta(a: EvidenceArtifact) -> dict:
    from ..platform import to_dict
    d = to_dict(a)
    d.pop("object_key", None)
    d.pop("capture_key", None)
    return d
