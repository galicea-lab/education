# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class StudentCorrespondence(Document):
	def validate(self):
		self.set_student_name()

	def set_student_name(self):
		if self.student and not self.student_name:
			self.student_name = frappe.db.get_value("Student", self.student, "student_name")

	def on_submit(self):
		if self.status == "Draft":
			self.status = "Submitted"

	def on_cancel(self):
		self.status = "Cancelled"


@frappe.whitelist()
def set_process_instance(name: str, process_instance_id: str):
	"""Wołane przez SpiffWorkflow (Service Task) po wystartowaniu instancji procesu
	dla tej sprawy - zapisuje identyfikator korelacyjny bez odblokowywania dokumentu."""
	doc = frappe.get_doc("Student Correspondence", name)
	doc.check_permission("write")
	doc.db_set("process_instance_id", process_instance_id)


@frappe.whitelist()
def update_status(name: str, status: str, signature_status: str | None = None):
	"""Wołane przez SpiffWorkflow przy zmianie etapu procesu - aktualizuje status
	sprawy (i opcjonalnie status podpisu) niezależnie od stanu submit/docstatus."""
	doc = frappe.get_doc("Student Correspondence", name)
	doc.check_permission("write")
	doc.db_set("status", status)
	if signature_status:
		doc.db_set("signature_status", signature_status)


@frappe.whitelist()
def record_signius_document(name: str, folder_id: str, document_id: str):
	"""Wołane przez wf/routers/signius_api.py po wgraniu dokumentu do Signius
	i zażądaniu podpisu - zapisuje identyfikatory korelacyjne (pola read_only,
	stąd db_set) i przestawia signature_status na "Pending"."""
	doc = frappe.get_doc("Student Correspondence", name)
	doc.check_permission("write")
	doc.db_set("signius_folder_id", folder_id)
	doc.db_set("signius_document_id", document_id)
	doc.db_set("signature_status", "Pending")
	doc.db_set("status", "Awaiting Signature")
