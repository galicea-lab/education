# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Tests of the portal API (Frappe site + fakes, no network).

Run with ``bench --site <site> run-tests --module education.portal.test_portal``.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from education.portal import api, auth, fees, grades

TEST_PESEL = "44051401458"
ACADEMIC_YEAR = "2099-2100"


class TestPortalStudent(FrappeTestCase):
	"""Student-facing endpoints, called with the student's own session."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._make_fixtures()

	@classmethod
	def _make_fixtures(cls):
		if not frappe.db.exists("Academic Year", ACADEMIC_YEAR):
			frappe.get_doc(
				{
					"doctype": "Academic Year",
					"academic_year_name": ACADEMIC_YEAR,
					"year_start_date": "2099-10-01",
					"year_end_date": "2100-09-30",
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Program", "Test Portal Programme"):
			frappe.get_doc(
				{
					"doctype": "Program",
					"program_name": "Test Portal Programme",
					"degree_type": "Bachelor",
					"study_mode": "Full-Time",
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("Course", "TP-101"):
			frappe.get_doc(
				{
					"doctype": "Course",
					"course_name": "Test Portal Course",
					"ects_credits": 5,
					"class_type": "Lecture",
				}
			).insert(ignore_permissions=True)

	def _user(self, email: str, roles=("Student",)) -> str:
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": email.split("@")[0]}
			)
			user.insert(ignore_permissions=True)
		user = frappe.get_doc("User", email)
		user.add_roles(*roles)
		return email

	def _student(self, email: str) -> str:
		self._user(email)
		name = frappe.db.get_value("Student", {"user": email}, "name")
		if name:
			return name
		student = frappe.get_doc(
			{
				"doctype": "Student",
				"first_name": "Portal",
				"last_name": "Student",
				"date_of_birth": "2003-05-14",
				"pesel": TEST_PESEL,
				"student_email_id": email,
				"user": email,
			}
		)
		student.insert(ignore_permissions=True)
		return student.name

	def _group(self, name: str, student: str) -> str:
		if not frappe.db.exists("Student Group", name):
			group = frappe.get_doc(
				{
					"doctype": "Student Group",
					"student_group_name": name,
					"group_based_on": "Course",
					"course": "TP-101",
					"program": "Test Portal Programme",
					"academic_year": ACADEMIC_YEAR,
					"students": [{"student": student, "active": 1}],
				}
			)
			group.insert(ignore_permissions=True)
		else:
			group = frappe.get_doc("Student Group", name)
			if not any(row.student == student for row in group.students):
				group.append("students", {"student": student, "active": 1})
				group.save(ignore_permissions=True)
		return group.name

	def setUp(self):
		self.email = "portal.student@example.com"
		self.student = self._student(self.email)
		self.other_email = "portal.other@example.com"
		self.other_student = self._student(self.other_email)
		self.group = self._group("TP-GROUP-A", self.student)
		self.other_group = self._group("TP-GROUP-B", self.other_student)

		if not frappe.db.exists("Course Schedule", {"student_group": self.group}):
			frappe.get_doc(
				{
					"doctype": "Course Schedule",
					"student_group": self.group,
					"course": "TP-101",
					"program": "Test Portal Programme",
					"schedule_date": today(),
					"from_time": "08:00:00",
					"to_time": "09:30:00",
					"room": None,
				}
			).insert(ignore_permissions=True)

		frappe.set_user(self.email)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_capabilities_identify_the_student(self):
		capabilities = api.get_capabilities()
		self.assertEqual(capabilities["kind"], "student")
		self.assertEqual(capabilities["student"], self.student)
		self.assertTrue(capabilities["features"]["schedule"])
		self.assertTrue(capabilities["features"]["card"])

	def test_schedule_contains_only_own_groups(self):
		result = api.get_schedule(from_date=today(), to_date=add_days(today(), 7))
		self.assertEqual(result["student"], self.student)
		groups = {entry["group"] for entry in result["entries"]}
		self.assertEqual(groups, {self.group})

	def test_cannot_ask_for_another_student(self):
		with self.assertRaises(frappe.PermissionError):
			api.get_grades(student=self.other_student)

	def test_foreign_results_are_not_listed(self):
		rows = frappe.get_list(
			"Assessment Result", filters={"student": self.other_student}, fields=["name"]
		)
		self.assertEqual(rows, [])

	def test_legacy_schedule_endpoint_ignores_client_groups(self):
		result = frappe.call(
			"education.education.api.get_course_schedule_for_student",
			program_name="Test Portal Programme",
			student_groups=[{"label": self.other_group}],
		)
		self.assertTrue(all(row.get("student_group") == self.group for row in result))


class TestPortalService(FrappeTestCase):
	"""Lecturer and card endpoints, called by the trusted service account."""

	def setUp(self):
		self.instructor = self._instructor()
		self.student = self._student()
		self.group = self._group()

	def _instructor(self) -> str:
		name = frappe.db.get_value("Instructor", {"instructor_name": "Portal Lecturer"}, "name")
		if name:
			return name
		return (
			frappe.get_doc(
				{"doctype": "Instructor", "instructor_name": "Portal Lecturer"}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _student(self) -> str:
		email = "portal.service.student@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": "Service"}).insert(
				ignore_permissions=True
			)
		name = frappe.db.get_value("Student", {"user": email}, "name")
		if name:
			return name
		return (
			frappe.get_doc(
				{
					"doctype": "Student",
					"first_name": "Service",
					"last_name": "Student",
					"date_of_birth": "2003-05-14",
					"user": email,
					"student_email_id": email,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _group(self) -> str:
		name = "TP-SERVICE-GROUP"
		if frappe.db.exists("Student Group", name):
			group = frappe.get_doc("Student Group", name)
		else:
			group = frappe.get_doc(
				{
					"doctype": "Student Group",
					"student_group_name": name,
					"group_based_on": "Course",
					"course": "TP-101",
					"program": "Test Portal Programme",
					"academic_year": ACADEMIC_YEAR,
				}
			)
			group.insert(ignore_permissions=True)

		changed = False
		if not any(row.student == self.student for row in group.students):
			group.append("students", {"student": self.student, "active": 1})
			changed = True
		if not any(row.instructor == self.instructor for row in group.instructors):
			group.append("instructors", {"instructor": self.instructor})
			changed = True
		if changed:
			group.save(ignore_permissions=True)
		return group.name

	def test_lecturer_context_lists_own_groups(self):
		result = api.get_lecturer_context(instructor=self.instructor)
		self.assertEqual(result["instructor"]["name"], self.instructor)
		self.assertIn(self.group, {row["name"] for row in result["groups"]})

	def test_foreign_group_is_rejected(self):
		with self.assertRaises(frappe.PermissionError):
			api.get_group_roster(group="TP-GROUP-NOT-MINE", instructor=self.instructor)

	def test_attendance_is_saved_once_and_updated(self):
		date = today()
		first = api.save_group_attendance(
			group=self.group,
			date=date,
			entries=[{"student": self.student, "status": "Present"}],
			instructor=self.instructor,
		)
		self.assertEqual(first["count"], 1)

		second = api.save_group_attendance(
			group=self.group,
			date=date,
			entries=[{"student": self.student, "status": "Absent"}],
			instructor=self.instructor,
		)
		self.assertEqual(second["count"], 1)

		rows = frappe.get_all(
			"Student Attendance",
			filters={
				"student": self.student,
				"student_group": self.group,
				"date": date,
				"docstatus": 1,
			},
			fields=["name", "status"],
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].status, "Absent")

	def test_case_creation_notifies_the_hub(self):
		frappe.get_doc(
			{"doctype": "Correspondence Type", "type_name": "Portal Test Type", "is_active": 1}
		).insert(ignore_permissions=True)

		with patch("education.portal.cases.hub.notify", return_value=True) as notify:
			result = api.create_case(
				correspondence_type="Portal Test Type",
				subject="Test subject",
				description="Test description",
				student=self.student,
			)

		self.assertEqual(result["student"], self.student)
		notify.assert_called_once()
		self.assertEqual(notify.call_args.args[0], "student_correspondence.created")

	def test_revealing_codes_without_ready_card_fails(self):
		with self.assertRaises(Exception):
			api.reveal_card_codes(student=self.student)


class TestPortalConfig(unittest.TestCase):
	"""Configuration helpers of the portal (no site needed)."""

	def test_passing_percentage_default(self):
		with patch.dict(frappe.conf, {}, clear=True):
			self.assertEqual(auth.passing_percentage(), 50.0)
		with patch.dict(frappe.conf, {"portal": {"passing_percentage": 60}}, clear=True):
			self.assertEqual(auth.passing_percentage(), 60.0)
		with patch.dict(frappe.conf, {"portal": {"passing_percentage": "abc"}}, clear=True):
			self.assertEqual(auth.passing_percentage(), 50.0)

	def test_service_users_parsing(self):
		with patch.dict(frappe.conf, {}, clear=True):
			self.assertIn("administrator", auth.service_users())
		with patch.dict(frappe.conf, {"portal": {"service_users": "a@x.pl, b@x.pl"}}, clear=True):
			self.assertEqual(auth.service_users(), {"a@x.pl", "b@x.pl"})

	def test_grade_threshold_math(self):
		self.assertTrue(grades._percentage(80, 100) == 80.0)
		self.assertIsNone(grades._percentage(10, 0))
		self.assertEqual(fees._state(0, today()), "paid")
		self.assertEqual(fees._state(10, add_days(today(), -1)), "overdue")
		self.assertEqual(fees._state(10, add_days(today(), 3)), "due")


if __name__ == "__main__":
	unittest.main()
