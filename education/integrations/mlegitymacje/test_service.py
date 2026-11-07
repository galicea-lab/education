# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Flow tests of the student card order (Frappe + fake MLS client, no network)."""

from __future__ import annotations

import base64
import unittest
from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from education.integrations.mlegitymacje import service
from education.integrations.mlegitymacje.mls_model import (
	CreationStatus,
	Status,
	Status1,
	StudentAddToOrderStatusDto,
	StudentCardCreateOrderResponseDto,
	StudentCardOrderQrCodesResponseDto,
	StudentCardOrderStatusResponseDto,
	StudentCardOrderStudentStatusResponseDto,
	StudentCardQrCodeDto,
)

TEST_PESEL = "44051401458"


class FakeMlsClient:
	"""In-memory stand-in for :class:`~.client.MlsAPI`."""

	def __init__(self):
		self.orders: dict[str, str] = {}
		self.student_cards: dict[str, str] = {}
		self.revoked: list[str] = []

	def authenticate(self, force: bool = False) -> str:
		return "token"

	def create_order(self, students) -> StudentCardCreateOrderResponseDto:
		order_id = 1000 + len(self.orders)
		self.orders[str(order_id)] = "NEW"
		for student in students:
			self.student_cards[student.studentCardId] = "ORDERED"
		return StudentCardCreateOrderResponseDto(
			orderId=order_id,
			students=[
				StudentAddToOrderStatusDto(
					studentCardId=student.studentCardId, creationStatus=CreationStatus.CREATED
				)
				for student in students
			],
		)

	def get_order_status(self, order_id: str) -> StudentCardOrderStatusResponseDto:
		return StudentCardOrderStatusResponseDto(status=Status1(self.orders[order_id]))

	def get_qr_codes(self, order_id: str) -> StudentCardOrderQrCodesResponseDto:
		return StudentCardOrderQrCodesResponseDto(
			qrCodes=[
				StudentCardQrCodeDto(qrCode="qr-content", studentCardId=card_id, password="act-123")
				for card_id in self.student_cards
			],
			validTo=date(2030, 1, 31),
		)

	def get_student_status(self, order_id: str, student_card_id: str):
		return StudentCardOrderStudentStatusResponseDto(
			status=Status.STUDENT_CARD_NOT_DOWNLOADED_YET
		)

	def revoke_student_card(self, student_card_id: str) -> str:
		self.revoked.append(student_card_id)
		return "{}"


class TestStudentCardOrder(FrappeTestCase):
	def setUp(self):
		self.client = FakeMlsClient()
		self.student = self._make_student()

		patch_student_picture = patch.object(
			service,
			"photo_from_student",
			return_value=base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii"),
		)
		patch_notify = patch.object(service.hub, "notify", return_value=True)
		self.addCleanup(patch_student_picture.stop)
		self.addCleanup(patch_notify.stop)
		patch_student_picture.start()
		self.notify = patch_notify.start()

	def _make_student(self) -> str:
		student = frappe.get_doc(
			{
				"doctype": "Student",
				"first_name": "Jan",
				"last_name": "Kowalski",
				"date_of_birth": "1994-05-14",
				"pesel": TEST_PESEL,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.rollback)
		return student.name

	def test_issue_order_is_sent_to_mls(self):
		order = service.create_card_order(self.student, "Issue", mls_client=self.client)

		self.assertEqual(order["status"], service.STATUS_QUEUED)
		self.assertEqual(order["kind"], service.KIND_ISSUE)
		self.assertTrue(order["mls_order_id"])
		self.assertTrue(order["student_card_id"])
		self.assertEqual(self.notify.call_args.args[0], service.EVENT_CREATED)

	def test_order_becomes_ready_with_qr_code(self):
		order = service.create_card_order(self.student, "Issue", mls_client=self.client)
		self.client.orders[order["mls_order_id"]] = "READY"

		refreshed = service.refresh_card_order(order["name"], mls_client=self.client)

		self.assertEqual(refreshed["status"], service.STATUS_READY)
		self.assertEqual(refreshed["mls_status"], "READY")
		self.assertEqual(refreshed["qr_code"], "qr-content")
		self.assertEqual(refreshed["activation_code"], "act-123")
		self.assertEqual(refreshed["valid_to"], "2030-01-31")
		self.assertEqual(self.notify.call_args.args[0], service.EVENT_STATUS_CHANGED)

	def test_error_status_is_stored(self):
		order = service.create_card_order(self.student, "Issue", mls_client=self.client)
		self.client.orders[order["mls_order_id"]] = "ERROR"

		refreshed = service.refresh_card_order(order["name"], mls_client=self.client)

		self.assertEqual(refreshed["status"], service.STATUS_ERROR)
		self.assertTrue(refreshed["error"])

	def test_revocation_uses_existing_card(self):
		issued = service.create_card_order(self.student, "Issue", mls_client=self.client)
		self.client.orders[issued["mls_order_id"]] = "READY"
		ready = service.refresh_card_order(issued["name"], mls_client=self.client)

		revocation = service.create_card_order(self.student, "Revocation", mls_client=self.client)

		self.assertEqual(revocation["status"], service.STATUS_REVOKED)
		self.assertEqual(revocation["student_card_id"], ready["student_card_id"])
		self.assertIn(ready["student_card_id"], self.client.revoked)

	def test_unknown_order_type_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			service.create_card_order(self.student, "Whatever", mls_client=self.client)

	def test_invalid_pesel_is_rejected(self):
		frappe.db.set_value("Student", self.student, "pesel", "12345678901")
		with self.assertRaises(ValueError):
			service.create_card_order(self.student, "Issue", mls_client=self.client)


if __name__ == "__main__":
	unittest.main()
