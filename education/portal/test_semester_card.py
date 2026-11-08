# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Tests of the semester achievement card (KOOS).

Run with ``bench --site <site> run-tests --module education.portal.test_semester_card``.
"""

from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from education.portal import api, semester_card

ACADEMIC_YEAR = "2099-2100"
TERM = "2099-2100 (KOOS zimowy)"
PROGRAM = "Test KOOS Programme"


def _result(name, course, score, grade, plan, group="G-1", modified="2100-01-20 10:00:00"):
	return {
		"name": name,
		"course": course,
		"academic_year": ACADEMIC_YEAR,
		"academic_term": TERM,
		"student_group": group,
		"assessment_plan": plan,
		"assessment_group": None,
		"grading_scale": None,
		"total_score": score,
		"maximum_score": 100,
		"grade": grade,
		"modified": modified,
	}


class TestBuildCard(FrappeTestCase):
	"""Assembly of the card from loaded rows (no records are created)."""

	def card(self, **overrides):
		data = dict(
			student={"name": "STU-1", "album_number": "12345", "first_name": "Jan", "last_name": "Kowalski"},
			program={"name": PROGRAM, "degree_type": "Bachelor", "study_mode": "Full-Time"},
			enrollment="PE-1",
			term={"name": TERM, "academic_year": ACADEMIC_YEAR},
			status={"semester_number": 3, "status": "Passed"},
			plan={
				"ALG": {"course": "ALG", "semester_number": 3, "required": 1, "idx": 1},
				"BD": {"course": "BD", "semester_number": 3, "required": 1, "idx": 2},
				"MAT": {"course": "MAT", "semester_number": 1, "required": 1, "idx": 3},
				"SI": {"course": "SI", "semester_number": 6, "required": 0, "idx": 4},
			},
			results=[
				_result("AR-1", "ALG", 30, "2", "AP-1", modified="2100-01-20 10:00:00"),
				_result("AR-2", "ALG", 70, "4,5", "AP-2", modified="2100-02-10 10:00:00"),
				_result("AR-3", "MAT", 60, "3", "AP-3"),
				_result("AR-4", "EXT", 90, "5", "AP-4"),
			],
			courses={
				"ALG": {
					"course_name": "Algorytmy",
					"ects_credits": 6,
					"passing_type": "Graded",
					"class_type": "Lecture",
				},
				"BD": {
					"course_name": "Bazy danych",
					"ects_credits": 4,
					"passing_type": "Graded",
					"class_type": "Laboratory",
				},
				"MAT": {
					"course_name": "Matematyka",
					"ects_credits": 5,
					"passing_type": "Graded",
					"class_type": "Lecture",
				},
				"EXT": {"course_name": "Różnica programowa", "ects_credits": 2, "passing_type": "Pass-Fail"},
			},
			plans={
				"AP-1": {"examiner": "INS-1", "schedule_date": "2100-01-20", "assessment_group": "Termin I"},
				"AP-2": {"examiner": "INS-2", "schedule_date": "2100-02-10", "assessment_group": "Poprawka"},
				"AP-3": {"examiner": None, "schedule_date": "2100-01-25", "assessment_group": "Termin I"},
				"AP-4": {"examiner": None, "schedule_date": "2100-01-26", "assessment_group": "Termin I"},
			},
			group_courses={"BD": ["G-BD"]},
			group_instructors={"G-BD": [("INS-3", "Ewa Nowak")], "G-1": [("INS-1", "Adam Lis")]},
			instructors={
				"INS-1": {"instructor_name": "Adam Lis", "title": "dr"},
				"INS-2": {"instructor_name": "Piotr Wróbel", "title": "prof. dr hab."},
				"INS-3": {"instructor_name": "Ewa Nowak", "title": None},
			},
			threshold=50,
			labels={"Termin I": "I", "Poprawka": "II"},
		)
		data.update(overrides)
		return semester_card.build_card(**data)

	def course(self, card, code):
		return next(row for row in card["courses"] if row["code"] == code)

	def test_header(self):
		card = self.card()
		self.assertEqual(card["semester_number"], 3)
		self.assertEqual(card["year_of_study"], 2)
		self.assertEqual(card["year_of_study_roman"], "II")
		self.assertEqual(card["next_semester_number"], 4)
		self.assertEqual(card["academic_year"], ACADEMIC_YEAR)

	def test_course_list_follows_plan_then_additional(self):
		card = self.card()
		self.assertEqual([row["code"] for row in card["courses"]], ["ALG", "BD", "EXT", "MAT"])
		self.assertEqual(self.course(card, "MAT")["category"], "repeat")
		self.assertEqual(self.course(card, "EXT")["category"], "program_difference")
		self.assertFalse(self.course(card, "ALG")["additional"])
		self.assertNotIn("SI", [row["code"] for row in card["courses"]])

	def test_attempts_labels_and_final_grade(self):
		alg = self.course(self.card(), "ALG")
		self.assertEqual([a["label"] for a in alg["attempts"]], ["I", "II"])
		self.assertEqual([a["passed"] for a in alg["attempts"]], [False, True])
		self.assertEqual(alg["grade"], "4,5")
		self.assertTrue(alg["passed"])
		self.assertEqual(alg["class_type"], "Lecture")
		self.assertEqual(alg["passing_type"], "Graded")

	def test_lecturer_is_examiner_of_final_attempt_then_group(self):
		card = self.card()
		alg = self.course(card, "ALG")
		self.assertEqual(alg["lecturer"]["display"], "prof. dr hab. Piotr Wróbel")
		self.assertEqual([l["instructor"] for l in alg["lecturers"]], ["INS-2", "INS-1"])
		bd = self.course(card, "BD")
		self.assertEqual(bd["lecturer"]["display"], "Ewa Nowak")
		self.assertIsNone(bd["final"])
		self.assertIsNone(bd["passed"])

	def test_totals_split_regular_and_additional_ects(self):
		totals = self.card()["totals"]
		self.assertEqual(totals["ects_planned"], 10)  # ALG + BD
		self.assertEqual(totals["ects_earned"], 6)  # ALG
		self.assertEqual(totals["ects_additional"], 7)  # EXT + MAT
		self.assertEqual(totals["ects_additional_earned"], 7)
		self.assertEqual(totals["not_graded"], 1)
		# (4.5*6 + 5*2 + 3*5) / 13
		self.assertEqual(totals["average_grade"], round((27 + 10 + 15) / 13, 2))

	def test_without_labels_attempts_are_numbered(self):
		alg = self.course(self.card(labels={}), "ALG")
		self.assertEqual([a["label"] for a in alg["attempts"]], ["1", "2"])

	def test_unknown_semester_lists_only_graded_courses(self):
		card = self.card(status=None)
		self.assertEqual([row["code"] for row in card["courses"]], ["ALG", "BD", "EXT", "MAT"])
		self.assertTrue(all(not row["additional"] for row in card["courses"] if row["code"] != "EXT"))
		self.assertIsNone(card["year_of_study"])

	def test_last_semester_has_no_next(self):
		card = self.card(status={"semester_number": 6})
		self.assertIsNone(card["next_semester_number"])


class TestSemesterCardApi(FrappeTestCase):
	"""``get_semester_card`` on a site: access rules and loading from records."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Academic Year", ACADEMIC_YEAR):
			frappe.get_doc(
				{
					"doctype": "Academic Year",
					"academic_year_name": ACADEMIC_YEAR,
					"year_start_date": "2099-10-01",
					"year_end_date": "2100-09-30",
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("Academic Term", TERM):
			frappe.get_doc(
				{
					"doctype": "Academic Term",
					"academic_year": ACADEMIC_YEAR,
					"term_name": "KOOS zimowy",
					"term_start_date": "2099-10-01",
					"term_end_date": "2100-02-28",
				}
			).insert(ignore_permissions=True)
		for code, name, ects in (("KOOS-1", "KOOS Algorytmy", 6), ("KOOS-2", "KOOS Bazy danych", 4)):
			if not frappe.db.exists("Course", code):
				frappe.get_doc(
					{
						"doctype": "Course",
						"name": code,
						"course_name": name,
						"ects_credits": ects,
						"class_type": "Lecture",
						"passing_type": "Graded",
					}
				).insert(ignore_permissions=True, set_name=code)
		if not frappe.db.exists("Program", PROGRAM):
			frappe.get_doc(
				{
					"doctype": "Program",
					"program_name": PROGRAM,
					"degree_type": "Bachelor",
					"study_mode": "Full-Time",
					"courses": [
						{"course": "KOOS-1", "course_name": "KOOS Algorytmy", "semester_number": 1},
						{"course": "KOOS-2", "course_name": "KOOS Bazy danych", "semester_number": 2},
					],
				}
			).insert(ignore_permissions=True)

	def _student(self, email: str) -> str:
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0]}).insert(
				ignore_permissions=True
			)
		frappe.get_doc("User", email).add_roles("Student")
		name = frappe.db.get_value("Student", {"user": email}, "name")
		if name:
			return name
		return (
			frappe.get_doc(
				{
					"doctype": "Student",
					"first_name": "Koos",
					"last_name": email.split("@")[0],
					"date_of_birth": "2003-05-14",
					"student_email_id": email,
					"user": email,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _enrollment(self, student: str) -> str:
		name = frappe.db.get_value(
			"Program Enrollment", {"student": student, "program": PROGRAM, "docstatus": 1}, "name"
		)
		if name:
			return name
		enrollment = frappe.get_doc(
			{
				"doctype": "Program Enrollment",
				"student": student,
				"program": PROGRAM,
				"academic_year": ACADEMIC_YEAR,
				"academic_term": TERM,
				"enrollment_date": "2099-10-01",
			}
		)
		enrollment.insert(ignore_permissions=True)
		enrollment.submit()
		return enrollment.name

	def _status(self, student: str, enrollment: str) -> None:
		if frappe.db.exists(
			"Student Academic Status",
			{"program_enrollment": enrollment, "academic_term": TERM, "docstatus": 1},
		):
			return
		status = frappe.get_doc(
			{
				"doctype": "Student Academic Status",
				"student": student,
				"program_enrollment": enrollment,
				"academic_year": ACADEMIC_YEAR,
				"academic_term": TERM,
				"semester_number": 1,
				"status": "Active",
			}
		)
		status.insert(ignore_permissions=True)
		status.submit()

	def setUp(self):
		self.email = "koos.student@example.com"
		self.student = self._student(self.email)
		self.other = self._student("koos.other@example.com")
		enrollment = self._enrollment(self.student)
		self._status(self.student, enrollment)
		frappe.set_user(self.email)
		self.addCleanup(frappe.set_user, "Administrator")

	def test_card_of_own_semester(self):
		card = api.get_semester_card()
		self.assertEqual(card["student"]["name"], self.student)
		self.assertEqual(card["academic_term"]["name"], TERM)
		self.assertEqual(card["semester_number"], 1)
		self.assertEqual(card["next_semester_number"], 2)
		self.assertEqual([row["code"] for row in card["courses"]], ["KOOS-1"])
		self.assertEqual(card["courses"][0]["ects"], 6)
		self.assertIsNone(card["courses"][0]["passed"])
		self.assertEqual(card["totals"]["ects_planned"], 6)

	def test_cannot_ask_for_another_student(self):
		with self.assertRaises(frappe.PermissionError):
			api.get_semester_card(student=self.other)

	def test_student_without_enrollment(self):
		frappe.set_user("koos.other@example.com")
		with self.assertRaises(frappe.DoesNotExistError):
			api.get_semester_card()
