# Copyright (c) 2026, Fundacja Galicea and contributors
# For license information, please see license.txt

"""Student album numbers: the next number of one sequence, zero-padded to five digits ("00001").

The sequence is kept in ``tabSeries`` (locked while a number is taken, so
concurrent matriculations do not get the same number) and never goes below the
highest numeric album number already given, so numbers entered by hand are
respected. A number used by another student is skipped.
"""

import frappe
from frappe import _
from frappe.model.naming import getseries

SERIES_KEY = "Student Album Number"
DIGITS = 5


def highest_album_number() -> int:
	result = frappe.db.sql(
		"""select max(cast(album_number as unsigned)) from `tabStudent`
		where album_number regexp '^[0-9]+$'"""
	)
	return int(result[0][0] or 0)


def next_album_number() -> str:
	frappe.db.sql(
		"""insert into `tabSeries` (`name`, `current`) values (%(key)s, %(highest)s)
		on duplicate key update `current` = greatest(`current`, %(highest)s)""",
		{"key": SERIES_KEY, "highest": highest_album_number()},
	)
	while True:
		number = getseries(SERIES_KEY, DIGITS)
		if len(number) > DIGITS:
			frappe.throw(_("Album numbers are exhausted ({0} digits)").format(DIGITS))
		if not frappe.db.exists("Student", {"album_number": number}):
			return number


def assign_album_number(student: str) -> str:
	"""Give the student the next album number unless they already have one."""

	current = frappe.db.get_value("Student", student, "album_number")
	if current:
		return current
	number = next_album_number()
	frappe.db.set_value("Student", student, "album_number", number)
	return number
