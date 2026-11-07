"""Scheduler entry points of the mLegitymacja connector.

Registered in ``hooks.py`` under ``scheduler_events`` -> ``cron`` (every ten
minutes). A full sweep of open orders keeps the status, the QR code and the
activation code in step with MLS without the hub polling Frappe.
"""

from __future__ import annotations

import frappe

from . import service


def poll_pending_orders(limit: int = 100) -> dict:
	"""Refresh every student card order that is still being processed by MLS."""

	summary = service.poll_pending_orders(limit=limit)

	if summary.get("failed"):
		frappe.logger("integrations.mlegitymacje").warning(
			f"MLS order poll finished with {summary['failed']} failures (polled: {summary['polled']})"
		)
		# Keep an audit trail; refresh_card_order already logs per-order details.
		frappe.log_error(
			title="MLS order poll had failures",
			message=f"polled={summary['polled']} failed={summary['failed']}",
		)

	return summary
