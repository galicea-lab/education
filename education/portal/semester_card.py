# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Semester achievement card of a student (Karta Okresowych Osiągnięć, KOOS).

One call returns everything the printout needs for one programme enrollment
and one academic term:

* header - student, programme (degree, study mode), academic year and term,
  semester number, year of study and the semester settlement
  (``Student Academic Status``),
* courses - the courses of the semester from the study plan
  (``Program Course.semester_number``), plus courses the student was graded in
  or attends in that term although the plan places them elsewhere, each with
  the form of classes (``Course.class_type``), the passing type, ECTS, the
  lecturer and every attempt,
* totals - ECTS planned and earned, the ECTS-weighted average.

Where the data comes from:

* attempts - submitted ``Assessment Result`` of the term, graded exactly like
  :func:`education.portal.grades.attempt` (grading scale and
  ``portal.passing_percentage``). The attempt is labelled by the result's
  ``Assessment Group`` through ``portal.semester_card.attempt_labels``
  (e.g. ``{"Poprawka": "II"}``); without a mapping it is the attempt number,
* lecturer - the examiner of the assessment plan of the final attempt, then of
  earlier attempts, then the instructors of the student's course groups,
* academic title - ``Employee.salutation`` of the instructor's employee record
  (the Instructor doctype has no title field),
* additional courses - derived from the study plan: a course planned for an
  earlier semester is ``repeat`` (repetition or conditional pass), one missing
  from the plan is ``program_difference``, one planned later is
  ``other_semester``. Their ECTS are counted separately, like U10 did.

Results are read with :func:`frappe.get_list`, so the row-level conditions of
:mod:`education.portal.permissions` apply. The card itself is assembled by
:func:`build_card`, which does not touch the database.
"""

from __future__ import annotations

import math

import frappe
from frappe import _
from frappe.utils import flt, today

from education.portal import auth, grades

ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X")

STUDENT_FIELDS = ["name", "student_name", "first_name", "middle_name", "last_name", "album_number"]
PROGRAM_FIELDS = ["name", "program_name", "program_abbreviation", "degree_type", "study_mode"]
COURSE_FIELDS = ["name", "course_name", "ects_credits", "passing_type", "class_type"]
PLAN_FIELDS = ["name", "examiner", "examiner_name", "supervisor", "schedule_date", "assessment_group"]
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
]

#: plan category -> counted with the regular semester ECTS
REGULAR = "program"


# ----------------------------------------------------------------------
# configuration
# ----------------------------------------------------------------------
def attempt_labels() -> dict:
	"""``portal.semester_card.attempt_labels``: Assessment Group -> label."""

	config = auth.portal_config().get("semester_card") or {}
	labels = config.get("attempt_labels") or {}
	return {str(key): str(value) for key, value in labels.items()} if isinstance(labels, dict) else {}


# ----------------------------------------------------------------------
# pure assembly
# ----------------------------------------------------------------------
def roman(number: int | None) -> str | None:
	if not number or number < 1:
		return None
	return ROMAN[number - 1] if number <= len(ROMAN) else str(number)


def numeric_grade(grade: str | None) -> float | None:
	"""``"4,5"``/``"4.5"`` -> 4.5; non-numeric grades (``"zal"``) -> None."""

	if grade is None:
		return None
	try:
		return float(str(grade).strip().replace(",", "."))
	except ValueError:
		return None


def category(course: str, semester: int | None, plan: dict) -> str:
	"""Where the course sits in the study plan relative to the card's semester."""

	planned = plan.get(course)
	if planned is None:
		return "program_difference"
	planned_semester = planned.get("semester_number")
	if not semester or not planned_semester or planned_semester == semester:
		return REGULAR
	return "repeat" if planned_semester < semester else "other_semester"


def _lecturer(instructor: str, instructors: dict, fallback_name: str | None = None) -> dict:
	meta = instructors.get(instructor) or {}
	name = meta.get("instructor_name") or fallback_name or instructor
	title = meta.get("title") or ""
	return {
		"instructor": instructor,
		"name": name,
		"title": title or None,
		"display": f"{title} {name}".strip(),
	}


def build_card(
	*,
	student: dict,
	program: dict,
	enrollment: str,
	term: dict,
	status: dict | None,
	plan: dict,
	results: list,
	courses: dict,
	plans: dict,
	group_courses: dict,
	group_instructors: dict,
	instructors: dict,
	threshold: float,
	labels: dict | None = None,
) -> dict:
	"""Assemble the card from already loaded rows (no database access).

	``plan`` - {course: Program Course row} for the whole programme,
	``results`` - submitted Assessment Result rows of the term,
	``courses`` - {course: Course row}, ``plans`` - {plan: Assessment Plan row},
	``group_courses`` - {course: [student group]} for the student's groups,
	``group_instructors`` - {group: [(instructor, instructor_name)]},
	``instructors`` - {instructor: {instructor_name, title}}.
	"""

	labels = labels or {}
	semester = (status or {}).get("semester_number") or None

	by_course: dict[str, list] = {}
	for result in sorted(
		results,
		key=lambda row: (
			str(
				(plans.get(row.get("assessment_plan")) or {}).get("schedule_date")
				or row.get("modified")
				or ""
			),
			str(row.get("modified") or ""),
		),
	):
		by_course.setdefault(result.get("course"), []).append(result)

	# Without a known semester only graded/attended courses are listed - the
	# whole study plan would not be a semester card.
	planned_now = [
		name
		for name, row in sorted(plan.items(), key=lambda item: (item[1].get("idx") or 0, item[0]))
		if semester and row.get("semester_number") == semester
	]
	extra = sorted((set(by_course) | set(group_courses)) - set(planned_now) - {None})

	rows = []
	for course in planned_now + extra:
		meta = courses.get(course) or {}
		planned = plan.get(course) or {}
		kind = category(course, semester, plan)

		attempts = []
		examiners: list[str] = []
		for number, result in enumerate(by_course.get(course, []), start=1):
			entry = grades.attempt(frappe._dict(result), threshold)
			assessment_plan = plans.get(result.get("assessment_plan")) or {}
			group = entry.get("assessment_group") or assessment_plan.get("assessment_group")
			entry.update(
				{
					"number": number,
					"assessment_group": group,
					"label": labels.get(group or "") or str(number),
					"date": str(assessment_plan.get("schedule_date") or "") or entry["date"],
					"examiner": assessment_plan.get("examiner"),
				}
			)
			attempts.append(entry)
			if assessment_plan.get("examiner"):
				examiners.append(assessment_plan.get("examiner"))

		lecturer_ids: list[str] = []
		for instructor in list(reversed(examiners)):
			if instructor not in lecturer_ids:
				lecturer_ids.append(instructor)
		groups = list(group_courses.get(course, []))
		groups += [r.get("student_group") for r in by_course.get(course, []) if r.get("student_group")]
		group_names: dict[str, str] = {}
		for group in groups:
			for instructor, instructor_name in group_instructors.get(group, []):
				group_names.setdefault(instructor, instructor_name)
				if instructor not in lecturer_ids:
					lecturer_ids.append(instructor)
		lecturers = [_lecturer(i, instructors, group_names.get(i)) for i in lecturer_ids]

		final = attempts[-1] if attempts else None
		rows.append(
			{
				"code": course,
				"name": meta.get("course_name") or planned.get("course_name") or course,
				"class_type": meta.get("class_type"),
				"passing_type": meta.get("passing_type"),
				"ects": flt(meta.get("ects_credits")),
				"plan_semester": planned.get("semester_number") or None,
				"required": bool(planned.get("required")) if planned else None,
				"category": kind,
				"additional": kind != REGULAR,
				"lecturer": lecturers[0] if lecturers else None,
				"lecturers": lecturers,
				"attempts": attempts,
				"final": final,
				"grade": final["grade"] if final else None,
				"passed": final["passed"] if final else None,
			}
		)

	def ects(rows_, *, passed: bool | None = None):
		return round(sum(r["ects"] for r in rows_ if passed is None or bool(r["passed"]) == passed), 2)

	regular = [r for r in rows if not r["additional"]]
	additional = [r for r in rows if r["additional"]]
	weighted = [
		(r["final"]["percentage"], r["ects"])
		for r in rows
		if r["final"] and r["final"]["percentage"] is not None and r["ects"] > 0
	]
	weighted_grades = [
		(numeric_grade(r["grade"]), r["ects"])
		for r in rows
		if numeric_grade(r["grade"]) is not None and r["ects"] > 0
	]

	max_semester = max((row.get("semester_number") or 0 for row in plan.values()), default=0)
	year = math.ceil(semester / 2) if semester else None

	return {
		"student": student,
		"program": program,
		"program_enrollment": enrollment,
		"academic_year": term.get("academic_year"),
		"academic_term": term,
		"semester_number": semester,
		"year_of_study": year,
		"year_of_study_roman": roman(year),
		"next_semester_number": semester + 1 if semester and semester < max_semester else None,
		"semester_status": status,
		"passing_percentage": threshold,
		"generated_on": today(),
		"courses": rows,
		"totals": {
			"courses": len(rows),
			"passed": sum(1 for r in rows if r["passed"]),
			"failed": sum(1 for r in rows if r["passed"] is False),
			"not_graded": sum(1 for r in rows if r["passed"] is None),
			"ects_planned": ects(regular),
			"ects_earned": ects(regular, passed=True),
			"ects_additional": ects(additional),
			"ects_additional_earned": ects(additional, passed=True),
			"average_percentage": round(sum(p * e for p, e in weighted) / sum(e for _, e in weighted), 2)
			if weighted
			else None,
			"average_grade": round(
				sum(g * e for g, e in weighted_grades) / sum(e for _, e in weighted_grades), 2
			)
			if weighted_grades
			else None,
		},
	}


# ----------------------------------------------------------------------
# loading
# ----------------------------------------------------------------------
def _enrollment(student: str, program: str | None) -> dict:
	filters = {"student": student, "docstatus": 1}
	if program:
		filters["program"] = program
	rows = frappe.get_list(
		"Program Enrollment",
		filters=filters,
		fields=["name", "program", "academic_year", "academic_term"],
		order_by="enrollment_date desc",
		limit_page_length=1,
	)
	if not rows:
		raise frappe.DoesNotExistError(_("Student {0} has no submitted programme enrollment").format(student))
	return rows[0]


def _status(enrollment: str, academic_term: str | None) -> dict | None:
	filters = {"program_enrollment": enrollment, "docstatus": 1}
	if academic_term:
		filters["academic_term"] = academic_term
	rows = frappe.get_list(
		"Student Academic Status",
		filters=filters,
		fields=STATUS_FIELDS,
		order_by="semester_number desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	row = dict(rows[0])
	row["decision_date"] = str(row.get("decision_date") or "") or None
	return row


def _instructors(names: set[str]) -> dict:
	if not names:
		return {}
	rows = frappe.get_all(
		"Instructor",
		filters={"name": ["in", sorted(names)]},
		fields=["name", "instructor_name", "employee"],
	)
	employees = sorted({row.employee for row in rows if row.get("employee")})
	titles = {}
	if employees and frappe.get_meta("Employee").has_field("salutation"):
		titles = {
			row.name: row.salutation
			for row in frappe.get_all(
				"Employee", filters={"name": ["in", employees]}, fields=["name", "salutation"]
			)
		}
	return {
		row.name: {"instructor_name": row.instructor_name, "title": titles.get(row.get("employee"))}
		for row in rows
	}


def for_student(student: str, academic_term: str | None = None, program: str | None = None) -> dict:
	"""KOOS of the student for one term (default: the latest settled semester)."""

	rows = frappe.get_list("Student", filters={"name": student}, fields=STUDENT_FIELDS, limit_page_length=1)
	if not rows:
		raise frappe.DoesNotExistError(_("Student {0} does not exist").format(student))

	enrollment = _enrollment(student, program)
	program_name = enrollment.program
	status = _status(enrollment.name, academic_term)
	term_name = (
		academic_term
		or (status or {}).get("academic_term")
		or frappe.db.get_single_value("Education Settings", "current_academic_term")
	)
	if not term_name:
		raise frappe.ValidationError(_("Academic term is required for the semester card"))
	term = frappe.db.get_value(
		"Academic Term",
		term_name,
		["name", "term_name", "academic_year", "term_start_date", "term_end_date"],
		as_dict=True,
	)
	if not term:
		raise frappe.DoesNotExistError(_("Academic Term {0} does not exist").format(term_name))
	term = {key: (str(value) if value is not None else None) for key, value in term.items()}

	program_row = frappe.db.get_value("Program", program_name, PROGRAM_FIELDS, as_dict=True) or {}

	plan = {
		row.course: row
		for row in frappe.get_all(
			"Program Course",
			filters={"parent": program_name, "parenttype": "Program"},
			fields=["course", "course_name", "semester_number", "required", "idx"],
			order_by="idx asc",
		)
	}

	results = [
		row
		for row in frappe.get_list(
			"Assessment Result",
			filters={"student": student, "docstatus": 1, "academic_term": term["name"]},
			fields=[*grades.RESULT_FIELDS, "program"],
			order_by="modified asc",
			limit_page_length=0,
		)
		if not row.get("program") or row.get("program") == program_name
	]

	group_rows = []
	member_of = auth.student_group_names(student)
	if member_of:
		group_rows = frappe.get_all(
			"Student Group",
			filters={
				"name": ["in", member_of],
				"academic_term": term["name"],
				"group_based_on": "Course",
				"disabled": 0,
			},
			fields=["name", "course", "program"],
		)
	group_courses: dict[str, list] = {}
	for row in group_rows:
		if row.get("course") and (not row.get("program") or row.get("program") == program_name):
			group_courses.setdefault(row.course, []).append(row.name)

	group_names = {g for groups in group_courses.values() for g in groups}
	group_names |= {row.student_group for row in results if row.get("student_group")}
	group_instructors: dict[str, list] = {}
	if group_names:
		for row in frappe.get_all(
			"Student Group Instructor",
			filters={"parent": ["in", sorted(group_names)], "parenttype": "Student Group"},
			fields=["parent", "instructor", "instructor_name"],
			order_by="idx asc",
		):
			group_instructors.setdefault(row.parent, []).append((row.instructor, row.instructor_name))

	plan_names = sorted({row.assessment_plan for row in results if row.get("assessment_plan")})
	plans = (
		{
			row.name: row
			for row in frappe.get_all(
				"Assessment Plan", filters={"name": ["in", plan_names]}, fields=PLAN_FIELDS
			)
		}
		if plan_names
		else {}
	)

	course_names = sorted(set(plan) | set(group_courses) | {r.course for r in results if r.get("course")})
	courses = (
		{
			row.name: row
			for row in frappe.get_all("Course", filters={"name": ["in", course_names]}, fields=COURSE_FIELDS)
		}
		if course_names
		else {}
	)

	instructor_ids = {p.examiner for p in plans.values() if p.get("examiner")}
	instructor_ids |= {i for rows_ in group_instructors.values() for i, _name in rows_ if i}

	return build_card(
		student=dict(rows[0]),
		program=dict(program_row),
		enrollment=enrollment.name,
		term=term,
		status=status,
		plan=plan,
		results=results,
		courses=courses,
		plans=plans,
		group_courses=group_courses,
		group_instructors=group_instructors,
		instructors=_instructors(instructor_ids),
		threshold=auth.passing_percentage(),
		labels=attempt_labels(),
	)
