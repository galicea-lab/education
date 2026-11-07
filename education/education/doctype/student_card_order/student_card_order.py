# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import add_years, getdate, today


class StudentCardOrder(Document):
	"""A request for issuing, renewing or revoking an mLegitymacja student card.

	The document only keeps the state of the order; the API calls themselves are
	made by ``education.integrations.mlegitymacje.service`` (both from the
	whitelisted endpoints and from the scheduler).
	"""

	def validate(self):
		self.set_student_details()
		self.set_dates()

	def set_student_details(self):
		if self.student and not self.student_name:
			self.student_name = frappe.db.get_value("Student", self.student, "student_name")

	def set_dates(self):
		if self.kind == "Revocation":
			return

		if not self.release_date:
			self.release_date = today()
		if not self.expire_date:
			# Default validity of one year; the dean's office can set the real
			# expiry date before the order is sent to MLS.
			self.expire_date = add_years(getdate(self.release_date), 1)

		if getdate(self.expire_date) <= getdate(self.release_date):
			frappe.throw(frappe._("The card expiry date must be later than the release date"))
