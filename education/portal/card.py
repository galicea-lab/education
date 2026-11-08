# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""mLegitymacja self-service for the portal.

The order itself lives in :mod:`education.integrations.mlegitymacje` (MLS client,
scheduler, hub notifications); this module only adds the student-facing layer:
current card state, ordering, a one-time reveal of the QR/activation codes and
a revocation request.

Sensitive data handling:

* the codes are returned only from :func:`reveal_codes`, always with an audit
  comment on the order and a log line,
* a session user must confirm with the account password when
  ``portal.reveal_codes_requires_password`` is on (default),
* the state endpoint never returns the codes, only whether they exist.
"""

from __future__ import annotations

import frappe
from frappe import _

from education.portal import auth

DOCTYPE = "Student Card Order"
ACTIVE_STATUSES = ("Draft", "Queued", "Submitted", "Processing")
KINDS = ("Issue", "Renewal", "Revocation")


def _latest_order(student: str) -> dict | None:
	rows = frappe.get_list(
		DOCTYPE,
		filters={"student": student},
		fields=[
			"name",
			"student",
			"kind",
			"status",
			"mls_status",
			"mls_order_id",
			"student_card_id",
			"release_date",
			"expire_date",
			"valid_to",
			"error",
			"last_synced_on",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=1,
	)
	return rows[0] if rows else None


def state_for_student(student: str) -> dict:
	"""Current card state without the secret codes."""

	order = _latest_order(student)
	if not order:
		return {"kind": "none", "codes_available": False}

	codes = frappe.db.get_value(DOCTYPE, order["name"], ["qr_code", "activation_code"], as_dict=True)
	return {
		"kind": "ordered" if order["status"] in ACTIVE_STATUSES else "issued",
		"order": order["name"],
		"order_kind": order["kind"],
		"status": order["status"],
		"mls_status": order.get("mls_status"),
		"mls_order_id": order.get("mls_order_id"),
		"student_card_id": order.get("student_card_id"),
		"release_date": str(order.get("release_date") or "") or None,
		"expire_date": str(order.get("expire_date") or "") or None,
		"valid_to": str(order.get("valid_to") or "") or None,
		"error": order.get("error"),
		"ordered_on": str(order.get("creation") or ""),
		"codes_available": bool(codes and codes.get("qr_code")),
	}


def order_card(
	student: str,
	kind: str = "Issue",
	release_date: str | None = None,
	expire_date: str | None = None,
) -> dict:
	"""Order a card (or its renewal) for the student."""

	kind = (kind or "Issue").strip().title()
	if kind not in ("Issue", "Renewal"):
		frappe.throw(_("Unsupported order type {0}; expected Issue or Renewal").format(kind))

	from education.integrations.mlegitymacje import service

	order = service.create_card_order(
		student,
		kind,
		release_date=release_date,
		expire_date=expire_date,
		ignore_permissions=True,
	)
	order.pop("qr_code", None)
	order.pop("activation_code", None)
	return order


def reveal_codes(student: str, password: str | None = None) -> dict:
	"""Return the QR and activation codes of the ready card, once per request."""

	if auth.reveal_codes_requires_password() and not auth.is_service_user():
		if not password:
			frappe.throw(_("Password confirmation is required to reveal the card codes"))
		from frappe.utils.password import check_password

		try:
			check_password(frappe.session.user, password)
		except frappe.AuthenticationError:
			frappe.throw(_("Invalid password"), frappe.AuthenticationError)

	order = _latest_order(student)
	if not order:
		frappe.throw(_("Student {0} has no card order").format(student))

	values = frappe.db.get_value(
		DOCTYPE, order["name"], ["qr_code", "activation_code", "valid_to", "status"], as_dict=True
	)
	if not values or not values.get("qr_code"):
		frappe.throw(_("The card is not ready yet - the QR code is not available"))

	doc = frappe.get_doc(DOCTYPE, order["name"])
	doc.flags.ignore_permissions = True
	doc.add_comment(
		"Info",
		_("Activation codes revealed in the portal by {0}").format(frappe.session.user),
	)
	frappe.logger("education.portal").info(
		"card codes revealed: order=%s student=%s user=%s",
		doc.name,
		student,
		frappe.session.user,
	)

	return {
		"order": doc.name,
		"qr_code": values.get("qr_code"),
		"activation_code": values.get("activation_code"),
		"valid_to": str(values.get("valid_to") or "") or None,
		"warning": _("The activation code is single use - do not share it"),
	}


def request_revocation(student: str, reason: str | None = None) -> dict:
	"""Ask the dean's office for the card to be revoked."""

	card = state_for_student(student)
	if not card.get("student_card_id"):
		frappe.throw(_("Student {0} has no issued card to revoke").format(student))

	from education.integrations.mlegitymacje import service

	order = service.create_card_order(
		student,
		"Revocation",
		student_card_id=card["student_card_id"],
		ignore_permissions=True,
	)
	order.pop("qr_code", None)
	order.pop("activation_code", None)

	if reason:
		frappe.get_doc(DOCTYPE, order["name"]).add_comment("Info", reason)

	return order
