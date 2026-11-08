# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Portal API of the education app.

A stable, session- or service-scoped interface for external front ends
(the Galwork "Wirtualna Uczelnia" site served by galwork-hub): schedule,
grades, attendance, fees, dean's office correspondence and mLegitymacja card
self-service for students, plus timetable, rosters, attendance and grades for
lecturers.

Two caller kinds are supported, see :mod:`education.portal.auth`:

* an end user signed in to Frappe (a student or a lecturer) - the identity is
  taken from the session and the caller cannot ask for somebody else's data,
* a trusted service account (site_config ``portal.service_users``, the
  ``Portal Service`` role, or ``Administrator``) - the caller must pass
  ``student``/``instructor`` explicitly; that account authenticated the end
  user itself (galwork-hub with Keycloak) and Frappe cannot see it.

Every endpoint derives its filters from the resolved principal and reads with
:func:`frappe.get_list`, so the row-level conditions registered in
:mod:`education.portal.permissions` apply as a second line of defence.
"""
