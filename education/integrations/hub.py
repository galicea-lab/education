"""Outgoing notifications to the Galwork hub.

The hub is the collector and router of academic workflows: it owns the BPMN
processes and exchanges only orders/state with Frappe. Every state change that
the hub must react to is sent as a signed JSON POST, so the hub can verify who
sent it and can reject replayed or forged calls.

Request body (compact JSON, sorted keys)::

	{
		"event": "student_card_order.status_changed",
		"sent_at": "2026-11-07T06:30:00+00:00",
		"data": {"name": "EDU-SCO-2026-00001", "status": "Ready", "..." : "..."}
	}

Headers::

	Content-Type: application/json
	X-Galwork-Event: student_card_order.status_changed
	X-Galwork-Signature: sha256=<hex HMAC-SHA256 of the raw body bytes>

Configuration (``site_config.json``)::

	"hub": {
		"url": "https://hub.example.org/api/edu/events",
		"hmac_secret": "shared-secret",
		"timeout": 10
	}

Missing configuration or an unreachable hub never breaks the caller: the
failure is written to ``Error Log`` and the document keeps an unsent state that
the next scheduler run or manual retry can pick up.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import frappe
from frappe.utils import get_request_session

from .config import IntegrationNotConfigured, hub_config

SIGNATURE_HEADER = "X-Galwork-Signature"
EVENT_HEADER = "X-Galwork-Event"
DEFAULT_TIMEOUT = 10


def build_body(event: str, data: dict, *, sent_at: str | None = None) -> str:
	"""Return the canonical JSON body that is signed and sent to the hub."""

	return json.dumps(
		{
			"event": event,
			"sent_at": sent_at or frappe.utils.now_datetime().isoformat(),
			"data": data,
		},
		separators=(",", ":"),
		sort_keys=True,
		default=str,
	)


def sign(body: str | bytes, secret: str) -> str:
	"""Return the ``sha256=<hex>`` HMAC signature of ``body``."""

	payload = body.encode("utf-8") if isinstance(body, str) else body
	digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
	return f"sha256={digest}"


def verify_signature(body: str | bytes, signature: str, secret: str) -> bool:
	"""Constant-time check of an incoming signature (counterpart of :func:`sign`).

	The hub uses the same rule on its side; the helper is here so the contract
	is testable from the Frappe app as well.
	"""

	if not signature or not secret:
		return False
	expected = sign(body, secret)
	return hmac.compare_digest(expected, signature.strip())


def notify(
	event: str,
	data: dict,
	*,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	raise_exceptions: bool = False,
) -> bool:
	"""POST a signed event to the hub.

	Returns ``True`` when the hub accepted the event. Unless ``raise_exceptions``
	is set, transport and configuration problems are logged instead of raised so
	a booking/issuing transaction is never rolled back because the hub is down.
	"""

	settings = hub_config()
	if not settings.is_configured("url", "hmac_secret"):
		_log_failure(
			event,
			"HUB NOT CONFIGURED: set hub.url and hub.hmac_secret in site_config.json",
			reference_doctype,
			reference_name,
		)
		if raise_exceptions:
			frappe.throw(
				frappe._("Hub notification settings are missing (hub.url, hub.hmac_secret)"),
				exc=IntegrationNotConfigured,
			)
		return False

	body = build_body(event, data)
	headers = {
		"Content-Type": "application/json",
		"Accept": "application/json",
		EVENT_HEADER: event,
		SIGNATURE_HEADER: sign(body, settings.get("hmac_secret")),
	}

	try:
		session = get_request_session()
		response = session.post(
			settings.get("url"),
			data=body,
			headers=headers,
			timeout=settings.get("timeout", DEFAULT_TIMEOUT),
		)
		response.raise_for_status()
	except Exception:
		_log_failure(event, frappe.get_traceback(), reference_doctype, reference_name)
		if raise_exceptions:
			raise
		return False

	return True


def _log_failure(
	event: str,
	message: str,
	reference_doctype: str | None,
	reference_name: str | None,
) -> None:
	frappe.log_error(
		title=f"Hub notification failed: {event}"[:140],
		message=message,
		reference_doctype=reference_doctype,
		reference_name=reference_name,
	)
