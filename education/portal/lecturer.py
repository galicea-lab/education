# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Lecturer workspace: groups, rosters, attendance and assessment results.

Every function first proves that the group belongs to the instructor
(:func:`_assert_group`); the service-account caller passes ``instructor``
explicitly, a session user gets it from the account.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from education.portal import attendance as attendance_portal
from education.portal import auth, grades


def _assert_group(group: str, instructor: str) -> str:
	"""Return the group name when the instructor teaches it."""

	if not frappe.db.exists("Student Group", group):
		raise frappe.DoesNotExistError(_("Student Group {0} does not exist").format(group))

	teaches = frappe.db.exists(
		"Student Group Instructor",
		{"parent": group, "parenttype": "Student Group", "instructor": instructor},
	)
	if not teaches:
		raise frappe.PermissionError(
			_("Not permitted to access group {0}").format(group)
		)
	return group


def _groups_of(instructor: str, academic_term: str | None = None) -> list[dict]:
	names = auth.group_names_of_instructor(instructor, academic_term)
	if not names:
		return []

	rows = frappe.get_all(
		"Student Group",
		filters={"name": ["in", names], "disabled": 0},
		fields=[
			"name",
			"student_group_name",
			"course",
			"program",
			"academic_year",
			"academic_term",
			"max_strength",
		],
		order_by="student_group_name asc",
	)

	names = [row["name"] for row in rows]
	counts = {
		row.parent: row.count
		for row in frappe.db.get_all(
			"Student Group Student",
			filters={"parent": ["in", names], "parenttype": "Student Group", "active": 1},
			fields=["parent", "count(name) as count"],
			group_by="parent",
		)
	}
	return [{**row, "students": counts.get(row["name"], 0)} for row in rows]


def context(instructor: str, academic_term: str | None = None) -> dict:
	"""Groups and courses of the lecturer for the current term."""

	term = academic_term or frappe.db.get_single_value("Education Settings", "current_academic_term")
	groups = _groups_of(instructor, term) or _groups_of(instructor)
	courses = sorted({row["course"] for row in groups if row.get("course")})

	profile = frappe.get_all(
		"Instructor",
		filters={"name": instructor},
		fields=["name", "instructor_name", "department", "image"],
	)
	return {
		"instructor": profile[0] if profile else {"name": instructor},
		"academic_term": term,
		"groups": groups,
		"courses": [
			{"code": code, "name": frappe.db.get_value("Course", code, "course_name")}
			for code in courses
		],
	}


def roster(group: str, instructor: str) -> list[dict]:
	"""Students of a group (without attendance state)."""

	_assert_group(group, instructor)
	return attendance_portal.for_group(group)


def group_attendance(
	group: str, instructor: str, date: str | None = None
) -> dict:
	"""Attendance sheet of a group for one day."""

	_assert_group(group, instructor)
	return {
		"group": group,
		"date": date or today(),
		"students": attendance_portal.for_group(group, date),
		"freeze_date": frappe.db.get_single_value("Education Settings", "attendance_freeze_date"),
	}


def save_attendance(
	group: str,
	instructor: str,
	date: str,
	entries: list[dict],
	course_schedule: str | None = None,
) -> dict:
	"""Save the attendance sheet of a group for one day."""

	_assert_group(group, instructor)

	freeze_date = frappe.db.get_single_value("Education Settings", "attendance_freeze_date")
	if freeze_date and getdate(date) <= getdate(freeze_date):
		frappe.throw(
			_("Attendance cannot be changed before the freeze date {0}").format(freeze_date)
		)

	roster_students = {
		row["student"]
		for row in frappe.get_all(
			"Student Group Student",
			filters={"parent": group, "parenttype": "Student Group", "active": 1},
			fields=["student"],
		)
	}
	for entry in entries or []:
		if entry.get("student") not in roster_students:
			frappe.throw(
				_("Student {0} is not an active member of group {1}").format(
					entry.get("student"), group
				)
			)

	result = attendance_portal.save_for_group(group, date, entries, course_schedule)
	return {"group": group, "date": date, **result}


def group_assessments(group: str, instructor: str) -> list[dict]:
	"""Assessment plans of the group with criteria and grading scale."""

	_assert_group(group, instructor)

	plans = frappe.get_all(
		"Assessment Plan",
		filters={"student_group": group, "docstatus": ["<", 2]},
		fields=[
			"name",
			"assessment_name",
			"course",
			"assessment_group",
			"grading_scale",
			"maximum_assessment_score",
			"schedule_date",
			"academic_term",
			"academic_year",
			"docstatus",
		],
		order_by="schedule_date desc",
	)
	if not plans:
		return []

	names = [plan["name"] for plan in plans]
	criteria: dict[str, list[dict]] = {}
	for row in frappe.get_all(
		"Assessment Plan Criteria",
		filters={"parent": ["in", names], "parenttype": "Assessment Plan"},
		fields=["parent", "assessment_criteria", "maximum_score", "weightage"],
	):
		criteria.setdefault(row.parent, []).append(
			{
				"criterion": row.assessment_criteria,
				"maximum_score": flt(row.maximum_score),
				"weightage": flt(row.weightage),
			}
		)

	results = {
		row.parent: row.count
		for row in frappe.db.get_all(
			"Assessment Result",
			filters={"assessment_plan": ["in", names], "docstatus": 1},
			fields=["assessment_plan as parent", "count(name) as count"],
			group_by="assessment_plan",
		)
	}

	for plan in plans:
		plan["criteria"] = criteria.get(plan["name"], [])
		plan["results_submitted"] = results.get(plan["name"], 0)
	return plans


def save_results(
	group: str,
	instructor: str,
	assessment_plan: str,
	rows: list[dict],
	submit: bool = False,
) -> dict:
	"""Save (and optionally submit) assessment results of the group."""

	_assert_group(group, instructor)

	plan = frappe.db.get_value(
		"Assessment Plan",
		assessment_plan,
		["name", "student_group", "course", "program", "grading_scale", "maximum_assessment_score"],
		as_dict=True,
	)
	if not plan:
		frappe.throw(_("Assessment Plan {0} does not exist").format(assessment_plan))
	if plan.student_group != group:
		frappe.throw(
			_("Assessment plan {0} does not belong to group {1}").format(assessment_plan, group)
		)

	roster_students = {
		row["student"]
		for row in frappe.get_all(
			"Student Group Student",
			filters={"parent": group, "parenttype": "Student Group", "active": 1},
			fields=["student"],
		)
	}

	saved = []
	for row in rows or []:
		student = row.get("student")
		if student not in roster_students:
			frappe.throw(_("Student {0} is not an active member of group {1}").format(student, group))

		details = row.get("details") or {}
		doc = _get_or_new_result(student, assessment_plan)
		if doc is None:
			continue

		doc.details = []
		total = 0.0
		max_total = 0.0
		for criterion, score in details.items():
			doc.append("details", {"assessment_criteria": criterion, "score": flt(score)})
			total += flt(score)
		for criterion in frappe.get_all(
			"Assessment Plan Criteria",
			filters={"parent": assessment_plan, "parenttype": "Assessment Plan"},
			fields=["assessment_criteria", "maximum_score"],
		):
			max_total += flt(criterion.maximum_score)

		doc.total_score = flt(row.get("total_score", total))
		doc.maximum_score = flt(plan.maximum_assessment_score or max_total)
		doc.grade = grades.grade_from_scale(
			plan.grading_scale,
			flt(doc.total_score) / flt(doc.maximum_score) * 100 if flt(doc.maximum_score) else 0,
		)
		doc.comment = row.get("comment") or doc.get("comment")
		doc.flags.ignore_permissions = True
		doc.save()

		if submit:
			doc.submit()
		saved.append({"student": student, "assessment_result": doc.name, "docstatus": doc.docstatus})

	return {"assessment_plan": assessment_plan, "saved": saved, "count": len(saved)}


def _get_or_new_result(student: str, assessment_plan: str):
	existing = frappe.get_all(
		"Assessment Result",
		filters={
			"student": student,
			"assessment_plan": assessment_plan,
			"docstatus": ["<", 2],
		},
		order_by="modified asc",
		limit=1,
	)
	if not existing:
		doc = frappe.new_doc("Assessment Result")
		doc.student = student
		doc.assessment_plan = assessment_plan
		return doc
	if existing[0].docstatus == 1:
		frappe.msgprint(
			_("Result of {0} for {1} is already submitted").format(assessment_plan, student),
			indicator="orange",
		)
		return None
	return frappe.get_doc("Assessment Result", existing[0].name)
