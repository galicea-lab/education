# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Whitelisted portal endpoints (``education.portal.api.*``).

Every method can be called by

* an end user signed in to Frappe - the identity is taken from the session and
  the ``student``/``instructor`` arguments are optional and must match it,
* a trusted service account (``portal.service_users``) - the arguments are
  required, because that account authenticated the end user itself.

Call with ``POST /api/method/education.portal.api.<name>``.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, today

from education.portal import attendance as attendance_portal
from education.portal import (
	auth,
	card,
	cases,
	fees,
	grades,
	lecturer,
	schedule,
	semester_card,
	studies,
)


def _range(from_date: str | None, to_date: str | None) -> tuple:
	default_from = today()
	return from_date or default_from, to_date or add_days(default_from, 6)


# ----------------------------------------------------------------------
# capabilities
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_capabilities() -> dict:
	"""Who the caller is and what the portal should show (no exception)."""

	user = frappe.session.user
	student = auth.student_for_user(user)
	instructor = auth.instructor_for_user(user)

	kind = "guest"
	if auth.is_service_user(user):
		kind = "service"
	elif student:
		kind = "student"
	elif instructor:
		kind = "instructor"
	elif auth.is_staff(user):
		kind = "staff"

	return {
		"user": user,
		"kind": kind,
		"student": student,
		"instructor": instructor,
		"staff": auth.is_staff(user),
		"service": auth.is_service_user(user),
		"passing_percentage": auth.passing_percentage(),
		"reveal_codes_requires_password": auth.reveal_codes_requires_password(),
		"features": {
			"schedule": kind in ("student", "instructor", "service", "staff"),
			"grades": kind in ("student", "service", "staff"),
			"semester_card": kind in ("student", "service", "staff"),
			"attendance": kind in ("student", "service", "staff"),
			"fees": kind in ("student", "service", "staff"),
			"cases": kind in ("student", "service", "staff"),
			"card": kind in ("student", "service", "staff"),
			"lecturer": kind in ("instructor", "service", "staff"),
		},
	}


# ----------------------------------------------------------------------
# student data
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_studies(student: str | None = None) -> dict:
	"""Profile, programme enrollments and semester settlements ("Moje studia")."""

	who = auth.require_student(student)
	return studies.for_student(who)


@frappe.whitelist()
def get_schedule(
	from_date: str | None = None,
	to_date: str | None = None,
	student: str | None = None,
	instructor: str | None = None,
	program: str | None = None,
) -> dict:
	"""Timetable of the student (or of the lecturer when ``instructor`` is given)."""

	from_date, to_date = _range(from_date, to_date)

	if instructor:
		who = auth.require_instructor(instructor)
		entries = schedule.for_instructor(who, from_date, to_date)
		return {"kind": "instructor", "instructor": who, "from": from_date, "to": to_date, "entries": entries}

	who = auth.require_student(student)
	entries = schedule.for_student(who, from_date, to_date, program)
	return {
		"kind": "student",
		"student": who,
		"from": from_date,
		"to": to_date,
		"program": program,
		"entries": entries,
	}


@frappe.whitelist()
def get_grades(
	student: str | None = None,
	academic_year: str | None = None,
	academic_term: str | None = None,
) -> dict:
	"""Grades, attempts, semester settlement and the average of a student."""

	who = auth.require_student(student)
	return grades.for_student(who, academic_year, academic_term)


@frappe.whitelist()
def get_semester_card(
	student: str | None = None,
	academic_term: str | None = None,
	program: str | None = None,
) -> dict:
	"""Semester achievement card (KOOS): courses of the semester, form of classes,
	passing type, attempts and grades, lecturers, ECTS and the semester settlement.

	Without ``academic_term`` the latest settled semester is used, without
	``program`` the latest submitted programme enrollment."""

	who = auth.require_student(student)
	return semester_card.for_student(who, academic_term, program)


@frappe.whitelist()
def get_attendance(
	student: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
) -> dict:
	"""Attendance summary and absences of a student."""

	who = auth.require_student(student)
	return attendance_portal.for_student(who, from_date, to_date)


@frappe.whitelist()
def get_fees(student: str | None = None) -> dict:
	"""Fees of a student with payment state and amounts."""

	who = auth.require_student(student)
	return fees.for_student(who)


# ----------------------------------------------------------------------
# dean's office correspondence
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_case_types() -> list[dict]:
	"""Case types available to students."""

	return cases.list_types()


@frappe.whitelist()
def get_cases(student: str | None = None) -> dict:
	"""Cases of a student, newest first."""

	who = auth.require_student(student)
	return {"student": who, "cases": cases.list_for_student(who)}


@frappe.whitelist()
def get_case(name: str, student: str | None = None) -> dict:
	"""Single case with the answer, timeline and the signed document."""

	who = auth.require_student(student)
	return cases.get_case(name, who)


@frappe.whitelist()
def create_case(
	correspondence_type: str,
	subject: str,
	description: str,
	program: str | None = None,
	academic_year: str | None = None,
	form_data: str | None = None,
	student: str | None = None,
) -> dict:
	"""Submit a new case and hand it over to the hub."""

	who = auth.require_student(student)
	return cases.create_case(
		who,
		correspondence_type,
		subject,
		description,
		program=program,
		academic_year=academic_year,
		form_data=form_data,
	)


# ----------------------------------------------------------------------
# mLegitymacja
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_card(student: str | None = None) -> dict:
	"""Card state of the student (without the secret codes)."""

	who = auth.require_student(student)
	state = card.state_for_student(who)
	state["student"] = who
	return state


@frappe.whitelist()
def order_card(
	kind: str = "Issue",
	release_date: str | None = None,
	expire_date: str | None = None,
	student: str | None = None,
) -> dict:
	"""Order a student card (Issue or Renewal)."""

	who = auth.require_student(student)
	return card.order_card(who, kind, release_date, expire_date)


@frappe.whitelist()
def refresh_card(student: str | None = None) -> dict:
	"""Re-poll MLS for the student's newest order and return the fresh state."""

	who = auth.require_student(student)
	state = card.refresh_state(who)
	state["student"] = who
	return state


@frappe.whitelist()
def reveal_card_codes(password: str | None = None, student: str | None = None) -> dict:
	"""Return the QR and activation codes of a ready card (audited)."""

	who = auth.require_student(student)
	return card.reveal_codes(who, password)


@frappe.whitelist()
def request_card_revocation(reason: str | None = None, student: str | None = None) -> dict:
	"""Request the revocation of the student's card."""

	who = auth.require_student(student)
	return card.request_revocation(who, reason)


# ----------------------------------------------------------------------
# lecturer
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_lecturer_context(instructor: str | None = None, academic_term: str | None = None) -> dict:
	"""Groups and courses of the lecturer."""

	who = auth.require_instructor(instructor)
	return lecturer.context(who, academic_term)


@frappe.whitelist()
def get_group_roster(group: str, instructor: str | None = None) -> dict:
	"""Students of a group taught by the lecturer."""

	who = auth.require_instructor(instructor)
	return {"group": group, "instructor": who, "students": lecturer.roster(group, who)}


@frappe.whitelist()
def get_group_attendance(
	group: str, date: str | None = None, instructor: str | None = None
) -> dict:
	"""Attendance sheet of a group for one day."""

	who = auth.require_instructor(instructor)
	return lecturer.group_attendance(group, who, date)


@frappe.whitelist()
def save_group_attendance(
	group: str,
	date: str,
	entries,
	course_schedule: str | None = None,
	instructor: str | None = None,
) -> dict:
	"""Save the attendance sheet (``entries`` = ``[{student, status}]``)."""

	who = auth.require_instructor(instructor)
	entries = frappe.parse_json(entries) if isinstance(entries, str) else entries
	return lecturer.save_attendance(group, who, date, entries, course_schedule)


@frappe.whitelist()
def get_group_assessments(group: str, instructor: str | None = None) -> dict:
	"""Assessment plans of a group with criteria and scales."""

	who = auth.require_instructor(instructor)
	return {"group": group, "plans": lecturer.group_assessments(group, who)}


@frappe.whitelist()
def save_group_results(
	group: str,
	assessment_plan: str,
	rows,
	submit: bool | str | int = False,
	instructor: str | None = None,
) -> dict:
	"""Save assessment results (``rows`` = ``[{student, details, total_score, comment}]``)."""

	who = auth.require_instructor(instructor)
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	submit = frappe.utils.sbool(submit)
	return lecturer.save_results(group, who, assessment_plan, rows, submit)
