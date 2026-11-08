# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Grades for the portal.

The pass/fail decision is authoritative: the grade comes from the ``Grading
Scale`` of the assessment plan that produced the result, and the pass threshold
from ``portal.passing_percentage`` (default 50) because the app does not store
one. The board of examiners can override the threshold in
``site_config.json`` without code changes.
"""

from __future__ import annotations

import frappe
from frappe.utils import flt

from education.portal.auth import passing_percentage

RESULT_FIELDS = [
	"name",
	"course",
	"academic_year",
	"academic_term",
	"student_group",
	"assessment_plan",
	"assessment_group",
	"grading_scale",
	"total_score",
	"maximum_score",
	"grade",
	"modified",
]

STATUS_FIELDS = [
	"name",
	"program_enrollment",
	"academic_year",
	"academic_term",
	"semester_number",
	"status",
	"is_summer_semester",
	"counts_towards_average",
	"is_paid",
	"decision_date",
]


def grade_from_scale(grading_scale: str | None, percentage: float) -> str | None:
	"""Return the grade code whose threshold is the highest one not exceeded."""

	if not grading_scale:
		return None
	intervals = frappe.get_all(
		"Grading Scale Interval",
		filters={"parent": grading_scale, "parenttype": "Grading Scale"},
		fields=["grade_code", "threshold"],
		order_by="threshold desc",
	)
	for interval in intervals:
		if percentage >= flt(interval.threshold):
			return interval.grade_code
	return intervals[-1].grade_code if intervals else None


def _percentage(score, maximum) -> float | None:
	maximum = flt(maximum)
	if maximum <= 0:
		return None
	return round(flt(score) / maximum * 100, 2)


def attempt(result, threshold: float) -> dict:
	"""One attempt (one submitted ``Assessment Result``) with the pass decision.

	Shared by :func:`for_student` and :mod:`education.portal.semester_card`."""

	percentage = _percentage(result.total_score, result.maximum_score)
	grade = result.grade
	if not grade and percentage is not None:
		grade = grade_from_scale(result.grading_scale, percentage)
	return {
		"assessment_result": result.name,
		"assessment_plan": result.assessment_plan,
		"assessment_group": result.get("assessment_group"),
		"date": str(result.modified)[:10] if result.modified else None,
		"score": flt(result.total_score),
		"maximum": flt(result.maximum_score),
		"percentage": percentage,
		"grade": grade,
		"passed": None if percentage is None else percentage >= threshold,
	}


def _course_meta(courses: list[str]) -> dict:
	if not courses:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Course",
			filters={"name": ["in", courses]},
			fields=["name", "course_name", "ects_credits", "passing_type"],
		)
	}


def for_student(
	student: str,
	academic_year: str | None = None,
	academic_term: str | None = None,
) -> dict:
	"""Grades grouped by course: final grade, attempts, average, semester status."""

	filters = {"student": student, "docstatus": 1}
	if academic_year:
		filters["academic_year"] = academic_year
	if academic_term:
		filters["academic_term"] = academic_term

	results = frappe.get_list(
		"Assessment Result",
		filters=filters,
		fields=RESULT_FIELDS,
		order_by="modified asc",
		limit_page_length=0,
	)

	courses = _course_meta(sorted({row.get("course") for row in results if row.get("course")}))
	threshold = passing_percentage()

	grouped: dict[str, dict] = {}
	for result in results:
		entry = grouped.setdefault(
			result.course,
			{
				"course": {
					"code": result.course,
					"name": (courses.get(result.course) or {}).get("course_name"),
					"ects": flt((courses.get(result.course) or {}).get("ects_credits")),
					"passing_type": (courses.get(result.course) or {}).get("passing_type"),
				},
				"academic_year": result.academic_year,
				"academic_term": result.academic_term,
				"attempts": [],
			},
		)
		entry["attempts"].append(attempt(result, threshold))

	grade_rows = []
	for entry in (value for _, value in sorted(grouped.items(), key=lambda item: item[0] or "")):
		final = entry["attempts"][-1]
		grade_rows.append(
			{
				**entry,
				"final": final,
				"passed": final["passed"],
				"best_percentage": max(
					(
						attempt["percentage"]
						for attempt in entry["attempts"]
						if attempt["percentage"] is not None
					),
					default=None,
				),
			}
		)

	weighted = [
		(row["final"]["percentage"], row["course"]["ects"])
		for row in grade_rows
		if row["final"]["percentage"] is not None and flt(row["course"]["ects"]) > 0
	]
	average = {
		"method": "ects_weighted",
		"value": round(
			sum(percent * ects for percent, ects in weighted) / sum(ects for _, ects in weighted), 2
		)
		if weighted
		else None,
		"courses": len(grade_rows),
		"passed": sum(1 for row in grade_rows if row["passed"]),
		"failed": sum(1 for row in grade_rows if row["passed"] is False),
	}

	statuses = frappe.get_list(
		"Student Academic Status",
		filters={"student": student, "docstatus": 1},
		fields=STATUS_FIELDS,
		order_by="semester_number asc",
		limit_page_length=0,
	)

	return {
		"student": student,
		"passing_percentage": threshold,
		"courses": grade_rows,
		"average": average,
		"semesters": statuses,
	}


def semester_status(student: str, academic_year: str | None = None) -> dict | None:
	"""Latest semester settlement of the student (used on the dashboard)."""

	filters = {"student": student, "docstatus": 1}
	if academic_year:
		filters["academic_year"] = academic_year
	rows = frappe.get_list(
		"Student Academic Status",
		filters=filters,
		fields=STATUS_FIELDS,
		order_by="semester_number desc",
		limit_page_length=1,
	)
	return rows[0] if rows else None
