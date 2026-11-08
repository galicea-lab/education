# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Timetable assembly for the portal.

Reads ``Course Schedule`` through :func:`frappe.get_list`, so the row-level
conditions from :mod:`education.portal.permissions` apply on top of the filters
derived from the resolved principal.
"""

from __future__ import annotations

import frappe
from frappe.utils import getdate

from education.portal.auth import group_names_of_instructor, student_group_names

ENTRY_FIELDS = [
	"name",
	"course",
	"student_group",
	"instructor",
	"instructor_name",
	"room",
	"schedule_date",
	"from_time",
	"to_time",
	"title",
	"class_schedule_color",
	"program",
]


def _course_meta(courses: list[str]) -> dict:
	if not courses:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Course",
			filters={"name": ["in", courses]},
			fields=["name", "course_name", "class_type", "passing_type", "ects_credits"],
		)
	}


def _normalise(row, courses: dict) -> dict:
	course = courses.get(row.get("course")) or {}
	return {
		"name": row.get("name"),
		"date": str(row.get("schedule_date") or ""),
		"weekday": getdate(row.get("schedule_date")).isoweekday()
		if row.get("schedule_date")
		else None,
		"from": str(row.get("from_time") or ""),
		"to": str(row.get("to_time") or ""),
		"course": {
			"code": row.get("course"),
			"name": course.get("course_name") or row.get("course"),
			"type": course.get("class_type"),
		},
		"group": row.get("student_group"),
		"room": row.get("room"),
		"lecturer": row.get("instructor_name") or row.get("instructor"),
		"program": row.get("program"),
		"title": row.get("title"),
		"color": row.get("class_schedule_color"),
	}


def _query(filters: dict, from_date, to_date) -> list[dict]:
	filters = dict(filters)
	filters["schedule_date"] = ["between", [getdate(from_date), getdate(to_date)]]
	rows = frappe.get_list(
		"Course Schedule",
		filters=filters,
		fields=ENTRY_FIELDS,
		order_by="schedule_date asc, from_time asc",
		limit_page_length=0,
	)
	courses = _course_meta(sorted({row.get("course") for row in rows if row.get("course")}))
	return [_normalise(row, courses) for row in rows]


def for_student(
	student: str, from_date, to_date, program: str | None = None
) -> list[dict]:
	"""Timetable of a student: the groups they are an active member of."""

	groups = student_group_names(student)
	if not groups:
		return []

	filters = {"student_group": ["in", groups]}
	if program:
		filters["program"] = program
	return _query(filters, from_date, to_date)


def for_instructor(instructor: str, from_date, to_date) -> list[dict]:
	"""Timetable of a lecturer: the classes they teach."""

	groups = group_names_of_instructor(instructor)
	if not groups:
		return []
	return _query({"student_group": ["in", groups]}, from_date, to_date)


def for_group(group: str, from_date, to_date) -> list[dict]:
	"""Timetable of a single student group (used by the roster screens)."""

	return _query({"student_group": group}, from_date, to_date)
