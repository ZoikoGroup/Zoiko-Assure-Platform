"""Cross-organisation shadow aggregates are never served to organisation principals (pre-push audit finding D1).

A release's ``shadow_summary`` and a rule draft's ``shadow_report`` are the content-operations shadow run
(rules.shadow -> applicability.shadow_impact, under system scope): counts over every organisation's subjects. ZA-API-001
§17: "GET /v1/content-releases/{id}/impact - Tenant-authorized impact summary after release/shadow processing. No
other-tenant aggregate detail", and content operations see shadow impact only "aggregate/minimized". These tests prove:

  * neither organisation (A: the seeded Customer Zero; B: a probe organisation of this module) receives the counts on
    any route that serialises them: /catalog/releases, /catalog/releases/{id}/impact, /rules/drafts, /rules/drafts/{id};
    the field stays in the response, null, so the response shape is unchanged;
  * each organisation still receives its own impact (tenant_impact, tenant_impacted_evaluations) and only its own;
  * platform content staff still receive the shadow counts.
Runs on SQLite and PostgreSQL 17.
"""
from __future__ import annotations

import json
import secrets

import pytest

from conftest import H, add_principal

TENANT_A, A_READER, A_AUDITOR = "tenant-zoiko-group", "actor-cco", "actor-ext-auditor"
TENANT_B, B_READER = "tenant-d1-probe-b", "d1-probe-b-cco"
CONTENT = "actor-content-analyst"  # Zoiko platform content staff (no organisation)
MARKER = f"d1-cross-tenant-{secrets.token_hex(6)}"


@pytest.fixture(scope="module")
def world(client):
    """Organisation B with a reader; the seeded release (whose shadow_summary is real content-operations output) and
    a seeded draft whose shadow report is set, for this module only, to counts carrying a unique marker. No release is
    created: published releases are immutable and other modules verify every release."""
    from app.db import CELL_ID, AdminSession
    from app.models import ContentRelease, LegalEntity, RuleDraft, Subject, Tenant
    shadow = {"subjects_evaluated": 41, "transition_buckets": {"NONE->APPLIES": 17, "APPLIES->DOES_NOT_APPLY": 3},
              "missing_fact_counts": {"f.probe": 9}, "adopted_determination_conflicts": 2, "execution_errors": 0,
              "note": MARKER}
    with AdminSession() as db:
        if db.get(Tenant, TENANT_B) is None:
            db.add(Tenant(tenant_id=TENANT_B, legal_name="D1 Probe Group B", contracting_region="UK"))
            db.flush()
            db.add(LegalEntity(entity_id="entity-d1-b", tenant_id=TENANT_B, legal_name="D1 Probe Ltd", country="GB",
                               home_cell_id=CELL_ID, subject_id="subj-d1-b"))
            db.add(Subject(subject_id="subj-d1-b", tenant_id=TENANT_B, entity_id="entity-d1-b",
                           subject_type="LEGAL_ENTITY", display_name="D1 Probe Ltd"))
            add_principal(db, actor_id=B_READER, tenant_id=TENANT_B, actor_type="HUMAN", display_name=B_READER,
                          roles=["CCO"])
        seeded = db.query(ContentRelease).filter(ContentRelease.shadow_summary.isnot(None)) \
            .order_by(ContentRelease.cell_received_seq).first()
        assert seeded is not None and (seeded.manifest or {}).get("rule_versions"), "the seed publishes a release"
        draft = db.query(RuleDraft).order_by(RuleDraft.draft_id).first()
        assert draft is not None, "the seed provides a rule draft"
        prior, draft.shadow_report = draft.shadow_report, shadow
        db.commit()
        rid, did, release_shadow = seeded.content_release_id, draft.draft_id, seeded.shadow_summary
    yield {"release": rid, "draft": did, "shadow": shadow, "release_shadow": release_shadow}
    with AdminSession() as db:  # the draft is mutable content: restore what the seed left there
        d = db.get(RuleDraft, did)
        d.shadow_report = prior
        db.commit()


def _get(client, path, actor):
    r = client.get(f"/api{path}", headers=H(actor))
    assert r.status_code == 200, (path, actor, r.text)
    return r.json()


def _routes(world):
    return ["/catalog/releases", f"/catalog/releases/{world['release']}/impact", "/rules/drafts",
            f"/rules/drafts/{world['draft']}"]


@pytest.mark.parametrize("actor", [A_READER, A_AUDITOR, B_READER])
def test_no_organisation_principal_receives_cross_tenant_shadow_counts(client, world, actor):
    release_shadow = json.dumps(world["release_shadow"], sort_keys=True)
    for path in _routes(world):
        r = client.get(f"/api{path}", headers=H(actor))
        if r.status_code == 403 and actor == A_AUDITOR and path.startswith("/rules/"):
            assert MARKER not in r.text  # outside the external-auditor allowlist altogether (IAM §14)
            continue
        assert r.status_code == 200, (path, actor, r.text)
        body = r.json()
        text = json.dumps(body)
        assert MARKER not in text, (path, actor)  # the draft's cross-tenant counts: on no route, in no field
        assert "subjects_evaluated" not in text and "transition_buckets" not in text, (path, actor)
        assert release_shadow not in json.dumps(body, sort_keys=True), (path, actor)
    releases = _get(client, "/catalog/releases", actor)
    probe = next(r for r in releases if r["content_release_id"] == world["release"])
    assert "shadow_summary" in probe and probe["shadow_summary"] is None  # same shape, no content
    impact = _get(client, f"/catalog/releases/{world['release']}/impact", actor)
    assert impact["shadow_summary"] is None
    if actor == A_AUDITOR:
        return
    draft = _get(client, f"/rules/drafts/{world['draft']}", actor)
    assert "shadow_report" in draft and draft["shadow_report"] is None
    listed = next(d for d in _get(client, "/rules/drafts", actor) if d["draft_id"] == world["draft"])
    assert listed["shadow_report"] is None


def test_platform_content_staff_keep_the_shadow_counts(client, world):
    releases = _get(client, "/catalog/releases", CONTENT)
    assert next(r for r in releases if r["content_release_id"] == world["release"])["shadow_summary"] == \
        world["release_shadow"]
    impact = _get(client, f"/catalog/releases/{world['release']}/impact", CONTENT)
    assert impact["shadow_summary"] == world["release_shadow"] and impact["tenant_impact"] is None  # INV-14
    assert _get(client, f"/rules/drafts/{world['draft']}", CONTENT)["shadow_report"] == world["shadow"]
    listed = next(d for d in _get(client, "/rules/drafts", CONTENT) if d["draft_id"] == world["draft"])
    assert listed["shadow_report"] == world["shadow"]


def test_each_organisation_keeps_its_own_impact_and_only_its_own(client, world):
    a = _get(client, f"/catalog/releases/{world['release']}/impact", A_READER)["tenant_impact"]
    b = _get(client, f"/catalog/releases/{world['release']}/impact", B_READER)["tenant_impact"]
    assert a is not None and b is not None
    a_list = next(r for r in _get(client, "/catalog/releases", A_READER) if r["content_release_id"] == world["release"])
    b_list = next(r for r in _get(client, "/catalog/releases", B_READER) if r["content_release_id"] == world["release"])
    assert a_list["tenant_impacted_evaluations"] > 0  # A's own evaluations under the seeded rule versions
    assert b_list["tenant_impacted_evaluations"] == 0  # B has none, and never sees A's
    a_ids, b_ids = json.dumps(a), json.dumps(b)
    from app.db import AdminSession
    from app.models import ApplicabilityEvaluation
    with AdminSession() as db:
        a_evals = [e.evaluation_id for e in db.query(ApplicabilityEvaluation).filter_by(tenant_id=TENANT_A).limit(50)]
    assert a_evals and not any(e in b_ids for e in a_evals)  # no organisation-A record identifier in B's report
    assert TENANT_A not in b_ids and TENANT_B not in a_ids


def test_shadow_counts_are_not_reachable_by_an_organisation_on_platform_only_routes(client, world):
    """The routes that compute or return a shadow report are platform functions: an organisation principal is refused
    before anything is read or computed."""
    for path in (f"/rules/drafts/{world['draft']}/shadow", f"/rules/drafts/{world['draft']}/sandbox"):
        r = client.post(f"/api{path}", json={"facts": {}} if path.endswith("sandbox") else None, headers=H(A_READER))
        assert r.status_code == 403, (path, r.text)
        assert MARKER not in r.text
