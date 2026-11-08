# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Student profile and study history for the portal.

One call for the "Moje studia" screen: personal data (identity document numbers
masked), the programme enrollments and the semester settlements. Read with
:func:`frappe.get_list`, so the row-level conditions of
:mod:`education.portal.permissions` apply.
"""

from __future__ import annotations

import frappe

STUDENT_FIELDS = [
	"name",
	"student_name",
	"first_name",
	"middle_name",
	"last_name",
	"album_number",
	"student_email_id",
	"private_email",
	"student_mobile_number",
	"date_of_birth",
	"gender",
	"citizenship",
	"nationality",
	"country",
	"city",
	"address_line_1",
	"address_line_2",
	"identity_card_number",
	"passport_number",
	"image",
	"joining_date",
]

ENROLLMENT_FIELDS = [
	"name",
	"program",
	"academic_year",
	"academic_term",
	"enrollment_date",
	"student_batch_name",
	"student_category",
	"is_tuition_based",
	"docstatus",
]

STATUS_FIELDS = [
	"name",
	"academic_year",
	"academic_term",
	"semester_number",
	"status",
	"is_summer_semester",
	"counts_towards_average",
	"is_paid",
	"decision_date",
	"registration_date",
]

PROGRAM_FIELDS = ["name", "program_name", "program_abbreviation", "degree_type", "study_mode"]


def _mask(value: str | None) -> str | None:
	"""Keep the last four characters of an identity document number."""

	if not value:
		return value
	cleaned = str(value).strip()
	if len(cleaned) <= 4:
		return "*" * len(cleaned)
	return "*" * (len(cleaned) - 4) + cleaned[-4:]


def _programs(names: list[str]) -> dict:
	if not names:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Program", filters={"name": ["in", names]}, fields=PROGRAM_FIELDS
		)
	}


def for_student(student: str) -> dict:
	"""Profile, programme enrollments and semester settlements of a student."""

	rows = frappe.get_list(
		"Student", filters={"name": student}, fields=STUDENT_FIELDS, limit_page_length=1
	)
	if not rows:
		raise frappe.DoesNotExistError(frappe._("Student {0} does not exist").format(student))

	profile = dict(rows[0])
	profile["identity_card_number"] = _mask(profile.get("identity_card_number"))
	profile["passport_number"] = _mask(profile.get("passport_number"))
	profile["address"] = ", ".join(
		part for part in (profile.get("address_line_1"), profile.get("address_line_2")) if part
	) or None

	enrollments = frappe.get_list(
		"Program Enrollment",
		filters={"student": student, "docstatus": 1},
		fields=ENROLLMENT_FIELDS,
		order_by="enrollment_date desc",
		limit_page_length=0,
	)
	programs = _programs(sorted({row.program for row in enrollments if row.get("program")}))

	enrollment_rows = []
	for row in enrollments:
		program = programs.get(row.get("program")) or {}
		enrollment_rows.append(
			{
				"name": row.name,
				"program": row.get("program"),
				"program_name": program.get("program_name"),
				"program_abbreviation": program.get("program_abbreviation"),
				"degree_type": program.get("degree_type"),
				"study_mode": program.get("study_mode"),
				"academic_year": row.get("academic_year"),
				"academic_term": row.get("academic_term"),
				"enrollment_date": str(row.get("enrollment_date") or "") or None,
				"student_batch": row.get("student_batch_name"),
				"student_category": row.get("student_category"),
				"is_tuition_based": bool(row.get("is_tuition_based")),
			}
		)

	semesters = frappe.get_list(
		"Student Academic Status",
		filters={"student": student, "docstatus": 1},
		fields=STATUS_FIELDS,
		order_by="semester_number asc",
		limit_page_length=0,
	)
	for semester in semesters:
		semester["decision_date"] = str(semester.get("decision_date") or "") or None
		semester["registration_date"] = str(semester.get("registration_date") or "") or None

	current_enrollment = enrollment_rows[0] if enrollment_rows else None
	active_semester = None
	for semester in reversed(semesters):
		if (semester.get("status") or "") in ("Active", "Conditional Pass", "Passed"):
			active_semester = semester
			break

	return {
		"student": profile,
		"enrollments": enrollment_rows,
		"semesters": semesters,
		"current_enrollment": current_enrollment,
		"current_semester": active_semester,
		"settings": {
			"current_academic_year": frappe.db.get_single_value(
				"Education Settings", "current_academic_year"
			),
			"current_academic_term": frappe.db.get_single_value(
				"Education Settings", "current_academic_term"
			),
		},
	}
