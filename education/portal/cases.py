# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Dean's office correspondence for the portal.

Creating a case inserts and submits a ``Student Correspondence`` document and
tells the Galwork hub about it with a signed webhook
(:mod:`education.integrations.hub`); the hub starts the BPMN process and writes
progress back through the ``Student Correspondence`` module methods.
"""

from __future__ import annotations

import frappe
from frappe import _

from education.integrations import hub

DOCTYPE = "Student Correspondence"
EVENT_CREATED = "student_correspondence.created"

LIST_FIELDS = [
	"name",
	"student",
	"correspondence_type",
	"subject",
	"status",
	"signature_status",
	"response_date",
	"program",
	"academic_year",
	"creation",
	"modified",
]

DETAIL_FIELDS = [
	*LIST_FIELDS,
	"album_number",
	"description",
	"form_data",
	"response",
	"process_instance_id",
	"signius_folder_id",
	"signius_document_id",
	"signed_document",
]


def list_for_student(student: str) -> list[dict]:
	"""Cases of a student, newest first."""

	rows = frappe.get_list(
		DOCTYPE,
		filters={"student": student},
		fields=LIST_FIELDS,
		order_by="modified desc",
		limit_page_length=0,
	)
	for row in rows:
		row["creation"] = str(row.get("creation") or "")
		row["modified"] = str(row.get("modified") or "")
		row["response_date"] = str(row.get("response_date") or "") or None
	return rows


def get_case(name: str, student: str) -> dict:
	"""Single case with the dean's office answer and the signed document."""

	doc = frappe.get_doc(DOCTYPE, name)
	if doc.student != student:
		raise frappe.PermissionError(_("This case belongs to another student"))
	doc.check_permission("read")

	data = {field: doc.get(field) for field in DETAIL_FIELDS if doc.meta.has_field(field)}
	data["creation"] = str(doc.get("creation") or "")
	data["modified"] = str(doc.get("modified") or "")
	data["response_date"] = str(doc.get("response_date") or "") or None
	data["timeline"] = _timeline(doc)
	return data


def _timeline(doc) -> list[dict]:
	"""Human readable history: creation, hub start, answer, signature."""

	events = [
		{"at": str(doc.creation or ""), "event": "created", "status": doc.get("status")}
	]
	if doc.get("process_instance_id"):
		events.append(
			{"at": str(doc.modified or ""), "event": "process_started", "detail": doc.process_instance_id}
		)
	if doc.get("response"):
		events.append(
			{
				"at": str(doc.get("response_date") or ""),
				"event": "answered",
				"detail": doc.get("response"),
			}
		)
	if doc.get("signed_document"):
		events.append(
			{
				"at": str(doc.modified or ""),
				"event": "signed",
				"detail": doc.get("signed_document"),
			}
		)
	return events


def create_case(
	student: str,
	correspondence_type: str,
	subject: str,
	description: str,
	*,
	program: str | None = None,
	academic_year: str | None = None,
	form_data: str | None = None,
) -> dict:
	"""Submit a new case on behalf of the student and notify the hub."""

	if not subject or not subject.strip():
		frappe.throw(_("Subject is required"))
	if not description or not description.strip():
		frappe.throw(_("Description is required"))

	case_type = frappe.db.get_value(
		"Correspondence Type", correspondence_type, ["name", "is_active"], as_dict=True
	)
	if not case_type:
		frappe.throw(_("Unknown correspondence type {0}").format(correspondence_type))
	if not case_type.is_active:
		frappe.throw(_("Correspondence type {0} is not active").format(correspondence_type))

	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"student": student,
			"correspondence_type": correspondence_type,
			"subject": subject.strip(),
			"description": description.strip(),
			"program": program,
			"academic_year": academic_year,
			"form_data": form_data,
			"status": "Draft",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.submit()

	hub.notify(
		EVENT_CREATED,
		{
			"name": doc.name,
			"student": doc.student,
			"album_number": doc.get("album_number"),
			"correspondence_type": doc.correspondence_type,
			"process_model_key": doc.get("process_model_key"),
			"subject": doc.subject,
			"program": doc.get("program"),
			"academic_year": doc.get("academic_year"),
			"status": doc.status,
			"created_by": frappe.session.user,
		},
		reference_doctype=DOCTYPE,
		reference_name=doc.name,
	)

	return get_case(doc.name, student)


def list_types() -> list[dict]:
	"""Case types the student may choose from (active only)."""

	return frappe.get_all(
		"Correspondence Type",
		filters={"is_active": 1},
		fields=["name", "type_name", "description"],
		order_by="type_name asc",
	)
