# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Unit tests of the MLS helper functions (no site or network needed)."""

from __future__ import annotations

import base64
import unittest
from io import BytesIO

from PIL import Image

from education.integrations.mlegitymacje.helpers import (
	MIN_HEIGHT,
	MIN_WIDTH,
	gender_from_pesel,
	normalize_photo,
	parse_pesel,
	pesel_to_date,
	validate_pesel,
)

# Reference numbers: 44051401458 is the canonical example from the PESEL
# specification, 02070803628 was verified against the same checksum rules.
VALID_MALE_PESEL = "44051401458"
VALID_FEMALE_PESEL = "02070803628"


def _pesel_with_checksum(first_ten_digits: str) -> str:
	"""Complete ten digits with the PESEL checksum digit (test helper only)."""

	weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
	checksum = sum(int(digit) * weight for digit, weight in zip(first_ten_digits, weights, strict=True))
	return first_ten_digits + str((10 - checksum % 10) % 10)


def _image_base64(size=(10, 10), mode="RGB", fmt="PNG") -> str:
	image = Image.new(mode, size, 0 if mode != "RGB" else (120, 80, 60))
	buffer = BytesIO()
	image.save(buffer, fmt)
	return base64.b64encode(buffer.getvalue()).decode("ascii")


class TestPesel(unittest.TestCase):
	def test_reference_numbers_are_valid(self):
		self.assertTrue(validate_pesel(VALID_MALE_PESEL))
		self.assertTrue(validate_pesel(VALID_FEMALE_PESEL))

	def test_birth_date_and_gender_decoding(self):
		decoded = parse_pesel(VALID_MALE_PESEL)
		self.assertEqual(decoded["birth_date"], "1944-05-14")
		self.assertEqual(decoded["gender"], "MALE")

		decoded = parse_pesel(VALID_FEMALE_PESEL)
		self.assertEqual(decoded["birth_date"], "1902-07-08")
		self.assertEqual(decoded["gender"], "FEMALE")

	def test_twentieth_century_months(self):
		# Month 81..92 encodes the 1800s, 21..32 the 2000s.
		born_1800s = _pesel_with_checksum("0081011234")
		self.assertEqual(pesel_to_date(born_1800s), "1800-01-01")

		born_2000s = _pesel_with_checksum("0021131234")
		self.assertEqual(pesel_to_date(born_2000s), "2000-01-13")

	def test_gender_from_tenth_digit(self):
		self.assertEqual(gender_from_pesel(_pesel_with_checksum("9001011231")), "MALE")
		self.assertEqual(gender_from_pesel(_pesel_with_checksum("9001011232")), "FEMALE")

	def test_invalid_pesels(self):
		for invalid in ("", "1234567890", "12345678901", "4405140145X", "44051401459"):
			with self.subTest(pesel=invalid):
				self.assertFalse(validate_pesel(invalid))
				with self.assertRaises(ValueError):
					parse_pesel(invalid)

	def test_invalid_month_and_day(self):
		# Month codes 13..20 do not belong to any century.
		with self.assertRaises(ValueError):
			pesel_to_date(_pesel_with_checksum("0013011234"))
		# 31 April does not exist.
		with self.assertRaises(ValueError):
			pesel_to_date(_pesel_with_checksum("9004311234"))


class TestPhotoNormalisation(unittest.TestCase):
	def test_small_picture_is_upscaled(self):
		result = normalize_photo(_image_base64((10, 10)))
		image = Image.open(BytesIO(base64.b64decode(result)))
		self.assertEqual(image.format, "JPEG")
		self.assertGreaterEqual(image.width, MIN_WIDTH)
		self.assertGreaterEqual(image.height, MIN_HEIGHT)

	def test_large_picture_keeps_dimensions(self):
		result = normalize_photo(_image_base64((600, 800), fmt="JPEG"))
		image = Image.open(BytesIO(base64.b64decode(result)))
		self.assertEqual((image.width, image.height), (600, 800))

	def test_transparent_picture_is_flattened(self):
		result = normalize_photo(_image_base64((400, 500), mode="RGBA"))
		image = Image.open(BytesIO(base64.b64decode(result)))
		self.assertEqual(image.mode, "RGB")

	def test_broken_picture_raises(self):
		for broken in ("", "not-base64!", base64.b64encode(b"not an image").decode("ascii")):
			with self.subTest(value=broken):
				with self.assertRaises(ValueError):
					normalize_photo(broken)


if __name__ == "__main__":
	unittest.main()
