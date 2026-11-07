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
	"""Called by SpiffWorkflow (Service Task) once the process instance for this
	case has started - stores the correlation identifier without unlocking the
	document."""
	doc = frappe.get_doc("Student Correspondence", name)
	doc.check_permission("write")
	doc.db_set("process_instance_id", process_instance_id)


@frappe.whitelist()
def update_status(name: str, status: str, signature_status: str | None = None):
	"""Called by SpiffWorkflow when the process moves to another stage - updates
	the case status (and optionally the signature status) regardless of the
	submit/docstatus state."""
	doc = frappe.get_doc("Student Correspondence", name)
	doc.check_permission("write")
	doc.db_set("status", status)
	if signature_status:
		doc.db_set("signature_status", signature_status)


@frappe.whitelist()
def record_signius_document(name: str, folder_id: str, document_id: str):
	"""Called by wf/routers/signius_api.py after the document was uploaded to
	Signius and a signature was requested - stores the correlation identifiers
	(read_only fields, hence db_set) and switches signature_status to
	"Pending"."""
	doc = frappe.get_doc("Student Correspondence", name)
	doc.check_permission("write")
	doc.db_set("signius_folder_id", folder_id)
	doc.db_set("signius_document_id", document_id)
	doc.db_set("signature_status", "Pending")
	doc.db_set("status", "Awaiting Signature")


# JW 2026-11-06: called by galwork-hub (edu/services/case_sync.py) after the
# dean's office decided on the edu-pismo process - response, date and case
# status (db_set like above, regardless of docstatus).
@frappe.whitelist()
def record_response(name: str, response: str, status: str | None = None):
	doc = frappe.get_doc("Student Correspondence", name)
	doc.check_permission("write")
	doc.db_set("response", response)
	doc.db_set("response_date", frappe.utils.now_datetime())
	if status:
		doc.db_set("status", status)
