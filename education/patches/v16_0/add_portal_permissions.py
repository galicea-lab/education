"""Portal permissions: lecturer access to timetable and results, service role.

Two things the portal needs and the app did not ship:

* ``Instructor`` had no permission at all on ``Course Schedule`` (the lecturer
  calendar came back empty) and on ``Assessment Result`` (a lecturer could not
  enter a grade). Granting it in the doctype JSON would collide with upstream on
  every merge, so it is done with ``Custom DocPerm`` records; the row limit
  ("only the groups I teach") comes from
  ``education.portal.permissions`` - it applies to these doctypes as soon as the
  role has the read permission,
* the ``Portal Service`` role lets a service account call the portal API for a
  named student without being ``Administrator``.

The patch is idempotent: re-running it does not duplicate rows.
"""

import frappe

PERMISSIONS = {
	"Course Schedule": [
		{"role": "Instructor", "read": 1, "report": 1, "export": 1},
	],
	"Assessment Result": [
		{"role": "Instructor", "read": 1, "write": 1, "create": 1, "submit": 1, "report": 1},
	],
	"Student Group": [
		{"role": "Instructor", "read": 1, "report": 1, "export": 1},
	],
}


def execute():
	create_portal_service_role()
	create_custom_permissions()


def create_portal_service_role():
	if not frappe.db.exists("Role", "Portal Service"):
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": "Portal Service",
				"desk_access": 0,
			}
		).insert(ignore_permissions=True)


def create_custom_permissions():
	for doctype, rows in PERMISSIONS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		for row in rows:
			role = row["role"]
			existing = frappe.db.exists(
				"Custom DocPerm",
				{"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
			)
			if existing:
				continue
			docperm = frappe.new_doc("Custom DocPerm")
			docperm.update({"parent": doctype, **row})
			docperm.insert(ignore_permissions=True)
