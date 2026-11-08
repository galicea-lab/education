# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Principal resolution and access checks for the portal API.

Two caller kinds:

* **end user** - a student or a lecturer signed in to Frappe. The identity comes
  from the session (``Student.user``, ``Instructor`` linked to the user) and the
  caller may not request somebody else's data.
* **trusted service** - ``Administrator``, a user listed in the
  ``portal.service_users`` setting of ``site_config.json`` or a user with the
  ``Portal Service`` role. Such a caller must name the principal explicitly
  (``student``/``instructor``); it is responsible for authenticating the end user
  itself (galwork-hub with Keycloak).

Configuration (``site_config.json``)::

	"portal": {
		"service_users": ["hub@example.org"],
		"passing_percentage": 50,
		"reveal_codes_requires_password": true
	}
"""

from __future__ import annotations

import frappe
from frappe import _

SERVICE_ROLE = "Portal Service"
STAFF_ROLES = frozenset(
	{"Academics User", "Education Manager", "System Manager", "Portal Service"}
)
CONFIG_SECTION = "portal"
DEFAULT_PASSING_PERCENTAGE = 50.0


class PortalAccessError(frappe.PermissionError):
	"""Raised when the caller may not act on the requested principal."""


def portal_config() -> dict:
	"""Return the ``portal`` section of ``site_config.json`` (never raises)."""

	section = (frappe.conf or {}).get(CONFIG_SECTION)
	return section if isinstance(section, dict) else {}


def service_users() -> set[str]:
	"""Lower-cased names of users allowed to act as a trusted service."""

	configured = portal_config().get("service_users") or []
	if isinstance(configured, str):
		configured = [name.strip() for name in configured.split(",") if name.strip()]
	return {str(name).strip().lower() for name in configured} | {"administrator"}


def is_service_user(user: str | None = None) -> bool:
	"""Return True when ``user`` (default: session user) is a trusted service."""

	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if user.lower() in service_users():
		return True
	return SERVICE_ROLE in frappe.get_roles(user)


def is_staff(user: str | None = None) -> bool:
	"""Return True for dean's office roles that may read every student."""

	user = user or frappe.session.user
	return bool(STAFF_ROLES & set(frappe.get_roles(user)))


def student_for_user(user: str | None = None) -> str | None:
	"""Return the ``Student`` record linked to the user, if any."""

	user = user or frappe.session.user
	if not user or user in ("Guest", "Administrator"):
		return None
	return frappe.db.get_value("Student", {"user": user}, "name")


def resolve_student_ref(ref: str | None) -> str | None:
	"""Map a student reference to the ``Student`` docname.

	The hub knows the album number (and the e-mail from Keycloak), not the
	Frappe docname, so all three are accepted: docname, ``album_number`` and
	``student_email_id``.
	"""

	ref = (ref or "").strip()
	if not ref:
		return None
	if frappe.db.exists("Student", ref):
		return ref

	name = frappe.db.get_value("Student", {"album_number": ref}, "name")
	if name:
		return name
	return frappe.db.get_value("Student", {"student_email_id": ref}, "name")


def resolve_instructor_ref(ref: str | None) -> str | None:
	"""Map a lecturer reference to the ``Instructor`` docname.

	Accepted: docname, the login e-mail (``Instructor.user_id`` when the field
	exists, otherwise ``Employee.user_id``/``company_email``/``personal_email``
	through ``Instructor.employee``) and the employee id.
	"""

	ref = (ref or "").strip()
	if not ref:
		return None
	if frappe.db.exists("Instructor", ref):
		return ref

	if frappe.get_meta("Instructor").has_field("user_id"):
		name = frappe.db.get_value("Instructor", {"user_id": ref}, "name")
		if name:
			return name

	for field in ("user_id", "company_email", "personal_email"):
		employee = frappe.db.get_value("Employee", {field: ref}, "name")
		if employee:
			name = frappe.db.get_value("Instructor", {"employee": employee}, "name")
			if name:
				return name

	return frappe.db.get_value("Instructor", {"employee": ref}, "name")


def instructor_for_user(user: str | None = None) -> str | None:
	"""Return the ``Instructor`` record of the user.

	``Instructor`` has no ``user`` link in the app, so the mapping goes through
	``Instructor.user_id`` when that field exists (added for this portal) and
	otherwise through ``Instructor.employee`` -> ``Employee.user_id``.
	"""

	user = user or frappe.session.user
	if not user or user in ("Guest", "Administrator"):
		return None
	return resolve_instructor_ref(user)


def require_student(student: str | None = None) -> str:
	"""Return the student the call is about, or raise :class:`PortalAccessError`.

	An end user always gets their own record; a trusted service must pass
	``student``; staff may pass anybody.
	"""

	student = (student or "").strip() or None

	if is_service_user() or is_staff():
		if not student:
			raise PortalAccessError(_("Parameter 'student' is required for this account"))
		resolved = resolve_student_ref(student)
		if not resolved:
			raise frappe.DoesNotExistError(_("Student {0} does not exist").format(student))
		return resolved

	own_student = student_for_user()
	if not own_student:
		raise PortalAccessError(_("The signed-in user is not a student"))
	if student and resolve_student_ref(student) != own_student:
		raise PortalAccessError(_("Not permitted to access another student's data"))
	return own_student


def require_instructor(instructor: str | None = None) -> str:
	"""Return the instructor the call is about, or raise :class:`PortalAccessError`."""

	instructor = (instructor or "").strip() or None

	if is_service_user() or is_staff():
		if not instructor:
			raise PortalAccessError(_("Parameter 'instructor' is required for this account"))
		resolved = resolve_instructor_ref(instructor)
		if not resolved:
			raise frappe.DoesNotExistError(_("Instructor {0} does not exist").format(instructor))
		return resolved

	own_instructor = instructor_for_user()
	if not own_instructor:
		raise PortalAccessError(
			_("The signed-in user is not linked to an Instructor record")
		)
	if instructor and resolve_instructor_ref(instructor) != own_instructor:
		raise PortalAccessError(_("Not permitted to access another lecturer's data"))
	return own_instructor


def student_group_names(student: str) -> list[str]:
	"""Return names of the student groups the student is an active member of."""

	return frappe.db.get_all(
		"Student Group Student",
		filters={"student": student, "parenttype": "Student Group", "active": 1},
		pluck="parent",
	)


def group_names_of_instructor(instructor: str, academic_term: str | None = None) -> list[str]:
	"""Return names of the student groups taught by the instructor."""

	groups = frappe.db.get_all(
		"Student Group Instructor",
		filters={"instructor": instructor, "parenttype": "Student Group"},
		pluck="parent",
	)
	if not groups:
		return []

	filters = {"name": ["in", groups], "disabled": 0}
	if academic_term:
		filters["academic_term"] = academic_term
	return frappe.get_all("Student Group", filters=filters, pluck="name")


def passing_percentage() -> float:
	"""Pass threshold used when a grading scale has no explicit pass rule."""

	value = portal_config().get("passing_percentage")
	try:
		return float(value) if value is not None else DEFAULT_PASSING_PERCENTAGE
	except (TypeError, ValueError):
		return DEFAULT_PASSING_PERCENTAGE


def reveal_codes_requires_password() -> bool:
	"""Whether revealing card codes needs the account password (default: yes)."""

	value = portal_config().get("reveal_codes_requires_password")
	return True if value is None else bool(value)
