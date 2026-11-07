"""Helpers shared by the mLegitymacja (MLS) connector.

Two concerns live here:

* PESEL decoding - the MLegitymacja API needs the birth date and the card
  holder's sex, both of which are encoded in the PESEL number.
* Photo normalisation - MLS requires a JPEG picture of at least 246x304 px,
  while schools usually keep whatever ID photo they were given.
"""

from __future__ import annotations

import base64
import math
from io import BytesIO

import frappe
from PIL import Image

MIN_WIDTH = 246
MIN_HEIGHT = 304
JPEG_QUALITY = 90

# PESEL checksum weights and the century offsets encoded in the month digits.
_PESEL_WEIGHTS = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
_PESEL_CENTURIES = (
	(1, 12, 1900),
	(21, 32, 2000),
	(41, 52, 2100),
	(61, 72, 2200),
	(81, 92, 1800),
)


def _validate_pesel(pesel: str) -> str:
	"""Return a stripped PESEL or raise ``ValueError`` for an invalid one."""

	pesel = (pesel or "").strip()
	if not (len(pesel) == 11 and pesel.isdigit()):
		raise ValueError(f"PESEL must be an 11-digit string, got: {pesel!r}")

	checksum = sum(int(digit) * weight for digit, weight in zip(pesel[:10], _PESEL_WEIGHTS, strict=False))
	expected = (10 - checksum % 10) % 10
	if expected != int(pesel[10]):
		raise ValueError(f"PESEL checksum mismatch: {pesel!r}")
	return pesel


def pesel_to_date(pesel: str) -> str:
	"""Return the birth date encoded in ``pesel`` as ``YYYY-MM-DD``."""

	year_digits, month_digits, day = int(pesel[:2]), int(pesel[2:4]), int(pesel[4:6])

	year = month = None
	for first, last, century in _PESEL_CENTURIES:
		if first <= month_digits <= last:
			year = century + year_digits
			month = month_digits - (first - 1)
			break

	if year is None:
		raise ValueError(f"Invalid PESEL month code: {pesel!r}")
	if not 1 <= day <= 31:
		raise ValueError(f"Invalid PESEL day: {pesel!r}")
	if month in (4, 6, 9, 11) and day > 30:
		raise ValueError(f"Invalid PESEL day for month {month}: {pesel!r}")
	if month == 2 and day > 29:
		raise ValueError(f"Invalid PESEL day for February: {pesel!r}")

	return f"{year:04d}-{month:02d}-{day:02d}"


def gender_from_pesel(pesel: str) -> str:
	"""Return ``MALE`` or ``FEMALE`` based on the tenth digit of the PESEL."""

	return "FEMALE" if int(pesel[9]) % 2 == 0 else "MALE"


def parse_pesel(pesel: str) -> dict:
	"""Validate ``pesel`` and decode the data the connectors need."""

	pesel = _validate_pesel(pesel)
	return {"pesel": pesel, "birth_date": pesel_to_date(pesel), "gender": gender_from_pesel(pesel)}


def validate_pesel(pesel: str) -> bool:
	"""Return True when ``pesel`` is a syntactically correct PESEL."""

	try:
		parse_pesel(pesel)
	except ValueError:
		return False
	return True


def normalize_photo(base64_image: str) -> str:
	"""Return a base64 JPEG that satisfies the MLS photo requirements.

	The picture is converted to RGB JPEG and upscaled (keeping the aspect
	ratio) when either side is smaller than the MLS minimum of 246x304 px.
	Transparent PNG uploads are flattened onto a white background.
	"""

	raw = (base64_image or "").strip()
	if not raw:
		raise ValueError("Empty picture")

	try:
		data = base64.b64decode(raw, validate=True)
	except Exception as exc:
		raise ValueError("Picture is not valid base64 data") from exc

	try:
		image = Image.open(BytesIO(data))
	except Exception as exc:
		raise ValueError("Picture is not a readable image") from exc

	scale = max(1, math.ceil(MIN_WIDTH / image.width), math.ceil(MIN_HEIGHT / image.height))
	if scale > 1:
		image = image.resize(
			(math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.LANCZOS
		)

	if image.mode in ("RGBA", "LA", "P"):
		background = Image.new("RGB", image.size, (255, 255, 255))
		converted = image.convert("RGBA")
		background.paste(converted, mask=converted.split()[-1])
		image = background
	else:
		image = image.convert("RGB")

	buffer = BytesIO()
	image.save(buffer, "JPEG", quality=JPEG_QUALITY)
	return base64.b64encode(buffer.getvalue()).decode("ascii")


def photo_from_student(student: str) -> str:
	"""Return the base64 JPEG picture of a ``Student``, ready for the MLS API."""

	image_url = frappe.db.get_value("Student", student, "image")
	if not image_url:
		frappe.throw(
			frappe._("Student {0} has no picture - the student card cannot be ordered").format(
				frappe.bold(student)
			)
		)

	try:
		file_doc = frappe.get_doc("File", {"file_url": image_url})
		content = file_doc.get_content()
	except Exception as exc:
		frappe.throw(
			frappe._("Cannot read the picture of student {0}: {1}").format(
				frappe.bold(student), exc
			)
		)

	return normalize_photo(base64.b64encode(content).decode("ascii"))
