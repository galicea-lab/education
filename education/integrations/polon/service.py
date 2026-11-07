"""POL-on reporting built from Frappe data.

The Galwork hub owns the BPMN process (preparation -> approval -> sending ->
verification); this module supplies the Frappe side of it:

* :func:`prepare_student_report` builds the POL-on payload from a ``Student``
  and a ``Program Enrollment`` and returns it for review - nothing is sent,
* :func:`send_student_report` submits the prepared payload to POL-on,
* :func:`verify_student_report` looks the student up in POL-on and returns what
  the national system holds, so the hub can compare it with Frappe.

Dictionaries (levels, document types, basis for admission, ...) stay in the
hub's dictionary service; here they are only mapped onto the POL-on codes.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import getdate, today

from .polon_api import PolonAPI, PolonError, get_client
from .student_models import (
	AcademicSemester,
	CourseAssignedToFieldOfStudyRequest,
	DocumentType,
	Gender,
	GeneralInformationRequest,
	IdentificationDataRequest,
	IdentificationDocumentDataRequest,
	PlaceOfResidence,
	SemesterWithFosRequest,
	StudentCourseDataRequest,
	StudentPersonalDataRequest,
	StudentRequest,
	StudentSearchFilter,
)

# Program.degree_type -> POL-on level code (studia pierwszego/drugiego stopnia).
LEVEL_MAP = {
	"Bachelor": "LEVEL_I",
	"Engineer": "LEVEL_I",
	"Master": "LEVEL_II",
	"Postp": "JM",
}

# Program.study_mode -> POL-on form code.
FORM_MAP = {
	"Full-Time": "FULL_TIME",
	"Part-Time": "PART_TIME",
}

# Student.polon_locality_type values -> POL-on place of residence.
LOCALITY_MAP = {
	"CITY": PlaceOfResidence.CITY,
	"URBAN": PlaceOfResidence.CITY,
	"VILLAGE": PlaceOfResidence.VILLAGE,
	"RURAL": PlaceOfResidence.VILLAGE,
}

DEFAULT_COUNTRY = "PL"
FOS_CACHE_KEY = "polon:fields_of_study:{institution}"
FOS_CACHE_TTL = 3600


# ----------------------------------------------------------------------
# field of study lookup
# ----------------------------------------------------------------------
def resolve_fos_code(client: PolonAPI | None = None, *, program: str, level: str) -> str | None:
	"""Return the POL-on ``fosCode`` of a program and level.

	The list of fields of study is cached per institution for an hour, because
	it changes rarely and POL-on only pages it.
	"""

	cache_key = FOS_CACHE_KEY.format(institution=(client or get_client()).institution)
	fields_of_study = frappe.cache.get_value(cache_key)
	if fields_of_study is None:
		client = client or get_client()
		client.authenticate()
		fields_of_study = client.find_all_fos()
		frappe.cache.set_value(cache_key, fields_of_study, expires_in_sec=FOS_CACHE_TTL)

	level = level if level not in LEVEL_MAP else LEVEL_MAP[level]
	for field_of_study in fields_of_study:
		if field_of_study.get("name") == program and field_of_study.get("level") == level:
			return field_of_study.get("fosCode")
	return None


def level_code(degree_type: str | None) -> str:
	"""Return the POL-on level code for ``Program.degree_type``."""

	try:
		return LEVEL_MAP[degree_type]
	except KeyError:
		frappe.throw(
			frappe._(
				"Cannot map degree type {0} to a POL-on level; set Program.degree_type to one of: {1}"
			).format(frappe.bold(degree_type or "-"), ", ".join(LEVEL_MAP))
		)


# ----------------------------------------------------------------------
# academic context
# ----------------------------------------------------------------------
def academic_year_label(academic_year: str | None, fallback_date=None) -> str:
	"""Return the academic year in the POL-on notation (``2024/2025``)."""

	if academic_year:
		return academic_year.replace("-", "/")

	anchor = getdate(fallback_date or today())
	start = anchor.year if anchor.month >= 10 else anchor.year - 1
	return f"{start}/{start + 1}"


def academic_semester_code(academic_term: str | None, fallback_date=None) -> AcademicSemester:
	"""Return WINTER/SUMMER based on ``Academic Term.is_summer_term``."""

	if academic_term:
		is_summer = frappe.db.get_value("Academic Term", academic_term, "is_summer_term")
		return AcademicSemester.SUMMER if is_summer else AcademicSemester.WINTER

	# Without a term, the Polish academic calendar is the best available hint:
	# the summer semester starts in February/March.
	month = getdate(fallback_date or today()).month
	return AcademicSemester.SUMMER if 2 <= month <= 9 else AcademicSemester.WINTER


def get_program_enrollment(student: str, program_enrollment: str | None = None):
	"""Return the given program enrollment or the student's latest one."""

	if program_enrollment:
		return frappe.get_doc("Program Enrollment", program_enrollment)

	latest = frappe.get_all(
		"Program Enrollment",
		filters={"student": student},
		pluck="name",
		order_by="enrollment_date desc",
		limit=1,
	)
	if not latest:
		frappe.throw(
			frappe._("Student {0} has no Program Enrollment - POL-on needs the field of study").format(
				frappe.bold(student)
			)
		)
	return frappe.get_doc("Program Enrollment", latest[0])


def study_semester_number(student: str, program: str, enrollment_date=None) -> int:
	"""Return the ordinal number of the student's semester in the program.

	POL-on expects an absolute semester number (1..N). Frappe does not store it,
	so it is derived from the enrollments of the student in the same program:
	the first one is semester 1, the next one 2, and so on.
	"""

	enrollments = frappe.get_all(
		"Program Enrollment",
		filters={"student": student, "program": program},
		fields=["name", "enrollment_date"],
		order_by="enrollment_date asc",
	)
	position = 0
	for index, enrollment in enumerate(enrollments, start=1):
		if enrollment_date and getdate(enrollment.enrollment_date) > getdate(enrollment_date):
			break
		position = index
	return max(position, 1)


def accumulated_ects(program: str, study_semester: int) -> float:
	"""Return the ECTS points planned for the program up to ``study_semester``."""

	total = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(course.ects_credits), 0)
		FROM `tabProgram Course` AS program_course
		JOIN `tabCourse` AS course ON course.name = program_course.course
		WHERE program_course.parent = %s AND program_course.semester_number <= %s
		""",
		(program, study_semester),
	)
	return float(total[0][0] or 0)


# ----------------------------------------------------------------------
# payload
# ----------------------------------------------------------------------
def student_gender(student_doc, pesel: str | None = None) -> Gender:
	"""Map ``Student.gender`` (or the PESEL) onto the POL-on gender enum."""

	value = (student_doc.gender or "").strip().lower()
	if value.startswith("m"):
		return Gender.MALE
	if value.startswith(("f", "k")):
		return Gender.FEMALE
	if pesel:
		return Gender.FEMALE if int(pesel[9]) % 2 == 0 else Gender.MALE
	frappe.throw(
		frappe._("Student {0} has no gender set and no PESEL to derive it from").format(
			frappe.bold(student_doc.name)
		)
	)


def citizenship_codes(student_doc) -> list[str]:
	"""Return ISO-3166 alpha-2 citizenship codes for the student."""

	value = (student_doc.citizenship or "").strip().upper()
	if len(value) == 2 and value.isalpha():
		return [value]
	country = frappe.db.get_value("Country", student_doc.country, "code") if student_doc.country else None
	return [country.upper() if country else DEFAULT_COUNTRY]


def build_student_request(
	student: str,
	program_enrollment: str | None = None,
	*,
	fos_code: str | None = None,
	academic_year: str | None = None,
	academic_term: str | None = None,
	study_semester: int | None = None,
	ects: float | None = None,
	level: str | None = None,
	client: PolonAPI | None = None,
) -> StudentRequest:
	"""Build the POL-on payload for a student from Frappe data.

	The payload is assembled from ``Student`` (personal data, documents,
	PESEL), ``Program Enrollment`` (program, year, term, start date) and
	``Program`` (degree type, study mode). No sample or hardcoded student data
	is used.
	"""

	student_doc = frappe.get_doc("Student", student)
	enrollment = get_program_enrollment(student, program_enrollment)
	program_doc = frappe.get_doc("Program", enrollment.program)

	pesel = (student_doc.pesel or "").strip() or None
	document_number = (student_doc.identity_card_number or student_doc.passport_number or "").strip() or None
	document_type = (
		DocumentType.ID_CARD if student_doc.identity_card_number else DocumentType.PASSPORT
	)

	if not pesel and not document_number:
		frappe.throw(
			frappe._("Student {0} needs a PESEL or an identity document number for POL-on").format(
				frappe.bold(student)
			)
		)

	birth_date = student_doc.date_of_birth
	if not birth_date:
		frappe.throw(frappe._("Student {0} has no date of birth").format(frappe.bold(student)))

	study_semester = study_semester or study_semester_number(
		student, enrollment.program, enrollment.enrollment_date
	)
	ects = ects if ects is not None else accumulated_ects(enrollment.program, study_semester)
	fos_code = fos_code or resolve_fos_code(
		client, program=enrollment.program, level=level or level_code(program_doc.degree_type)
	)
	if not fos_code:
		frappe.throw(
			frappe._("Program {0} is not reported in POL-on as {1}").format(
				frappe.bold(enrollment.program), frappe.bold(level or level_code(program_doc.degree_type))
			)
		)

	year_label = academic_year_label(
		academic_year or enrollment.academic_year, enrollment.enrollment_date
	)
	semester_code = academic_semester_code(academic_term or enrollment.academic_term, enrollment.enrollment_date)

	identification = IdentificationDataRequest(pesel=pesel)
	if document_number:
		identification.document = IdentificationDocumentDataRequest(
			documentCountry=DEFAULT_COUNTRY,
			documentNumber=document_number,
			documentType=document_type,
		)

	return StudentRequest(
		studentPersonalData=StudentPersonalDataRequest(
			name=student_doc.first_name,
			otherNames=student_doc.middle_name or None,
			surnamePrefix="",
			surname=student_doc.last_name,
			gender=student_gender(student_doc, pesel),
			birthYear=getdate(birth_date).year,
			citizenships=citizenship_codes(student_doc),
			birthCountry=DEFAULT_COUNTRY,
			originCountry=DEFAULT_COUNTRY,
			hasPlCard=False,
			identificationData=identification,
		),
		studentCourseData=StudentCourseDataRequest(
			generalInformation=GeneralInformationRequest(
				educationStartDate=getdate(enrollment.enrollment_date or birth_date),
				discontinuationDate=None,
				diplomaData=None,
				placeOfResidence=_place_of_residence(student_doc),
				note="",
				exclusionFromStudiesProcedure=False,
				teacherTraining=False,
				basesForAdmission=[],
				basesForExemptionFromFees=[],
				financialAids=[],
			),
			courseAssignedToFieldOfStudy=CourseAssignedToFieldOfStudyRequest(
				semesters=[
					SemesterWithFosRequest(
						academicYear=year_label,
						academicSemester=semester_code,
						studySemester=study_semester,
						accumulatedEcts=ects,
						accumulatedEctsTeacherTraining=0,
						confirmedLearningOutcomesEcts=0,
						fieldOfStudyInstanceCode=int(fos_code),
						fieldOfStudyInstanceCodeEmpty=False,
					)
				]
			),
		),
	)


def _place_of_residence(student_doc) -> PlaceOfResidence:
	"""Return the POL-on place of residence from ``Student.polon_locality_type``."""

	return LOCALITY_MAP.get((student_doc.polon_locality_type or "").strip().upper(), PlaceOfResidence.CITY)


# ----------------------------------------------------------------------
# hub-facing operations
# ----------------------------------------------------------------------
def prepare_student_report(
	student: str,
	program_enrollment: str | None = None,
	**options,
) -> dict:
	"""Return the POL-on payload for review; nothing is sent to POL-on."""

	request = build_student_request(student, program_enrollment, **options)
	enrollment = get_program_enrollment(student, program_enrollment)

	return {
		"student": student,
		"album_number": frappe.db.get_value("Student", student, "album_number"),
		"program": enrollment.program,
		"academic_year": enrollment.academic_year,
		"academic_term": enrollment.academic_term,
		"payload": json.loads(request.model_dump_json(exclude_none=True)),
	}


def send_student_report(
	student: str,
	program_enrollment: str | None = None,
	*,
	client: PolonAPI | None = None,
	**options,
) -> dict:
	"""Build and submit the POL-on payload of a student."""

	request = build_student_request(student, program_enrollment, client=client, **options)
	client = client or get_client()
	client.authenticate()

	try:
		response = client.add_student(request)
	except PolonError as exc:
		return {"ok": False, "student": student, "error": str(exc)}

	return {"ok": True, "student": student, "response": response}


def verify_student_report(
	student: str,
	*,
	client: PolonAPI | None = None,
) -> dict:
	"""Return the POL-on record of a student together with match information."""

	student_doc = frappe.get_doc("Student", student)
	search_filter = StudentSearchFilter(
		pesel=(student_doc.pesel or None),
		surname=student_doc.last_name or None,
		name=student_doc.first_name or None,
	)
	if not search_filter.pesel and not search_filter.surname:
		frappe.throw(
			frappe._("Student {0} has neither a PESEL nor a surname to search by").format(
				frappe.bold(student)
			)
		)

	client = client or get_client()
	client.authenticate()
	matches = client.find_students(search_filter)

	records = [
		{
			"studentInUniversityId": match.get("studentInUniversityId"),
			"externalId": match.get("externalId"),
			"name": match.get("name"),
			"surname": match.get("surname"),
			"isCorrect": match.get("isCorrect"),
			"courses": [
				{
					"fieldOfStudyInstanceName": course.get("fieldOfStudyInstanceName"),
					"fieldOfStudyInstanceCode": (course.get("fieldOfStudyInstanceCode") or {}).get("code"),
					"studyId": course.get("studentStudyId"),
				}
				for course in match.get("courses") or []
			],
		}
		for match in matches
	]

	return {
		"student": student,
		"album_number": student_doc.album_number,
		"found": bool(records),
		"matches": records,
		"external_id_matches": any(
			record["externalId"] == student_doc.album_number for record in records
		),
	}


def add_semester(
	student_in_university_id: str,
	study_id: str,
	semester_number: int,
	*,
	client: PolonAPI | None = None,
) -> dict:
	"""Add a semester to a POL-on study (used after a verified correction).

	``student_in_university_id`` and ``study_id`` come from
	:func:`verify_student_report`.
	"""

	client = client or get_client()
	client.authenticate()
	try:
		return {"ok": True, "response": client.add_semester(student_in_university_id, study_id, semester_number)}
	except PolonError as exc:
		return {"ok": False, "error": str(exc)}
