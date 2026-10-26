"""Hardening of the public package verifier: POST /api/packages/verify-upload and verifier/verify_package.py.

Access policy: the endpoint is public on purpose (ZA-EVD-001 §18: auditors and regulators verify packages without
logging into Assure), so the guards are resource limits, not authentication. Fixtures are small and built in memory
from one real signed package; limits are lowered through their environment variables rather than with huge inputs.
"""
import asyncio
import io
import struct
import zipfile

import pytest

from conftest import H

URL = "/api/packages/verify-upload"
OK = ("PASS", "PASS_WITH_WARNINGS")


@pytest.fixture(scope="module")
def package(client) -> bytes:
    from app.engines.evidence import PACKAGE_DIR
    obls = client.get("/api/obligations", headers=H("actor-cco")).json()
    pkg = client.post("/api/packages", headers=H("actor-audit"), json={
        "entity_id": "entity-eu", "period_from": "2026-07-01T00:00:00Z", "period_to": "2026-10-01T00:00:00Z",
        "obligation_lineage_ids": [o["lineage_id"] for o in obls if o["latest_snapshot"]]}).json()
    return (PACKAGE_DIR / pkg["file_ref"]).read_bytes()


def upload(client, data: bytes, headers=None):
    return client.post(URL, files={"file": ("package.zip", data, "application/zip")}, headers=headers or {})


def rebuild(raw: bytes, replace=None, drop=(), extra=()) -> bytes:
    """Copy a package, replacing or dropping members and appending (ZipInfo or name, bytes) pairs."""
    replace = replace or {}
    src, buf = zipfile.ZipFile(io.BytesIO(raw)), io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name in src.namelist():
            if name not in drop:
                out.writestr(name, replace.get(name, src.read(name)))
        for info, data in extra:
            out.writestr(info, data)
    return buf.getvalue()


def patch_central(raw: bytes, name: str, offset: int, fmt: str, value: int) -> bytes:
    """Overwrite one field of `name`'s central-directory header (metadata an attacker controls)."""
    data, pos = bytearray(raw), 0
    while (pos := raw.find(b"PK\x01\x02", pos)) != -1:
        n = struct.unpack_from("<H", raw, pos + 28)[0]
        if raw[pos + 46:pos + 46 + n].decode() == name:
            struct.pack_into(fmt, data, pos + offset, value)
            return bytes(data)
        pos += 4
    raise AssertionError(f"{name} not found")


def failed(r, code=None):
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "FAIL" and body["result"] not in OK, body
    if code:
        assert code in body["reason_codes"], body
    return body


# ---------------------------------------------------------------- access policy and legitimate packages
def test_valid_package_verifies_without_login_and_returns_only_the_report(client, package):
    r = upload(client, package)  # no credentials at all
    assert r.status_code == 200 and r.json()["result"] in OK, r.text
    assert set(r.json()) == {"result", "reason_codes", "warnings", "steps", "package_id", "statement"}
    # Credentials are neither required nor consulted: an invalid token changes nothing.
    assert upload(client, package, {"Authorization": "Bearer not-a-token"}).json()["result"] in OK


def test_upload_at_the_size_limit_is_accepted_and_one_byte_over_is_refused(client, package, monkeypatch):
    monkeypatch.setenv("ZA_VERIFY_MAX_ARCHIVE_BYTES", str(len(package)))
    assert upload(client, package).json()["result"] in OK
    monkeypatch.setenv("ZA_VERIFY_MAX_ARCHIVE_BYTES", str(len(package) - 1))
    assert upload(client, package).status_code == 413


def test_oversized_body_is_refused_with_413(client, monkeypatch):
    monkeypatch.setenv("ZA_VERIFY_MAX_ARCHIVE_BYTES", "1000")
    r = upload(client, b"x" * 200_000)
    assert r.status_code == 413 and "limit" in r.json()["detail"]


def test_oversized_chunked_body_without_content_length_is_refused(client, monkeypatch):
    monkeypatch.setenv("ZA_VERIFY_MAX_ARCHIVE_BYTES", "1000")
    head = b'--b\r\nContent-Disposition: form-data; name="file"; filename="p.zip"\r\nContent-Type: application/zip\r\n\r\n'

    def body():
        yield head
        for _ in range(100):
            yield b"x" * 10_000
        yield b"\r\n--b--\r\n"
    r = client.post(URL, content=body(), headers={"content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413


def _run_limited(chunks: int, content_length: bytes | None):
    """Drive app.limits.BodyLimit directly: count how much of an endless body it consumes."""
    from app.limits import BodyLimit
    pulls, sent = [0], []

    async def receive():
        pulls[0] += 1
        return {"type": "http.request", "body": b"x" * 100, "more_body": pulls[0] < chunks}

    async def app(scope, recv, send):  # like a framework: read the whole body, then answer 200
        while (await recv()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(message):
        sent.append(message)

    headers = [(b"content-length", content_length)] if content_length is not None else []
    asyncio.run(BodyLimit(app, {"/up": lambda: 1000})({"type": "http", "path": "/up", "headers": headers}, receive, send))
    return pulls[0], sent[0]["status"]


def test_body_limit_stops_reading_early_whatever_content_length_says():
    assert _run_limited(10_000, None) == (11, 413)          # absent: stops after 1,100 of 1,000,000 bytes
    assert _run_limited(10_000, b"50") == (11, 413)         # understated
    assert _run_limited(10_000, b"5000000") == (0, 413)     # overstated: refused before reading anything
    assert _run_limited(5, None) == (5, 200)                # within the limit: untouched


# ---------------------------------------------------------------- malformed, truncated and incomplete packages
def test_non_zip_input_fails_cleanly(client):
    failed(upload(client, b"definitely not a zip"), "PARSE")
    failed(upload(client, b""), "PARSE")


def test_truncated_and_corrupt_archives_fail_cleanly(client, package):
    failed(upload(client, package[: len(package) // 2]))
    corrupt = bytearray(package)
    for i in range(200, 260):  # inside the first member's compressed data
        corrupt[i] ^= 0xFF
    failed(upload(client, bytes(corrupt)))


def test_missing_required_member_is_a_deterministic_fail_not_a_500(client, package):
    body = failed(upload(client, rebuild(package, drop={"ledger-proofs/tenant-chain.json"})), "PARSE")
    assert "ledger-proofs/tenant-chain.json" in body["steps"][-1]["detail"]


def test_malformed_json_and_invalid_encoding_fail_cleanly(client, package):
    failed(upload(client, rebuild(package, replace={"ledger-proofs/tenant-chain.json": b"{not json"})), "MALFORMED")
    failed(upload(client, rebuild(package, replace={"timestamp/checkpoint.json": b'["valid json, wrong shape"]'})), "MALFORMED")
    failed(upload(client, rebuild(package, replace={"SIGNATURE": b"\xff\xfe\xfd"})), "MALFORMED")


def test_tampered_member_still_fails_the_digest_step(client, package):
    body = failed(upload(client, rebuild(package, replace={"assurance/snapshots.json": b"[]"})))
    assert "3. Member digests" in body["reason_codes"]


# ---------------------------------------------------------------- resource limits and ZIP bombs
def test_too_many_members(client, package, monkeypatch):
    monkeypatch.setenv("ZA_VERIFY_MAX_MEMBERS", "5")
    failed(upload(client, package), "LIMIT_EXCEEDED")


def test_member_over_the_decompressed_size_limit(client, package, monkeypatch):
    monkeypatch.setenv("ZA_VERIFY_MAX_MEMBER_BYTES", "100")
    failed(upload(client, package), "LIMIT_EXCEEDED")


def test_total_decompressed_bytes_over_the_limit(client, package, monkeypatch):
    monkeypatch.setenv("ZA_VERIFY_MAX_TOTAL_BYTES", "500")
    failed(upload(client, package), "LIMIT_EXCEEDED")


def test_high_compression_ratio_bomb_is_refused_before_decompression(client, package):
    bomb = rebuild(package, extra=[("evidence/bomb", b"\0" * (4 * 1024 * 1024))])  # 4 MiB of zeros, ~4 KiB compressed
    assert len(bomb) < len(package) + 64 * 1024
    failed(upload(client, bomb), "LIMIT_EXCEEDED")


def test_actual_bytes_are_counted_not_just_declared_sizes(client, package, monkeypatch):
    # JSON members are capped on the bytes actually decompressed, after the directory checks have passed.
    monkeypatch.setenv("ZA_VERIFY_MAX_JSON_BYTES", "10")
    failed(upload(client, package), "LIMIT_EXCEEDED")
    # A directory that understates a member's size cannot smuggle data through: the read stops at the declared size
    # and the CRC check fails.
    failed(upload(client, patch_central(package, "assurance/snapshots.json", 24, "<I", 10)))


# ---------------------------------------------------------------- ambiguous, unsafe and unsupported entries
def test_duplicate_member_names_are_refused(client, package):
    dup = rebuild(package, extra=[("SIGNATURE", b"00")])
    with zipfile.ZipFile(io.BytesIO(dup)) as z:
        assert z.namelist().count("SIGNATURE") == 2
    failed(upload(client, dup), "PARSE")


@pytest.mark.parametrize("name", ["../escape.txt", "/etc/absolute.txt", "C:/windows/drive.txt", "dir\\backslash.txt",
                                  "a/../../b.txt", "./dot.txt"])
def test_traversal_and_absolute_member_paths_are_refused(client, package, tmp_path, name):
    # zipfile on Windows rewrites "\" to "/" when writing, so store a neutral name and patch the raw bytes to `name`.
    stand_in = name.replace("\\", "/")
    raw = rebuild(package, extra=[(stand_in, b"payload")])
    raw = raw.replace(stand_in.encode(), name.encode()) if stand_in != name else raw
    failed(upload(client, raw), "PARSE")
    assert not any(tmp_path.parent.glob("escape.txt"))  # the verifier never writes members anywhere


def test_encrypted_member_is_refused(client, package):
    failed(upload(client, patch_central(package, "SIGNATURE", 8, "<H", 0x1)), "UNSUPPORTED")


def test_unsupported_compression_is_refused(client, package):
    info = zipfile.ZipInfo("evidence/bzip2")
    info.compress_type = zipfile.ZIP_BZIP2
    failed(upload(client, rebuild(package, extra=[(info, b"data")])), "UNSUPPORTED")


def test_symbolic_link_entry_is_refused(client, package):
    link = zipfile.ZipInfo("evidence/link")
    link.external_attr = 0o120777 << 16  # S_IFLNK
    failed(upload(client, rebuild(package, extra=[(link, b"/etc/passwd")])), "UNSUPPORTED")


def test_verifier_cli_function_applies_the_same_limits_directly(package, monkeypatch):
    from app.routers.evidence import _load_verifier
    v = _load_verifier()
    assert v.verify(package)["result"] in OK
    monkeypatch.setenv("ZA_VERIFY_MAX_ARCHIVE_BYTES", "100")
    assert v.verify(package)["reason_codes"] == ["LIMIT_EXCEEDED"]
