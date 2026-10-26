"""End-to-end invariant tests over the API (seeded Customer Zero)."""
from conftest import H


def test_seeded_assurance_and_no_generic_compliance_state(client):
    obls = client.get("/api/obligations", headers=H("actor-cco")).json()
    states = {o["definition"]["key"]: o["latest_snapshot"]["state"] for o in obls if o["latest_snapshot"]}
    assert states["DORA_ICT_RISK_FRAMEWORK"] == "NOT_PROVEN"
    assert states["EUAIA_TRANSPARENCY_INTERACTION"] == "PROVEN"
    assert "COMPLIANT" not in str(client.get("/api/dictionary").json()["dimensions"])


def test_inv01_agent_cannot_attest_even_with_permission(client):
    dets = client.get("/api/applicability/determinations", headers=H("actor-cco")).json()
    prop = next(d for d in dets if d["state"] == "PROPOSED")
    r = client.post(f"/api/applicability/determinations/{prop['lineage_id']}/attest", headers=H("actor-agent"),
                    json={"decision": "ADOPT", "authority_basis": "none"})
    assert r.status_code == 403 and "INV-01" in r.text


def test_uncertain_cannot_be_adopted_and_creates_fact_request(client):
    frs = client.get("/api/fact-requests", headers=H("actor-cco")).json()
    assert any(f["fact_key"] == "ai.high_risk_basis" and f["status"] == "OPEN" for f in frs)


def test_cross_tenant_and_content_staff_isolation(client):
    assert client.get("/api/obligations", headers=H("actor-content-analyst")).status_code == 403
    assert client.get("/api/evidence/does-not-exist", headers=H("actor-cco")).status_code == 404


def test_bitemporal_correction_preserves_as_known_then(client):
    wm_before = client.get("/api/watermark").json()["record_seq"]
    r = client.post("/api/subjects/subj-entity-uk/facts", headers=H("actor-analyst"), json={
        "fact_key": "entity.established_in_eu", "value": True, "valid_from": "2025-01-01T00:00:00Z",
        "change_kind": "RETROACTIVE_CORRECTION", "reason": "Test correction"})
    assert r.status_code == 200
    odef = next(o for o in client.get("/api/catalog/obligation-definitions", headers=H("actor-cco")).json() if o["key"] == "DORA_ICT_RISK_FRAMEWORK")
    wm_after = client.get("/api/watermark").json()["record_seq"]
    cmp = client.get("/api/history/compare", headers=H("actor-cco"), params={
        "subject_id": "subj-entity-uk", "obligation_definition_id": odef["obligation_definition_id"],
        "valid_at": "2026-07-01T00:00:00Z", "left_seq": wm_before, "right_seq": wm_after}).json()
    assert cmp["left"]["outcome"] == "DOES_NOT_APPLY" and cmp["right"]["outcome"] == "APPLIES"
    assert any(d["type"] == "KNOWLEDGE_CORRECTED" for d in cmp["differences"])


def test_exception_never_changes_state_and_manual_green_impossible(client):
    obl = next(o for o in client.get("/api/obligations", headers=H("actor-cco")).json() if o["definition"]["key"] == "DORA_ICT_RISK_FRAMEWORK")
    snap = client.post(f"/api/obligations/{obl['lineage_id']}/snapshots", headers=H("actor-cco"),
                       json={"period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z"}).json()
    assert snap["state"] == "NOT_PROVEN" and snap["exception_treatments"]
    rep = client.get(f"/api/snapshots/{snap['snapshot_id']}/reproduce", headers=H("actor-audit")).json()
    assert rep["reproducible"]


def test_evidence_integrity_package_and_offline_verifier(client):
    v = client.post("/api/ledger/verify", headers=H("actor-audit")).json()
    assert v["result"] == "VERIFIED"
    obls = client.get("/api/obligations", headers=H("actor-cco")).json()
    lin = [o["lineage_id"] for o in obls if o["latest_snapshot"]]
    pkg = client.post("/api/packages", headers=H("actor-audit"), json={
        "entity_id": "entity-eu", "period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z",
        "obligation_lineage_ids": lin}).json()
    out = client.post(f"/api/packages/{pkg['package_id']}/verify", headers=H("actor-audit")).json()
    assert out["result"] in ("PASS", "PASS_WITH_WARNINGS"), out
    # tamper: flip a byte in one member -> FAIL
    import io, zipfile
    from app.engines.evidence import PACKAGE_DIR
    from app.engines import keys
    raw = (PACKAGE_DIR / pkg["file_ref"]).read_bytes()
    zin = zipfile.ZipFile(io.BytesIO(raw))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in zin.namelist():
            data = zin.read(n)
            if n == "assurance/snapshots.json":
                data = data.replace(b"NOT_PROVEN", b"PROVEN_____")
            z.writestr(n, data)
    from app.routers.evidence import _load_verifier
    assert _load_verifier().verify(buf.getvalue(), keys.public_key_pem().encode())["result"] == "FAIL"


def test_hold_blocks_erasure(client):
    ev = client.get("/api/evidence", headers=H("actor-cco")).json()[0]
    assert client.post(f"/api/evidence/{ev['evidence_id']}/hold", headers=H("actor-gc"), json={"reason": "litigation"}).status_code == 200
    r = client.post(f"/api/evidence/{ev['evidence_id']}/erase", headers=H("actor-gc"), json={"reason": "DSR"})
    assert r.status_code == 409


def test_pdp_is_deterministic_and_fail_closed(client):
    ok = client.post("/api/ai/decisions", headers=H("actor-svc"), json={
        "agent_subject_id": "subj-ai-payables", "action_class": "Prepare regulatory filing draft",
        "scope": "UK legal entity; VAT filings only", "requested_authority": "A2"}).json()
    assert ok["decision"] == "PERMIT"
    deny = client.post("/api/ai/decisions", headers=H("actor-svc"), json={
        "agent_subject_id": "subj-ai-payables", "action_class": "Submit regulatory filing",
        "scope": "UK legal entity; VAT filings only", "requested_authority": "A3"}).json()
    assert deny["decision"] == "DENY"


# ---------------------------------------------------------------- interactive authentication
def test_login_logout_and_token_access(client):
    from app.auth import DEMO_PASSWORD
    bad = client.post("/api/auth/login", json={"email": "actor-cco@zoiko.example", "password": "wrong-password"})
    assert bad.status_code == 401 and "incorrect" in bad.json()["detail"]
    unknown = client.post("/api/auth/login", json={"email": "nobody@zoiko.example", "password": "x"})
    assert unknown.status_code == 401 and unknown.json()["detail"] == bad.json()["detail"]  # no account enumeration
    r = client.post("/api/auth/login", json={"email": "ACTOR-CCO@zoiko.example", "password": DEMO_PASSWORD})
    assert r.status_code == 200
    tok = {"Authorization": f"Bearer {r.json()['token']}"}
    assert client.get("/api/auth/session", headers=tok).json()["actor"]["actor_id"] == "actor-cco"
    assert client.get("/api/dashboard", headers=tok).status_code == 200
    tampered = {"Authorization": tok["Authorization"][:-3] + "abc"}
    assert client.get("/api/dashboard", headers=tampered).status_code == 401
    assert client.post("/api/auth/logout", headers=tok).status_code == 200
    assert client.get("/api/dashboard", headers=tok).status_code == 401  # revoked server-side


def test_agents_cannot_sign_in_and_lockout(client):
    from app.auth import DEMO_PASSWORD
    # agents/services hold no interactive credential (INV-01)
    assert client.post("/api/auth/login", json={"email": "actor-agent@zoiko.example", "password": DEMO_PASSWORD}).status_code == 401
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "actor-gc@zoiko.example", "password": "nope"})
    locked = client.post("/api/auth/login", json={"email": "actor-gc@zoiko.example", "password": DEMO_PASSWORD})
    assert locked.status_code == 423


def test_change_password_revokes_other_sessions(client):
    from app.auth import DEMO_PASSWORD
    a = client.post("/api/auth/login", json={"email": "actor-audit@zoiko.example", "password": DEMO_PASSWORD}).json()["token"]
    b = client.post("/api/auth/login", json={"email": "actor-audit@zoiko.example", "password": DEMO_PASSWORD}).json()["token"]
    weak = client.post("/api/auth/password", headers={"Authorization": f"Bearer {a}"},
                       json={"current_password": DEMO_PASSWORD, "new_password": "short"})
    assert weak.status_code == 422
    ok = client.post("/api/auth/password", headers={"Authorization": f"Bearer {a}"},
                     json={"current_password": DEMO_PASSWORD, "new_password": "NewSecret2026x"})
    assert ok.status_code == 200
    assert client.get("/api/auth/session", headers={"Authorization": f"Bearer {b}"}).status_code == 401
    assert client.get("/api/auth/session", headers={"Authorization": f"Bearer {a}"}).status_code == 200


# ---------------------------------------------------------------- pre-development cleanup guards
def test_clients_cannot_choose_provenance_or_cell_and_no_demo_password_is_exposed(client, monkeypatch):
    fact = {"fact_key": "entity.established_in_eu", "value": True, "valid_from": "2025-01-01T00:00:00Z"}
    # provenance is server-derived (APP-REQ-005, API-REQ-014)
    assert client.post("/api/subjects/subj-entity-uk/facts", headers=H("actor-analyst"),
                       json={**fact, "provenance_class": "P1_SYSTEM_DIRECT"}).status_code == 422
    # system-sourced facts must not arrive through the human-facing endpoint
    assert client.post("/api/subjects/subj-entity-uk/facts", headers=H("actor-svc"), json=fact).status_code == 403
    # clients never select the authoritative cell (TEN-REQ-005, API-REQ-009)
    assert client.post("/api/org/entities", headers=H("actor-admin"),
                       json={"legal_name": "Probe Ltd", "country": "GB", "home_cell_id": "uk-south-cell-b"}).status_code == 422
    assert "password" not in client.get("/api/auth/demo-accounts").json()
    # client-posted source runs are disabled outside dev/test (audit F4)
    from app.routers import integration
    monkeypatch.setattr(integration, "DEV_HEADER", False)
    assert client.post("/api/ipe/source-runs", headers=H("actor-owner"),
                       json={"installation_id": "x", "ipe_policy_version_id": "y", "payload": {}}).status_code == 403
