# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Tests of the POL-on payload building (Frappe data, fake POL-on client)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from education.integrations.polon import service

TEST_PESEL = "44051401458"
FOS_CODE = "1000"


class FakePolonClient:
	"""In-memory stand-in for :class:`~.polon_api.PolonAPI`."""

	institution = "test-institution"

	def __init__(self):
		self.sent = []
		self.students = []

	def authenticate(self, force: bool = False) -> str:
		return "token"

	def find_all_fos(self):
		return [{"name": "Informatyka", "level": "LEVEL_I", "fosCode": FOS_CODE}]

	def add_student(self, payload):
		self.sent.append(payload)
		return {"studentInUniversityId": "polon-1"}

	def find_students(self, search_filter, pageable=None):
		return self.students


class TestPolonPayload(FrappeTestCase):
	def setUp(self):
		self.client = FakePolonClient()
		self.academic_year = self._make_academic_year()
		self.academic_term = self._make_academic_term()
		self.program = self._make_program()
		self.student = self._make_student()
		self.enrollment = self._make_enrollment()

	def _make_academic_year(self) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Academic Year",
				"academic_year_name": "2025-2026",
				"year_start_date": "2025-10-01",
				"year_end_date": "2026-09-30",
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_academic_term(self) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Academic Term",
				"academic_year": self.academic_year,
				"term_name": "Summer 2026",
				"term_start_date": "2026-02-15",
				"term_end_date": "2026-06-30",
				"is_summer_term": 1,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_program(self) -> str:
		course = frappe.get_doc(
			{"doctype": "Course", "course_name": "Programowanie", "ects_credits": 6}
		).insert(ignore_permissions=True)
		doc = frappe.get_doc(
			{
				"doctype": "Program",
				"program_name": "Informatyka",
				"degree_type": "Bachelor",
				"study_mode": "Full-Time",
				"courses": [{"course": course.name, "semester_number": 1}],
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_student(self) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Student",
				"first_name": "Anna",
				"middle_name": "Maria",
				"last_name": "Nowak",
				"date_of_birth": "2004-05-14",
				"pesel": TEST_PESEL,
				"album_number": "123456",
				"citizenship": "PL",
				"identity_card_number": "ABC123456",
				"polon_locality_type": "CITY",
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_enrollment(self) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "Program Enrollment",
				"student": self.student,
				"program": self.program,
				"academic_year": self.academic_year,
				"academic_term": self.academic_term,
				"enrollment_date": "2025-10-01",
			}
		).insert(ignore_permissions=True)
		return doc.name

	def test_payload_is_built_from_student_and_enrollment(self):
		report = service.prepare_student_report(
			self.student, self.enrollment, client=self.client, fos_code=FOS_CODE
		)

		payload = report["payload"]
		personal = payload["studentPersonalData"]
		semester = payload["studentCourseData"]["courseAssignedToFieldOfStudy"]["semesters"][0]

		self.assertEqual(personal["name"], "Anna")
		self.assertEqual(personal["otherNames"], "Maria")
		self.assertEqual(personal["surname"], "Nowak")
		self.assertEqual(personal["birthYear"], 2004)
		self.assertEqual(personal["gender"], "MALE")  # tenth PESEL digit is odd
		self.assertEqual(personal["citizenships"], ["PL"])
		self.assertEqual(personal["identificationData"]["pesel"], TEST_PESEL)
		self.assertEqual(personal["identificationData"]["document"]["documentNumber"], "ABC123456")

		self.assertEqual(semester["academicYear"], "2025/2026")
		self.assertEqual(semester["academicSemester"], "SUMMER")
		self.assertEqual(semester["studySemester"], 1)
		self.assertEqual(semester["fieldOfStudyInstanceCode"], int(FOS_CODE))
		self.assertGreater(semester["accumulatedEcts"], 0)

	def test_level_is_mapped_from_degree_type(self):
		self.assertEqual(service.level_code("Bachelor"), "LEVEL_I")
		self.assertEqual(service.level_code("Master"), "LEVEL_II")
		with self.assertRaises(frappe.ValidationError):
			service.level_code("Unknown")

	def test_academic_year_falls_back_to_a_calendar_year(self):
		self.assertEqual(service.academic_year_label(None, "2026-03-01"), "2025/2026")
		self.assertEqual(service.academic_year_label(None, "2026-11-01"), "2026/2027")
		self.assertEqual(service.academic_year_label("2024-2025"), "2024/2025")

	def test_fos_is_resolved_when_not_given(self):
		with patch.object(service, "resolve_fos_code", return_value=FOS_CODE) as resolver:
			service.prepare_student_report(self.student, self.enrollment, client=self.client)
		resolver.assert_called_once()

	def test_unknown_program_is_reported(self):
		with patch.object(service, "resolve_fos_code", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				service.prepare_student_report(self.student, self.enrollment, client=self.client)

	def test_send_student_report_stores_payload(self):
		result = service.send_student_report(
			self.student, self.enrollment, client=self.client, fos_code=FOS_CODE
		)

		self.assertTrue(result["ok"])
		self.assertEqual(len(self.client.sent), 1)
		self.assertEqual(result["response"]["studentInUniversityId"], "polon-1")

	def test_verification_matches_the_album_number(self):
		self.client.students = [
			{
				"studentInUniversityId": "polon-1",
				"externalId": "123456",
				"name": "Anna",
				"surname": "Nowak",
				"isCorrect": True,
				"courses": [
					{
						"fieldOfStudyInstanceName": "Informatyka",
						"fieldOfStudyInstanceCode": {"code": FOS_CODE},
						"studentStudyId": "study-1",
					}
				],
			}
		]

		result = service.verify_student_report(self.student, client=self.client)

		self.assertTrue(result["found"])
		self.assertTrue(result["external_id_matches"])
		self.assertEqual(result["matches"][0]["courses"][0]["studyId"], "study-1")


if __name__ == "__main__":
	unittest.main()
