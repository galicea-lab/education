# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Attendance summary for the portal."""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, today

RECORD_FIELDS = [
	"name",
	"student",
	"student_group",
	"course_schedule",
	"date",
	"status",
]


def _groups(codes: list[str]) -> dict:
	if not codes:
		return {}
	return {
		row.name: row
		for row in frappe.get_all(
			"Student Group",
			filters={"name": ["in", codes]},
			fields=["name", "student_group_name", "course"],
		)
	}


def for_student(
	student: str, from_date: str | None = None, to_date: str | None = None
) -> dict:
	"""Per-group presence summary plus the most recent absences."""

	filters: dict = {"student": student, "docstatus": 1}
	if from_date or to_date:
		filters["date"] = ["between", [getdate(from_date or "1970-01-01"), getdate(to_date or today())]]

	records = frappe.get_list(
		"Student Attendance",
		filters=filters,
		fields=RECORD_FIELDS,
		order_by="date desc",
		limit_page_length=0,
	)

	groups = _groups(sorted({row.get("student_group") for row in records if row.get("student_group")}))

	summary: dict[str, dict] = {}
	for record in records:
		group = record.get("student_group") or ""
		entry = summary.setdefault(
			group,
			{
				"group": group,
				"group_name": (groups.get(group) or {}).get("student_group_name"),
				"course": (groups.get(group) or {}).get("course"),
				"present": 0,
				"absent": 0,
				"leave": 0,
				"percent": None,
			},
		)
		status = (record.get("status") or "").lower()
		if status == "present":
			entry["present"] += 1
		elif status == "absent":
			entry["absent"] += 1
		elif status == "leave":
			entry["leave"] += 1

	for entry in summary.values():
		marked = entry["present"] + entry["absent"]
		entry["percent"] = round(entry["present"] / marked * 100, 2) if marked else None

	absences = [
		{
			"date": str(record.get("date")),
			"group": record.get("student_group"),
			"course_schedule": record.get("course_schedule"),
			"attendance": record.get("name"),
			"excused": False,
		}
		for record in records
		if (record.get("status") or "").lower() == "absent"
	][:50]

	excused = {
		row.course_schedule or ""
		for row in frappe.get_list(
			"Student Leave Application",
			filters={"student": student, "docstatus": 1},
			fields=["course_schedule"],
			limit_page_length=0,
		)
	}
	for absence in absences:
		absence["excused"] = bool(absence["course_schedule"]) and (
			absence["course_schedule"] in excused
		)

	present = sum(entry["present"] for entry in summary.values())
	absent = sum(entry["absent"] for entry in summary.values())
	return {
		"student": student,
		"overall": {
			"present": present,
			"absent": absent,
			"percent": round(present / (present + absent) * 100, 2) if present + absent else None,
		},
		"groups": sorted(summary.values(), key=lambda entry: entry["group"] or ""),
		"absences": absences,
	}


def for_group(student_group: str, date: str | None = None) -> list[dict]:
	"""Attendance of one group on a given date together with the roster."""

	date = date or today()
	records = {
		row.student: row
		for row in frappe.get_list(
			"Student Attendance",
			filters={"student_group": student_group, "date": date, "docstatus": 1},
			fields=["name", "student", "status"],
			limit_page_length=0,
		)
	}

	roster = frappe.get_all(
		"Student Group Student",
		filters={"parent": student_group, "parenttype": "Student Group", "active": 1},
		fields=["student", "student_name", "group_roll_number"],
	)
	students = {row["student"] for row in roster if row.get("student")}
	details = {
		row.name: row
		for row in frappe.get_all(
			"Student",
			filters={"name": ["in", list(students)]},
			fields=["name", "student_name", "album_number", "image"],
		)
	} if students else {}

	result = []
	for row in roster:
		record = records.get(row.get("student"))
		meta = details.get(row.get("student")) or {}
		result.append(
			{
				"student": row.get("student"),
				"student_name": meta.get("student_name") or row.get("student_name"),
				"album_number": meta.get("album_number"),
				"image": meta.get("image"),
				"group_roll_number": row.get("group_roll_number"),
				"status": record.get("status") if record else None,
				"attendance": record.get("name") if record else None,
			}
		)
	return result


def save_for_group(
	student_group: str,
	date: str,
	entries: list[dict],
	course_schedule: str | None = None,
) -> dict:
	"""Upsert attendance of a group for one day (idempotent per student)."""

	saved, skipped = [], []
	for entry in entries or []:
		student = entry.get("student")
		status = entry.get("status")
		if not student or status not in ("Present", "Absent", "Leave"):
			skipped.append({"student": student, "reason": "invalid status"})
			continue

		existing = frappe.get_all(
			"Student Attendance",
			filters={
				"student": student,
				"student_group": student_group,
				"date": getdate(date),
				"docstatus": ["<", 2],
			},
			pluck="name",
			limit=1,
		)
		if existing:
			doc = frappe.get_doc("Student Attendance", existing[0])
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
				doc = frappe.copy_doc(doc)
		else:
			doc = frappe.new_doc("Student Attendance")
			doc.student = student
			doc.student_group = student_group
			doc.date = getdate(date)
			doc.course_schedule = course_schedule

		doc.status = status
		doc.flags.ignore_permissions = True
		doc.insert()
		doc.submit()
		saved.append(doc.name)

	return {"saved": saved, "skipped": skipped, "count": len(saved)}
