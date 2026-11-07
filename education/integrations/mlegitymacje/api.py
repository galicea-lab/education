"""Whitelisted mLegitymacja endpoints.

These are the only entry points the Galwork hub (and the Desk form) use. The
hub exchanges orders and their state only - personal data, pictures and card
codes never leave Frappe through these endpoints.

``create_card_order`` and ``get_card_order`` are the two methods required by
the integration contract; the remaining ones let the dean's office retry a
submission or refresh a single order without waiting for the scheduler.
"""

from __future__ import annotations

import frappe

from . import service


@frappe.whitelist()
def create_card_order(
	student: str,
	kind: str = service.KIND_ISSUE,
	release_date: str | None = None,
	expire_date: str | None = None,
	student_card_id: str | None = None,
) -> dict:
	"""Order a student card for ``student`` (Issue, Renewal or Revocation)."""

	return service.create_card_order(
		student,
		kind,
		release_date=release_date,
		expire_date=expire_date,
		student_card_id=student_card_id,
	)


@frappe.whitelist()
def get_card_order(name: str) -> dict:
	"""Return the current state of a student card order."""

	return service.get_card_order(name)


@frappe.whitelist()
def submit_card_order(name: str) -> dict:
	"""(Re)send a draft or failed order to MLS."""

	order = frappe.get_doc(service.DOCTYPE, name)
	order.check_permission("write")
	return service.submit_to_mls(order)


@frappe.whitelist()
def refresh_card_order(name: str) -> dict:
	"""Synchronise a single order with MLS."""

	order = frappe.get_doc(service.DOCTYPE, name)
	order.check_permission("write")
	return service.refresh_card_order(name)


@frappe.whitelist()
def revoke_student_card(name: str) -> dict:
	"""Revoke the card of an already issued order.

	Creates a separate revocation order so the original issuance stays in the
	audit trail.
	"""

	order = frappe.get_doc(service.DOCTYPE, name)
	order.check_permission("read")
	if not order.student_card_id:
		frappe.throw(frappe._("Order {0} has no student card to revoke").format(frappe.bold(name)))

	return service.create_card_order(
		order.student,
		service.KIND_REVOCATION,
		student_card_id=order.student_card_id,
	)


@frappe.whitelist()
def list_card_orders(student: str) -> list[dict]:
	"""Return the card orders of a student, newest first."""

	if not frappe.has_permission("Student", "read", student):
		frappe.throw(frappe._("Not permitted to read student {0}").format(frappe.bold(student)), frappe.PermissionError)

	return frappe.get_all(
		service.DOCTYPE,
		filters={"student": student},
		fields=[
			"name",
			"kind",
			"status",
			"mls_status",
			"mls_order_id",
			"student_card_id",
			"release_date",
			"expire_date",
			"valid_to",
			"error",
			"creation",
		],
		order_by="creation desc",
	)
