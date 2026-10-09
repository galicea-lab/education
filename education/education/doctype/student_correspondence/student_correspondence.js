// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Student Correspondence", {
	setup(frm) {
		frm.set_query("correspondence_type", () => ({
			filters: { is_active: 1 },
		}));
	},
});
