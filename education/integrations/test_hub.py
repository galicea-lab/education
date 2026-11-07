# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Tests of the hub webhook contract (signing part, no network)."""

from __future__ import annotations

import json
import unittest

from education.integrations import hub

SECRET = "test-secret"
SENT_AT = "2026-11-07T06:30:00"


class TestHubSignature(unittest.TestCase):
	def test_body_is_canonical_json(self):
		body = hub.build_body("student_card_order.created", {"b": 1, "a": 2}, sent_at=SENT_AT)
		payload = json.loads(body)

		self.assertEqual(payload["event"], "student_card_order.created")
		self.assertEqual(payload["sent_at"], SENT_AT)
		self.assertEqual(payload["data"], {"a": 2, "b": 1})
		# Keys are sorted and there is no whitespace, so the hub can verify the
		# signature over the raw bytes it receives.
		self.assertIn('{"data":{"a":2,"b":1}', body)
		self.assertNotIn(", ", body)

	def test_signature_is_stable_and_verifiable(self):
		body = hub.build_body("student_card_order.status_changed", {"name": "EDU-SCO-1"}, sent_at=SENT_AT)

		signature = hub.sign(body, SECRET)

		self.assertTrue(signature.startswith("sha256="))
		self.assertEqual(signature, hub.sign(body, SECRET))
		self.assertTrue(hub.verify_signature(body, signature, SECRET))

	def test_signature_rejects_tampering(self):
		body = hub.build_body("student_card_order.status_changed", {"status": "Ready"}, sent_at=SENT_AT)
		signature = hub.sign(body, SECRET)

		self.assertFalse(hub.verify_signature(body.replace("Ready", "Error"), signature, SECRET))
		self.assertFalse(hub.verify_signature(body, signature, "another-secret"))
		self.assertFalse(hub.verify_signature(body, "", SECRET))
		self.assertFalse(hub.verify_signature(body, signature, ""))

	def test_bytes_and_str_bodies_sign_alike(self):
		body = hub.build_body("event", {"x": 1}, sent_at=SENT_AT)
		self.assertEqual(hub.sign(body, SECRET), hub.sign(body.encode("utf-8"), SECRET))


if __name__ == "__main__":
	unittest.main()
