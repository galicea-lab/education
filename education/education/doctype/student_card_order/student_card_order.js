// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Student Card Order", {
	refresh(frm) {
		if (["Draft", "Error"].includes(frm.doc.status) && frm.doc.kind !== "Revocation") {
			frm.add_custom_button(__("Send to MLS"), () => {
				frappe.call({
					method: "education.integrations.mlegitymacje.api.submit_card_order",
					args: { name: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				});
			});
		}

		if (["Queued", "Submitted", "Processing", "Ready"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Refresh Status"), () => {
				frappe.call({
					method: "education.integrations.mlegitymacje.api.refresh_card_order",
					args: { name: frm.doc.name },
					freeze: true,
					callback: () => frm.reload_doc(),
				});
			});
		}
	},
});
