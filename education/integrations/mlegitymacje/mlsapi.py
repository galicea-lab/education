import base64
import math
from io import BytesIO

from PIL import Image


def pesel_to_date(pesel):
    if not (isinstance(pesel, str) and len(pesel) == 11 and pesel.isdigit()):
        return "Invalid PESEL: Must be an 11-digit string."
    # Extract first 6 digits: YYMMDD
    year_digits = int(pesel[0:2])
    month_digits = int(pesel[2:4])
    day = int(pesel[4:6])
    # Initialize month
    month = None
    year = None

    # Determine century and month based on month digits
    if 1 <= month_digits <= 12:
        year = 1900 + year_digits
        month = month_digits
    elif 21 <= month_digits <= 32:
        year = 2000 + year_digits
        month = month_digits - 20
    elif 81 <= month_digits <= 92:
        year = 1800 + year_digits
        month = month_digits - 80
    elif 41 <= month_digits <= 52:
        year = 2100 + year_digits
        month = month_digits - 40
    elif 61 <= month_digits <= 72:
        year = 2200 + year_digits
        month = month_digits - 60
    else:
        return "Invalid PESEL: Month code is incorrect."

    # Validate month and day
    if not (1 <= month <= 12):
        return "Invalid PESEL: Month out of range."
    if not (1 <= day <= 31):
        return "Invalid PESEL: Day out of range."

    # Basic day validation for months (ignoring leap years for simplicity)
    if month in [4, 6, 9, 11] and day > 30:
        return "Invalid PESEL: Day invalid for the given month."
    if month == 2 and day > 29:
        return "Invalid PESEL: Day invalid for February."

    return f"{year:04d}-{month:02d}-{day:02d}"


def b64jpeg(img):
  try:
    bimg = base64.b64decode(img)
    bio = BytesIO(bimg)
    image = Image.open(bio)
  except Exception as e:
    print(e)
    return img
  # do mLegitymacji minimalny rozmiar: 246 x 304
  proc=1
  if (image.width<246):
    proc=(246/image.width)
  if (image.height<304):
    proc_height=304/image.height
    if proc_height>proc:
      proc=proc_height
  if proc>1: # Ustal nowy rozmiar
    nowy_rozmiar = (math.ceil(image.width * proc), math.ceil(image.height * proc))

  if img[:3] == '/9j/':
    if proc>1:
      # Zmień rozmiar obrazu
      resized = image.resize(nowy_rozmiar, Image.LANCZOS)
      jpg = BytesIO()
      resized.save(jpg, 'JPEG')
      jpg.seek(0)
      aimage=base64.b64encode(jpg.read()).decode('ascii')
      return aimage
    else:
      return img
  else:
    try:
      bimg=base64.b64decode(img)
      bio=BytesIO(bimg)
      obraz = Image.open(bio)
      if proc > 1:
        # Zmień rozmiar obrazu
        obraz = obraz.resize(nowy_rozmiar, Image.LANCZOS)

      rgb=obraz.convert('RGB')
      jpg = BytesIO()
      rgb.save(jpg, 'JPEG')
      jpg.seek(0)
      image=base64.b64encode(jpg.read()).decode('ascii')
    except Exception as e:
      print(e)
    return image


