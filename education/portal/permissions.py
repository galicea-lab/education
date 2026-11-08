# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Row-level conditions for the portal roles.

Without these hooks a portal user (``Student``/``Guardian``/``Instructor``) that
has the ``read`` permission on a doctype can read **every** row through the REST
API: only ``Student`` had a condition before this module existed.

Rules:

* the restriction applies only when the user reaches the doctype through portal
  roles - users with a desk role (``Academics User``, ``Education Manager``,
  ``System Manager``) keep unrestricted access,
* students see their own records, guardians the records of their wards,
* lecturers see the student groups they teach and the records attached to them,
* trusted service accounts (``portal.service_users``) are not restricted -
  they scope every call themselves (see :mod:`education.portal.auth`).

Registered in ``hooks.py`` as ``permission_query_conditions`` and
``has_permission`` for: Assessment Result, Course Schedule, Fees, Program
Enrollment, Student Academic Status, Student Attendance, Student Card Order,
Student Correspondence, Student Group, Student Leave Application.
"""

from __future__ import annotations

import frappe
from frappe.permissions import get_valid_perms

from education.portal.auth import instructor_for_user, is_service_user, student_for_user

PORTAL_ROLES = frozenset({"Student", "Guardian", "Instructor"})

# doctypes where the student's own records are found through the `student` field
STUDENT_FIELD_DOCTYPES = (
	"Assessment Result",
	"Fees",
	"Program Enrollment",
	"Student Academic Status",
	"Student Attendance",
	"Student Card Order",
	"Student Correspondence",
	"Student Leave Application",
)

# doctypes where a lecturer's rows are found through the `student_group` field
GROUP_FIELD_DOCTYPES = ("Assessment Result", "Student Attendance")


def _is_restricted(doctype: str, user: str) -> bool:
	"""True when ``user`` reaches ``doctype`` only through portal roles."""

	if not user or user in ("Administrator", "Guest"):
		return False
	if is_service_user(user):
		return False

	perms = get_valid_perms(doctype, user)
	if not any(perm.read for perm in perms):
		return False
	return not any(perm.read and perm.role not in PORTAL_ROLES for perm in perms)


def _in_quoted(values) -> str:
	"""SQL list literal with escaped values (empty list -> NULL match)."""

	if not values:
		return "NULL"
	return ", ".join(frappe.db.escape(value) for value in values)


def _students_of(user: str) -> list[str]:
	"""Students the user may see: own record plus the wards of a guardian."""

	names = []
	own = student_for_user(user)
	if own:
		names.append(own)

	guardians = frappe.get_all("Guardian", filters={"user": user}, pluck="name")
	email = frappe.db.get_value("User", user, "email")
	if email:
		guardians += frappe.get_all(
			"Guardian",
			filters={"user": ["is", "not set"], "email_address": email},
			pluck="name",
		)
	if guardians:
		names += frappe.get_all(
			"Student Guardian",
			filters={"guardian": ["in", guardians], "parenttype": "Student"},
			pluck="parent",
		)

	return sorted(set(names))


def _groups_of_students(students: list[str]) -> list[str]:
	if not students:
		return []
	return sorted(
		set(
			frappe.get_all(
				"Student Group Student",
				filters={"student": ["in", students], "parenttype": "Student Group"},
				pluck="parent",
			)
		)
	)


def _groups_of_instructor(instructor: str | None) -> list[str]:
	if not instructor:
		return []
	return sorted(
		set(
			frappe.get_all(
				"Student Group Instructor",
				filters={"instructor": instructor, "parenttype": "Student Group"},
				pluck="parent",
			)
		)
	)


def _scope(user: str) -> dict:
	"""Names the user is allowed to see, computed once per query."""

	students = _students_of(user)
	instructor = instructor_for_user(user)
	if instructor:
		groups = sorted(set(_groups_of_instructor(instructor)) | set(_groups_of_students(students)))
	else:
		groups = _groups_of_students(students)

	return {
		"students": students,
		"groups": groups,
		"instructors": [instructor] if instructor else [],
	}


def _clauses_for(doctype: str, scope: dict) -> list[str]:
	clauses = []

	if doctype in STUDENT_FIELD_DOCTYPES and scope["students"]:
		clauses.append(f"`tab{doctype}`.`student` in ({_in_quoted(scope['students'])})")

	if doctype in GROUP_FIELD_DOCTYPES and scope["groups"]:
		clauses.append(f"`tab{doctype}`.`student_group` in ({_in_quoted(scope['groups'])})")

	if doctype == "Course Schedule":
		if scope["groups"]:
			clauses.append(f"`tabCourse Schedule`.`student_group` in ({_in_quoted(scope['groups'])})")
		if scope["instructors"]:
			clauses.append(f"`tabCourse Schedule`.`instructor` in ({_in_quoted(scope['instructors'])})")

	if doctype == "Student Group" and scope["groups"]:
		clauses.append(f"`tabStudent Group`.`name` in ({_in_quoted(scope['groups'])})")

	return clauses


def build_query_conditions(doctype: str, user: str | None = None) -> str:
	"""Return the ``WHERE`` fragment for ``doctype`` ('' = no restriction)."""

	user = user or frappe.session.user
	if not _is_restricted(doctype, user):
		return ""

	clauses = _clauses_for(doctype, _scope(user))
	if not clauses:
		return "1=0"
	return "(" + " or ".join(clauses) + ")"


def _doc_in_scope(doctype: str, doc, scope: dict) -> bool:
	field_values = {
		"student": getattr(doc, "student", None),
		"student_group": getattr(doc, "student_group", None),
		"instructor": getattr(doc, "instructor", None),
	}

	if doctype == "Student Group":
		return doc.name in scope["groups"]

	if doctype == "Course Schedule":
		return field_values["student_group"] in scope["groups"] or field_values[
			"instructor"
		] in scope["instructors"]

	if doctype in STUDENT_FIELD_DOCTYPES and field_values["student"] in scope["students"]:
		return True

	if doctype in GROUP_FIELD_DOCTYPES and field_values["student_group"] in scope["groups"]:
		return True

	return False


def _make_has_permission(doctype: str):
	def _check(doc, ptype=None, user=None, debug=False):
		user = user or frappe.session.user
		if not _is_restricted(doctype, user):
			return True
		return _doc_in_scope(doctype, doc, _scope(user))

	_check.__name__ = f"{frappe.scrub(doctype)}_has_permission"
	return _check


def assessment_result_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Assessment Result", user)


def course_schedule_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Course Schedule", user)


def fees_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Fees", user)


def program_enrollment_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Program Enrollment", user)


def student_academic_status_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Student Academic Status", user)


def student_attendance_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Student Attendance", user)


def student_card_order_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Student Card Order", user)


def student_correspondence_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Student Correspondence", user)


def student_group_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Student Group", user)


def student_leave_application_query(user=None, doctype=None, **kwargs):
	return build_query_conditions("Student Leave Application", user)


def assessment_result_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Assessment Result")(doc, ptype, user, debug)


def course_schedule_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Course Schedule")(doc, ptype, user, debug)


def fees_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Fees")(doc, ptype, user, debug)


def program_enrollment_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Program Enrollment")(doc, ptype, user, debug)


def student_academic_status_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Student Academic Status")(doc, ptype, user, debug)


def student_attendance_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Student Attendance")(doc, ptype, user, debug)


def student_card_order_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Student Card Order")(doc, ptype, user, debug)


def student_correspondence_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Student Correspondence")(doc, ptype, user, debug)


def student_group_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Student Group")(doc, ptype, user, debug)


def student_leave_application_has_permission(doc, ptype=None, user=None, debug=False):
	return _make_has_permission("Student Leave Application")(doc, ptype, user, debug)
