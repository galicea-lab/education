# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class StudentAcademicStatus(Document):
	"""Records the per-term academic standing of a student (semester
	accounting), e.g. whether the semester is still open, has been
	closed/passed/failed, whether it is a conditional pass, whether it
	counts towards the student's average, and whether fees for it have
	been paid.

	Ported from the ``edu.state`` / ``edu.state.transition`` models of the
	legacy Odoo "Academy" modules.
	"""

	def validate(self):
		self.set_student_name()
		self.validate_dates()

	def set_student_name(self):
		if not self.student_name:
			self.student_name = frappe.db.get_value("Student", self.student, "student_name")

	def validate_dates(self):
		if self.registration_date and self.decision_date and self.decision_date < self.registration_date:
			frappe.throw(_("Decision Date cannot be before Registration Date."))
