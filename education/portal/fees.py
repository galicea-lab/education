# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Tuition and fee overview for the portal."""

from __future__ import annotations

import frappe
from frappe.utils import flt, getdate, today

FEE_FIELDS = [
	"name",
	"posting_date",
	"due_date",
	"grand_total",
	"outstanding_amount",
	"currency",
	"program",
	"academic_year",
	"academic_term",
	"docstatus",
]


def _state(outstanding: float, due_date) -> str:
	if outstanding <= 0:
		return "paid"
	if due_date and getdate(due_date) < getdate(today()):
		return "overdue"
	return "due"


def for_student(student: str) -> dict:
	"""Fees with their settlement state and the amounts still to pay."""

	rows = frappe.get_list(
		"Fees",
		filters={"student": student, "docstatus": 1},
		fields=FEE_FIELDS,
		order_by="due_date asc",
		limit_page_length=0,
	)

	fees = []
	for row in rows:
		outstanding = flt(row.get("outstanding_amount"))
		fees.append(
			{
				"name": row.get("name"),
				"posting_date": str(row.get("posting_date") or ""),
				"due_date": str(row.get("due_date") or ""),
				"currency": row.get("currency"),
				"total": flt(row.get("grand_total")),
				"outstanding": outstanding,
				"state": _state(outstanding, row.get("due_date")),
				"program": row.get("program"),
				"academic_year": row.get("academic_year"),
				"academic_term": row.get("academic_term"),
			}
		)

	outstanding = round(sum(fee["outstanding"] for fee in fees), 2)
	return {
		"student": student,
		"fees": fees,
		"totals": {
			"outstanding": outstanding,
			"paid": round(sum(flt(row.get("grand_total")) for row in rows) - outstanding, 2),
			"overdue": round(
				sum(fee["outstanding"] for fee in fees if fee["state"] == "overdue"), 2
			),
		},
		"payment_required": outstanding > 0,
	}
