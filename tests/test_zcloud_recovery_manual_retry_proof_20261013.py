"""Offline HMAC manual retry proof and nonce replay safety acceptance tests."""
import copy
import hashlib
import hmac
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import zcloud_recovery_manual_retry_proof_20261013 as manual

SHA = "a" * 40
OTHER = "b" * 40
SECRET = b"owner-private-test-only-signing-key-32bytes"
T = 20000
ACTOR = "zCloud-Owner"
NONCE = "12345678-1234-4234-8234-123456789abc"


def envelope():
    return {
        "schema": manual.SCHEMA, "repository": manual.REPOSITORY,
        "action": manual.ACTION, "sha": SHA, "actor": ACTOR,
        "reason": "Approved one-shot recovery investigation",
        "nonce": NONCE, "issued_at": T - 60, "expires_at": T + 300,
    }


def sign(body=envelope(), secret=SECRET):
    return hmac.new(secret, json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False
    ).encode("utf-8"), hashlib.sha256).hexdigest()


def evaluate(body=None, sig=None, **updates):
    source = envelope() if body is None else body
    return manual.check_manual_retry_proof(
        source, sign(source) if sig is None else sig,
        secret=updates.get("secret", SECRET),
        expected_sha=updates.get("expected_sha", SHA),
        authenticated_actor=updates.get("authenticated_actor", ACTOR),
        now=updates.get("now", T),
    )


class ManualRetryProofTests(unittest.TestCase):
    def assert_denied(self, payload, *, expected=None, **context):
        result = evaluate(payload, **context)
        self.assertEqual(result["decision"], "DENY")
        self.assertIs(result["authorized"], False)
        self.assertIs(result["dispatch_allowed"], False)
        self.assertIs(result["mutations_performed"], False)
        self.assertIs(result["manual_reservation_candidate"], False)
        if expected is not None:
            self.assertEqual(result["reason"], expected)

    def test_valid_short_lived_proof_is_only_nonce_reservation_candidate(self):
        r = evaluate()
        self.assertEqual(r["decision"], "READY_FOR_MANUAL_NONCE_RESERVATION")
        self.assertTrue(r["manual_reservation_candidate"])
        self.assertIs(r["requires_atomic_shared_claim"], True)
        self.assertIs(r["requires_exact_main_recheck"], True)
        self.assertIs(r["requires_serialized_owner_approval"], True)
        self.assertIs(r["authorized"], False)
        self.assertIs(r["dispatch_allowed"], False)
        self.assertEqual(r["reservation_key"], "manual/" + SHA + "/" + NONCE)

    def test_wrong_authentic_actor_denied_even_signed(self):
        self.assert_denied(envelope(), authenticated_actor="attacker",
                           expected="actor_not_authenticated")

    def test_wrong_sha_is_denied_even_signed(self):
        self.assert_denied(envelope(), expected_sha=OTHER, expected="sha_mismatch")

    def test_bad_audience_or_action(self):
        for field, value in (("repository", "evil/target"),
                             ("action", "deploy"),
                             ("schema", "v2")):
            e = envelope()
            e[field] = value
            with self.subTest(field=field):
                self.assert_denied(e, expected="wrong_audience_or_action")

    def test_missing_or_extra_field(self):
        e = envelope()
        e.pop("nonce")
        self.assert_denied(e, expected="missing_or_extra_fields")
        e = envelope()
        e["force_dispatch"] = True
        self.assert_denied(e, expected="missing_or_extra_fields")

    def test_bad_nonce(self):
        for n in ("ABC", "0" * 36,
                  "12345678-1234-1234-8234-123456789abc",
                  "12345678-1234-4234-1234-123456789abc",
                  "12345678-1234-4234-8234-123456789ABC"):
            e = envelope()
            e["nonce"] = n
            with self.subTest(nonce=n):
                self.assert_denied(e, expected="invalid_nonce")

    def test_issued_timestamp_requires_integer_without_bool(self):
        for val in (True, False, 0, "20000", 2.0, None):
            e = envelope()
            e["issued_at"] = val
            with self.subTest(value=val):
                self.assert_denied(e, expected="invalid_lifetime")

    def test_ttl_over_ten_minutes_denied(self):
        e = envelope()
        e["expires_at"] = e["issued_at"] + 601
        self.assert_denied(e, expected="invalid_lifetime")

    def test_expired_and_future_proof_denied(self):
        e = envelope()
        self.assert_denied(e, expected="expired_or_future_proof", now=e["expires_at"] + 1)
        self.assert_denied(e, expected="expired_or_future_proof", now=e["issued_at"] - 61)

    def test_invalid_actor_name_denied(self):
        for value in ("", " owner", "owner\n", "a" * 40, [], None):
            e = envelope()
            e["actor"] = value
            with self.subTest(value=value):
                self.assert_denied(e, authenticated_actor=value, expected="actor_not_authenticated")

    def test_invalid_reason_denied(self):
        for reason in ("too short", "x" * 241, "a valid line\nsecond", "okay enough\x00but invalid"):
            e = envelope()
            e["reason"] = reason
            with self.subTest(reason=reason[:10]):
                self.assert_denied(e, expected="invalid_reason")

    def test_changed_signed_fields_cannot_reuse_old_signature(self):
        original = envelope()
        valid_sig = sign(original)
        for field, value in (
            ("nonce", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            ("reason", "Different owner-approved reason"),
            ("expires_at", original["expires_at"] + 60),
            ("actor", "OtherOwner"),
        ):
            e = dict(original)
            e[field] = value
            with self.subTest(field=field):
                r = evaluate(e, sig=valid_sig,
                             authenticated_actor=e["actor"])
                self.assertEqual(r["decision"], "DENY")
                self.assertIs(r["authorized"], False)

    def test_signature_format_cannot_be_nonstring_or_uppercase(self):
        for sig in ("no", "A" * 64, True, None, [], {}):
            e = envelope()
            # evaluate uses computed signature when sig=None; call directly.
            r = manual.check_manual_retry_proof(
                e, sig, secret=SECRET, expected_sha=SHA,
                authenticated_actor=ACTOR, now=T,
            )
            self.assertEqual(r["reason"], "invalid_signature_format")

    def test_invalid_signature_secret_denied(self):
        e = envelope()
        r = evaluate(e, sig="0" * 64)
        self.assertEqual(r["reason"], "signature_mismatch")
        self.assert_denied(e, secret=b"short", expected="untrusted_signing_key")
        self.assert_denied(e, secret="string-not-secret", expected="untrusted_signing_key")

    def test_signature_and_reason_not_returned_in_receipt(self):
        r = evaluate()
        serialized = json.dumps(r)
        self.assertNotIn(envelope()["reason"], serialized)
        self.assertNotIn(sign(), serialized)
        self.assertNotIn(SECRET.decode(), serialized)

    def test_credential_free_pure_source(self):
        text = (ROOT / "scripts/zcloud_recovery_manual_retry_proof_20261013.py").read_text()
        for forbidden in ("GITHUB_TOKEN", "POST", "subprocess", "urllib",
                          "requests.", "systemctl", "os.environ", "sqlite3"):
            self.assertNotIn(forbidden, text)

    def test_two_distinct_nonces_produce_distinct_reservation_keys(self):
        e = envelope()
        other = dict(e)
        other["nonce"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        self.assertNotEqual(evaluate(e)["reservation_key"], evaluate(other)["reservation_key"])

    def test_simulated_global_atomic_nonce_claim_prevents_64_replays(self):
        key = evaluate()["reservation_key"]
        existing = set()
        lock = Lock()
        def attempt(_):
            proof = evaluate()
            self.assertFalse(proof["authorized"])
            with lock:
                if key in existing:
                    return False
                existing.add(key)
                return True
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(attempt, range(64)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(existing), 1)

    def test_replay_cannot_be_proven_safe_with_signature_alone(self):
        one = evaluate()
        two = evaluate()
        self.assertEqual(one["reservation_key"], two["reservation_key"])
        self.assertIs(one["authorized"], False)
        self.assertTrue(one["requires_atomic_shared_claim"])

if __name__ == "__main__":
    unittest.main()
