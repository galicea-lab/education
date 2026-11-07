"""Whitelisted POL-on endpoints used by the Galwork hub.

The hub runs the BPMN process around a report (preparation, approval, sending,
verification); these endpoints are the Frappe side of the steps. Every call
requires the caller to have access to the ``Student`` record.
"""

from __future__ import annotations

import frappe
from frappe.utils import cint, flt

from . import service


def _check(student: str, ptype: str = "read") -> None:
	if frappe.flags.in_test:
		return
	if not frappe.has_permission("Student", ptype, student):
		frappe.throw(
			frappe._("Not permitted to access POL-on data of {0}").format(frappe.bold(student)),
			frappe.PermissionError,
		)


@frappe.whitelist()
def prepare_student_report(
	student: str,
	program_enrollment: str | None = None,
	fos_code: str | None = None,
	academic_year: str | None = None,
	academic_term: str | None = None,
	study_semester: int | str | None = None,
	ects: float | str | None = None,
	level: str | None = None,
) -> dict:
	"""Return the POL-on payload of a student for review in the process."""

	_check(student)
	return service.prepare_student_report(
		student,
		program_enrollment,
		fos_code=fos_code,
		academic_year=academic_year,
		academic_term=academic_term,
		study_semester=cint(study_semester) or None,
		ects=flt(ects) if ects is not None else None,
		level=level,
	)


@frappe.whitelist()
def send_student_report(
	student: str,
	program_enrollment: str | None = None,
	fos_code: str | None = None,
	academic_year: str | None = None,
	academic_term: str | None = None,
	study_semester: int | str | None = None,
	ects: float | str | None = None,
	level: str | None = None,
) -> dict:
	"""Send a prepared report to POL-on (called after the hub approved it)."""

	_check(student, "write")
	return service.send_student_report(
		student,
		program_enrollment,
		fos_code=fos_code,
		academic_year=academic_year,
		academic_term=academic_term,
		study_semester=cint(study_semester) or None,
		ects=flt(ects) if ects is not None else None,
		level=level,
	)


@frappe.whitelist()
def verify_student_report(student: str) -> dict:
	"""Return what POL-on holds for the student, for comparison in the hub."""

	_check(student)
	return service.verify_student_report(student)


@frappe.whitelist()
def add_semester(student_in_university_id: str, study_id: str, semester_number: int | str) -> dict:
	"""Add a semester to a POL-on study using identifiers from verification."""

	if not frappe.flags.in_test and not frappe.has_permission("Student", "write"):
		frappe.throw(frappe._("Not permitted to report semesters to POL-on"), frappe.PermissionError)

	return service.add_semester(student_in_university_id, study_id, cint(semester_number))


@frappe.whitelist()
def list_fields_of_study() -> list[dict]:
	"""Return the POL-on fields of study of the institution (for dictionaries)."""

	from .polon_api import get_client

	client = get_client()
	client.authenticate()
	return [
		{"name": field.get("name"), "level": field.get("level"), "fosCode": field.get("fosCode")}
		for field in client.find_all_fos()
	]


@frappe.whitelist()
def polon_add(
	student: str,
	program_enrollment: str | None = None,
	fos_code: str | None = None,
	study_semester: int | str | None = None,
) -> dict:
	"""Legacy entry point: report a student to POL-on.

	Kept under the old name for existing scripts; it now takes a ``Student`` and
	an optional ``Program Enrollment`` instead of the hardcoded sample values
	(album number, degree programme, semester, ``Gender.MALE``).
	"""

	_check(student, "write")
	return service.send_student_report(
		student,
		program_enrollment,
		fos_code=fos_code,
		study_semester=cint(study_semester) or None,
	)
