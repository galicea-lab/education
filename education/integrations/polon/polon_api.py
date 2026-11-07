"""Client of the POL-on 2 ``fields-of-study-api``.

Used by the dean's office to report student data to the national POL-on
system: adding students, semester updates and lookups used for verification.

API reference:
https://polon2.opi.org.pl/fields-of-study-api/swagger-ui/index.html

Authentication is a Keycloak password grant (the POL-on credentials of the
institution user); every request carries the ``institution`` header. Settings
come from the ``polon`` section of ``site_config.json``::

	"polon": {
		"polon_user": "...",
		"polon_password": "...",
		"polon_institution": "...",
		"polon_api_url": "https://polon2.opi.org.pl"
	}

Failures are raised as :class:`PolonError` and logged to ``Error Log`` together
with the response body, so a rejected report can be diagnosed from Desk.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import frappe
from requests import sessions

from ..config import polon_config
from .student_models import (
	CorrectSemesterWithoutFosRequest,
	ImportStudentInUniversityRequest,
	Pageable,
	StudentRequest,
	StudentSearchFilter,
)

DEFAULT_API_URL = "https://polon2.opi.org.pl"
TOKEN_URL = "https://mcl.opi.org.pl/auth/realms/OPI/protocol/openid-connect/token"

TOKEN_REFRESH_MARGIN = 60  # seconds before expiry
MAX_PAGES = 20
DEFAULT_PAGE_SIZE = 50

MAX_LOGGED_RESPONSE = 4000


class PolonError(frappe.ValidationError):
	"""Raised when POL-on refuses a request or returns an unexpected payload."""


def merge_params(first: dict, second: dict) -> dict:
	"""Merge two parameter dicts, keeping non-empty values of ``first``.

	Used to combine a search filter with the paging parameters; ``None`` and
	empty strings are treated as "not set".
	"""

	result = {}
	for key in first.keys() | second.keys():
		value = first[key] if key in first and first[key] not in (None, "") else second.get(key)
		if value not in (None, ""):
			result[key] = value
	return result


class PolonAPI:
	"""Thin wrapper around the POL-on endpoints used by the connector."""

	def __init__(
		self,
		settings=None,
		*,
		institution: str | None = None,
		api_url: str | None = None,
		username: str | None = None,
		password: str | None = None,
		token_url: str | None = None,
		timeout: int | None = None,
	):
		settings = settings if settings is not None else polon_config()
		self.institution = institution or settings.get("polon_institution")
		self.api_url = (api_url or settings.get("polon_api_url") or DEFAULT_API_URL).rstrip("/")
		self.username = username or settings.get("polon_user")
		self.password = password if password is not None else settings.get("polon_password")
		self.token_url = token_url or settings.get("polon_token_url") or TOKEN_URL
		self.timeout = int(timeout or settings.get("timeout") or 60)

		self.access_token: str | None = None
		self._token_expires_at: datetime | None = None

		self._check_settings()

	def _check_settings(self) -> None:
		missing = [
			key
			for key, value in {
				"polon_institution": self.institution,
				"polon_user": self.username,
				"polon_password": self.password,
			}.items()
			if not value
		]
		if missing:
			frappe.throw(
				frappe._("Missing POL-on configuration (site_config.json, section 'polon'): {0}").format(
					", ".join(missing)
				),
				exc=PolonError,
			)

	# ------------------------------------------------------------------
	# transport
	# ------------------------------------------------------------------
	def _request(self, method: str, url: str, **kwargs):
		with sessions.Session() as session:
			return session.request(method, url, timeout=self.timeout, **kwargs)

	def _url(self, path: str) -> str:
		return self.api_url + path

	def _headers(self, *, with_token: bool = True, json_content: bool = False) -> dict:
		headers = {
			"Accept": "application/json,text/plain,*/*",
			"institution": self.institution,
		}
		if json_content:
			headers["Content-Type"] = "application/json"
		if with_token:
			headers["Authorization"] = "Bearer " + (self.access_token or "")
		return headers

	@staticmethod
	def _body(response) -> str:
		try:
			return response.content.decode("utf-8")
		except Exception:
			return repr(response.content)

	def _check(self, response, context: str) -> str:
		"""Validate a response and return its body or raise :class:`PolonError`."""

		body = self._body(response)
		if response.status_code not in (200, 201, 204):
			message = f"POL-on {context} failed ({response.status_code}): {body}"
			frappe.log_error(title=f"POL-on {context} failed", message=message[:MAX_LOGGED_RESPONSE])
			raise PolonError(message[:MAX_LOGGED_RESPONSE])
		return body

	@staticmethod
	def _json(body: str) -> dict:
		if not body:
			return {}
		try:
			return json.loads(body)
		except json.JSONDecodeError as exc:
			raise PolonError(f"POL-on returned an invalid JSON payload: {body[:500]}") from exc

	# ------------------------------------------------------------------
	# authentication
	# ------------------------------------------------------------------
	def authenticate(self, *, force: bool = False) -> str:
		"""Return a valid bearer token, requesting a new one when needed."""

		if not force and self.access_token and self._token_expires_at:
			if datetime.now(timezone.utc) + timedelta(seconds=TOKEN_REFRESH_MARGIN) < self._token_expires_at:
				return self.access_token

		response = self._request(
			"POST",
			self.token_url,
			data={
				"client_id": "polon2",
				"grant_type": "password",
				"username": self.username,
				"password": self.password,
			},
			headers={
				"Content-Type": "application/x-www-form-urlencoded",
				"Accept": "*/*",
			},
		)
		if response.status_code != 200:
			message = f"POL-on authentication failed ({response.status_code}): {self._body(response)}"
			frappe.log_error(title="POL-on authentication failed", message=message[:MAX_LOGGED_RESPONSE])
			raise PolonError(message[:MAX_LOGGED_RESPONSE])

		payload = self._json(self._body(response))
		self.access_token = payload["access_token"]
		expires_in = int(payload.get("expires_in") or 0)
		self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
		return self.access_token

	# ------------------------------------------------------------------
	# lookups
	# ------------------------------------------------------------------
	def get_fos(self, *, page: int = 0, size: int = DEFAULT_PAGE_SIZE, sort: str = "name,asc") -> list[dict]:
		"""Return one page of the fields of study (``fos``) of the institution."""

		response = self._request(
			"GET",
			self._url("/fields-of-study-api/v3.0/fos"),
			params={"page": page, "size": size, "sort": sort},
			headers=self._headers(),
		)
		payload = self._json(self._check(response, "fields of study lookup"))
		return payload.get("_embedded", {}).get("fosDocumentList", [])

	def find_all_fos(self, *, max_pages: int = MAX_PAGES, size: int = DEFAULT_PAGE_SIZE) -> list[dict]:
		"""Return every field of study of the institution (paged endpoint)."""

		result = []
		for page in range(max_pages):
			batch = self.get_fos(page=page, size=size)
			result.extend(batch)
			if len(batch) < size:
				break
		return result

	def find_students(
		self,
		search_filter: StudentSearchFilter,
		pageable: Pageable | None = None,
	) -> list[dict]:
		"""Search the students reported by the institution.

		``pageable`` defaults to the first 25 records sorted by surname, which is
		enough for the verification of a single student.
		"""

		pageable = pageable or Pageable(page=0, size=25, sort=["surname,ASC"])
		params = merge_params(search_filter.model_dump(), pageable.model_dump())
		if sort := params.get("sort"):
			params["sort"] = ",".join(sort) if isinstance(sort, list) else sort
		params["institutionId"] = self.institution

		response = self._request(
			"GET",
			self._url("/fields-of-study-api/v3.0/university/students"),
			params=params,
			headers=self._headers(),
		)
		payload = self._json(self._check(response, "student search"))
		return payload.get("_embedded", {}).get("studentInUniversityBasicResponseDocumentList", [])

	# ------------------------------------------------------------------
	# writes
	# ------------------------------------------------------------------
	def add_student(self, student: StudentRequest | ImportStudentInUniversityRequest) -> dict:
		"""Add a new student to the institution (``addStudent``)."""

		response = self._request(
			"POST",
			self._url("/fields-of-study-api/v1.0/university/students"),
			data=student.model_dump_json(exclude_none=True),
			headers=self._headers(json_content=True),
		)
		body = self._check(response, "student import")
		return self._json(body) or {"raw": body}

	def add_semester(
		self,
		student_in_university_id: str,
		study_id: str,
		semester_number: int,
		*,
		accumulated_ects: float | None = None,
	) -> dict:
		"""Add a semester to a study that is not assigned to a field of study."""

		payload = CorrectSemesterWithoutFosRequest(studySemester=semester_number)
		response = self._request(
			"POST",
			self._url(
				f"/fields-of-study-api/v3.0/university/students/{student_in_university_id}"
				f"/studies/{study_id}/semesters/without-fos-instance"
			),
			data=payload.model_dump_json(exclude_none=True),
			headers=self._headers(json_content=True),
		)
		body = self._check(response, "semester import")
		return self._json(body) or {"raw": body, "accumulated_ects": accumulated_ects}


def get_client(**overrides) -> PolonAPI:
	"""Return a configured POL-on client."""

	return PolonAPI(**overrides)


def open_polon() -> PolonAPI:
	"""Return an authenticated POL-on client (legacy helper name)."""

	client = get_client()
	client.authenticate()
	return client


# ----------------------------------------------------------------------
# backwards compatible helpers (older scripts/my-school tooling used these)
# ----------------------------------------------------------------------
def polon_open() -> PolonAPI:
	"""Deprecated alias of :func:`open_polon`."""

	return open_polon()


def polon_fos(program: str, level: str) -> str | None:
	"""Deprecated: resolve a POL-on ``fosCode`` from a program name and level."""

	from .service import resolve_fos_code

	return resolve_fos_code(program=program, level=level)


def polon_add_stud(payload: ImportStudentInUniversityRequest, polon: PolonAPI | None = None) -> dict:
	"""Send an already built POL-on payload (kept for existing integrations)."""

	client = polon or get_client()
	client.authenticate()
	return client.add_student(payload)


def polon_add(*args, **kwargs) -> dict:
	"""Deprecated: use ``education.integrations.polon.api.polon_add``."""

	from .api import polon_add as _polon_add

	return _polon_add(*args, **kwargs)
