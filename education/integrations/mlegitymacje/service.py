"""Student card order orchestration between Frappe and the MLS API.

Responsibilities:

* build the MLS request payload from a ``Student`` record (personal data,
  picture, PESEL) and the order dates,
* send issue/renewal orders, revoke installed cards,
* poll open orders for the ``GetOrderStatus``/``GetQrCodes`` state and store the
  QR code and activation code on the order document,
* notify the Galwork hub about every state change with a signed webhook.

Only this module talks to :class:`~.client.MlsAPI`; the whitelisted endpoints in
``api.py`` and the scheduler entry point in ``tasks.py`` are thin wrappers.
"""

from __future__ import annotations

import json
import uuid

import frappe
from frappe.model.document import Document
from frappe.utils import add_years, getdate, now_datetime, today

from .. import hub
from .client import MlsAPI, MlsError
from .helpers import parse_pesel, photo_from_student
from .mls_model import StudentDto

DOCTYPE = "Student Card Order"

KIND_ISSUE = "Issue"
KIND_RENEWAL = "Renewal"
KIND_REVOCATION = "Revocation"

STATUS_DRAFT = "Draft"
STATUS_QUEUED = "Queued"
STATUS_SUBMITTED = "Submitted"
STATUS_PROCESSING = "Processing"
STATUS_READY = "Ready"
STATUS_REVOKED = "Revoked"
STATUS_ERROR = "Error"

# Orders that the scheduler keeps polling.
PENDING_STATUSES = (STATUS_QUEUED, STATUS_SUBMITTED, STATUS_PROCESSING)

# MLS order status -> order document status.
_MLS_STATUS_MAP = {
	"NEW": STATUS_SUBMITTED,
	"PENDING": STATUS_PROCESSING,
	"PROCESSED": STATUS_PROCESSING,
	"READY": STATUS_READY,
	"ERROR": STATUS_ERROR,
}

# MLS creation statuses that mean "the student was accepted into the order".
_ACCEPTED_CREATION_STATUSES = ("CREATED", "VALIDATED")

EVENT_CREATED = "student_card_order.created"
EVENT_STATUS_CHANGED = "student_card_order.status_changed"


def get_client() -> MlsAPI:
	"""Return a configured MLS API client."""

	return MlsAPI()


# ----------------------------------------------------------------------
# payload
# ----------------------------------------------------------------------
def build_student_dto(
	student: str,
	*,
	student_card_id: str | None = None,
	release_date=None,
	expire_date=None,
) -> StudentDto:
	"""Build the MLS student payload from a ``Student`` record.

	The PESEL is stored on the student (Compliance POL-on tab); the birth date
	falls back to the one encoded in the PESEL when the record has none.
	"""

	# Permission checks belong to the callers (``create_card_order`` and the
	# whitelisted endpoints); this helper is also used from the scheduler.
	student_doc = frappe.get_doc("Student", student)
	decoded = parse_pesel(student_doc.pesel)
	birth_date = student_doc.date_of_birth or decoded["birth_date"]

	missing = [
		label
		for label, value in {
			"First Name": student_doc.first_name,
			"Last Name": student_doc.last_name,
		}.items()
		if not value
	]
	if missing:
		frappe.throw(
			frappe._("Student {0} is missing: {1}").format(frappe.bold(student), ", ".join(missing))
		)

	release_date = getdate(release_date or today())
	expire_date = getdate(expire_date or add_years(release_date, 1))

	return StudentDto(
		pesel=decoded["pesel"],
		firstName=student_doc.first_name,
		secondName=student_doc.middle_name or None,
		surname=student_doc.last_name,
		birthDate=getdate(birth_date),
		picture=photo_from_student(student),
		studentCardId=student_card_id or str(uuid.uuid4()),
		releaseDate=release_date,
		expireDate=expire_date,
	)


def masked_request_payload(dto: StudentDto) -> str:
	"""Return the request payload as JSON with the PESEL and picture masked.

	The payload is stored on the order for auditing, so sensitive data is
	replaced with its length before it is written to the database.
	"""

	payload = json.loads(dto.model_dump_json())
	payload["pesel"] = "*" * len(payload["pesel"])
	picture = payload.get("picture") or ""
	payload["picture"] = f"<base64 jpeg, {len(picture)} bytes>"
	return json.dumps({"students": [payload]}, indent=2)


# ----------------------------------------------------------------------
# order creation
# ----------------------------------------------------------------------
def create_card_order(
	student: str,
	kind: str = KIND_ISSUE,
	*,
	release_date=None,
	expire_date=None,
	student_card_id: str | None = None,
	mls_client: MlsAPI | None = None,
	ignore_permissions: bool = False,
) -> dict:
	"""Create a student card order and send it to MLS.

	``kind`` is one of ``Issue``, ``Renewal`` or ``Revocation``. Revocations need
	the identifier of an existing card: it is taken from ``student_card_id`` or
	from the latest ready order of the student.
	"""

	kind = _validate_kind(kind)
	check_student_permission(student, ignore_permissions)

	if kind == KIND_REVOCATION:
		student_card_id = student_card_id or latest_student_card_id(student)
		if not student_card_id:
			frappe.throw(
				frappe._("Student {0} has no issued student card to revoke").format(frappe.bold(student))
			)

	order = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"student": student,
			"kind": kind,
			"status": STATUS_DRAFT,
			"release_date": getdate(release_date) if release_date else None,
			"expire_date": getdate(expire_date) if expire_date else None,
			"student_card_id": student_card_id,
		}
	)
	order.insert(ignore_permissions=ignore_permissions)

	if kind == KIND_REVOCATION:
		return revoke_card(order, mls_client=mls_client)

	return submit_to_mls(order, mls_client=mls_client)


def submit_to_mls(order, *, mls_client: MlsAPI | None = None) -> dict:
	"""Send a draft order to MLS and store the answer on the document."""

	if not isinstance(order, Document):
		order = frappe.get_doc(DOCTYPE, order)

	if order.status not in (STATUS_DRAFT, STATUS_ERROR) or order.kind == KIND_REVOCATION:
		frappe.throw(
			frappe._("Order {0} cannot be sent to MLS (status {1}, type {2})").format(
				frappe.bold(order.name), order.status, order.kind
			)
		)

	client = mls_client or get_client()
	client.authenticate()

	dto = build_student_dto(
		order.student,
		student_card_id=order.student_card_id or str(uuid.uuid4()),
		release_date=order.release_date,
		expire_date=order.expire_date,
	)

	order.db_set("request_payload", masked_request_payload(dto))
	order.db_set("student_card_id", dto.studentCardId)

	try:
		response = client.create_order([dto])
	except MlsError as exc:
		return _fail(order, str(exc))

	order.db_set("mls_order_id", str(response.orderId) if response.orderId else None)
	order.db_set("response_payload", response.model_dump_json(indent=2))

	student_result = response.students[0] if response.students else None
	creation_status = getattr(getattr(student_result, "creationStatus", None), "value", None)

	if creation_status not in _ACCEPTED_CREATION_STATUSES:
		return _fail(order, creation_status or frappe._("MLS did not accept the student"))

	order.db_set("status", STATUS_QUEUED)
	order.db_set("mls_status", creation_status)
	order.db_set("error", None)
	order.db_set("last_synced_on", now_datetime())

	_notify(order, EVENT_CREATED)
	return get_card_order(order.name)


def revoke_card(order, *, mls_client: MlsAPI | None = None) -> dict:
	"""Revoke an already installed card for the given order document."""

	client = mls_client or get_client()
	client.authenticate()

	try:
		response = client.revoke_student_card(order.student_card_id)
	except MlsError as exc:
		return _fail(order, str(exc))

	order.db_set("status", STATUS_REVOKED)
	order.db_set("response_payload", response or "")
	order.db_set("error", None)
	order.db_set("last_synced_on", now_datetime())

	_notify(order, EVENT_STATUS_CHANGED)
	return get_card_order(order.name)


def latest_student_card_id(student: str) -> str | None:
	"""Return the card identifier of the last ready order of a student."""

	return frappe.db.get_value(
		DOCTYPE,
		{"student": student, "kind": ["in", (KIND_ISSUE, KIND_RENEWAL)], "student_card_id": ["is", "set"]},
		"student_card_id",
		order_by="creation desc",
	)


# ----------------------------------------------------------------------
# polling
# ----------------------------------------------------------------------
def refresh_card_order(name: str, *, mls_client: MlsAPI | None = None) -> dict:
	"""Synchronise one order with MLS (status, QR codes, card download state)."""

	order = frappe.get_doc(DOCTYPE, name)
	if not order.mls_order_id or order.kind == KIND_REVOCATION:
		return get_card_order(name)

	client = mls_client or get_client()
	client.authenticate()

	previous_status = order.status
	status_response = client.get_order_status(order.mls_order_id)
	mls_status = status_response.status.value

	changes = {"mls_status": mls_status, "last_synced_on": now_datetime()}
	new_status = _MLS_STATUS_MAP.get(mls_status, previous_status)

	if mls_status == "READY":
		payload = _fetch_qr_codes(client, order)
		if payload:
			changes.update(payload)
		else:
			# QR codes are not available yet - retry on the next run.
			new_status = STATUS_PROCESSING

	download_status = (
		_fetch_download_status(client, order) if mls_status in ("READY", "PROCESSED") else None
	)
	if download_status:
		changes["card_download_status"] = download_status

	changes["status"] = new_status
	if new_status == STATUS_ERROR:
		changes["error"] = frappe._("MLS reported an error while processing order {0}").format(
			order.mls_order_id
		)

	# ``last_synced_on`` changes on every run, so it must not decide whether the
	# document is written back; it is stored on its own when nothing else moved.
	content = {field: value for field, value in changes.items() if field != "last_synced_on"}
	if any(getattr(order, field, None) != value for field, value in content.items()):
		order.update(changes)
		order.save(ignore_permissions=True)
	else:
		order.db_set("last_synced_on", changes["last_synced_on"])

	if new_status != previous_status:
		_notify(order, EVENT_STATUS_CHANGED)

	return get_card_order(name)


def _fetch_qr_codes(client: MlsAPI, order) -> dict | None:
	"""Return QR code fields for the order's card, or ``None`` when not ready."""

	try:
		response = client.get_qr_codes(order.mls_order_id)
	except MlsError as exc:
		frappe.log_error(
			title="MLS QR code fetch failed",
			message=str(exc),
			reference_doctype=DOCTYPE,
			reference_name=order.name,
		)
		return None

	for entry in response.qrCodes:
		if entry.studentCardId == order.student_card_id:
			return {
				"qr_code": entry.qrCode,
				"activation_code": entry.password,
				"valid_to": response.validTo,
				"status": STATUS_READY,
			}

	frappe.log_error(
		title="MLS QR code missing for card",
		message=f"Card {order.student_card_id} not present in the MLS response for order {order.mls_order_id}",
		reference_doctype=DOCTYPE,
		reference_name=order.name,
	)
	return None


def _fetch_download_status(client: MlsAPI, order) -> str | None:
	"""Return whether the student downloaded the card (informational only)."""

	if not order.student_card_id:
		return None
	try:
		response = client.get_student_status(order.mls_order_id, order.student_card_id)
	except MlsError as exc:
		frappe.log_error(
			title="MLS student status fetch failed",
			message=str(exc),
			reference_doctype=DOCTYPE,
			reference_name=order.name,
		)
		return None
	return response.status.value


def poll_pending_orders(limit: int = 100) -> dict:
	"""Scheduler entry point: refresh every order still waiting in MLS."""

	names = frappe.get_all(
		DOCTYPE,
		filters={
			"status": ["in", PENDING_STATUSES],
			"mls_order_id": ["is", "set"],
			"kind": ["!=", KIND_REVOCATION],
		},
		pluck="name",
		limit=limit,
		order_by="modified asc",
	)

	summary = {"polled": 0, "failed": 0}
	for name in names:
		try:
			refresh_card_order(name)
			summary["polled"] += 1
		except Exception:
			summary["failed"] += 1
			frappe.log_error(
				title="Student card order refresh failed",
				message=frappe.get_traceback(),
				reference_doctype=DOCTYPE,
				reference_name=name,
			)

	return summary


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def get_card_order(name: str) -> dict:
	"""Return the order data the Desk form and the hub exchange."""

	order = frappe.get_doc(DOCTYPE, name)
	order.check_permission("read")

	return {
		"name": order.name,
		"student": order.student,
		"student_name": order.get("student_name"),
		"album_number": order.get("album_number"),
		"kind": order.kind,
		"status": order.status,
		"mls_status": order.mls_status,
		"mls_order_id": order.mls_order_id,
		"student_card_id": order.student_card_id,
		"card_download_status": order.card_download_status,
		"release_date": str(order.release_date) if order.release_date else None,
		"expire_date": str(order.expire_date) if order.expire_date else None,
		"valid_to": str(order.valid_to) if order.valid_to else None,
		"qr_code": order.qr_code,
		"activation_code": order.activation_code,
		"error": order.error,
		"last_synced_on": str(order.last_synced_on) if order.last_synced_on else None,
	}


def check_student_permission(student: str, ignore_permissions: bool = False) -> None:
	"""Ensure the caller may order a card for the student."""

	if ignore_permissions or frappe.flags.in_test:
		return
	if not frappe.has_permission("Student", "write", student):
		frappe.throw(
			frappe._("Not permitted to order a student card for {0}").format(frappe.bold(student)),
			frappe.PermissionError,
		)


def order_payload(order) -> dict:
	"""Flat representation of an order used in hub notifications."""

	return {
		"name": order.name,
		"student": order.student,
		"album_number": order.get("album_number"),
		"kind": order.kind,
		"status": order.status,
		"mls_status": order.mls_status,
		"mls_order_id": order.mls_order_id,
		"student_card_id": order.student_card_id,
		"card_download_status": order.card_download_status,
		"release_date": str(order.release_date) if order.release_date else None,
		"expire_date": str(order.expire_date) if order.expire_date else None,
		"valid_to": str(order.valid_to) if order.valid_to else None,
		"error": order.error,
	}


def _notify(order, event: str) -> None:
	"""Send a signed event to the hub and remember when it happened."""

	if hub.notify(event, order_payload(order), reference_doctype=DOCTYPE, reference_name=order.name):
		order.db_set("hub_notified_on", now_datetime())


def _fail(order, message: str) -> dict:
	"""Store a failure on the order and return the refreshed document."""

	order.db_set("status", STATUS_ERROR)
	order.db_set("error", (message or "")[:1000])
	order.db_set("last_synced_on", now_datetime())
	_notify(order, EVENT_STATUS_CHANGED)
	return get_card_order(order.name)


def _validate_kind(kind: str) -> str:
	kind = (kind or KIND_ISSUE).strip().title()
	if kind not in (KIND_ISSUE, KIND_RENEWAL, KIND_REVOCATION):
		frappe.throw(
			frappe._("Unknown order type {0}; expected Issue, Renewal or Revocation").format(
				frappe.bold(kind)
			)
		)
	return kind
