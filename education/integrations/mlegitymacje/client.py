"""Client of the mLegitymacja (MLS) student card API.

The school talks to MLS over mutual TLS: every request is signed with the
school's PKCS#12 certificate, and the bearer token comes from the
``student-card`` Keycloak realm (client credentials flow).

Nothing here is hardcoded - host, certificate path, certificate password and
client credentials are read from the ``mls`` section of ``site_config.json``,
so keys never live in the repository::

	"mls": {
		"host": "https://mls.example.org",
		"p12_path": "/home/frappe/private/mls/client.p12",
		"password": "p12-password",
		"client_id": "...",
		"client_secret": "..."
	}

Errors are raised as :class:`MlsError` and also written to ``Error Log`` with
the response body, so a failed order can be diagnosed from Desk.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import frappe
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
from requests import sessions

from ..config import mls_config
from .mls_model import (
	RevokeStudentCardRequestDto,
	StudentCardCreateOrderRequestDto,
	StudentCardCreateOrderResponseDto,
	StudentCardOrderQrCodesResponseDto,
	StudentCardOrderStatusResponseDto,
	StudentCardOrderStudentStatusResponseDto,
	StudentDto,
)

TOKEN_PATH = "/realms/student-card/protocol/openid-connect/token"
ORDERS_PATH = "/school/external-service/api/student-cards/order"
ORDER_STATUS_PATH = ORDERS_PATH + "/{order_id}/status"
ORDER_QR_CODES_PATH = ORDERS_PATH + "/{order_id}/qr-codes"
STUDENT_STATUS_PATH = ORDERS_PATH + "/{order_id}/students/{student_card_id}/status"

TOKEN_REFRESH_MARGIN = 60  # seconds before expiry
DEFAULT_TIMEOUT = 60
MAX_LOGGED_RESPONSE = 4000


class MlsError(frappe.ValidationError):
	"""Raised when the MLS API refuses a request or returns an invalid payload."""


def load_pkcs12_certificate(pfx_file: str, password: str) -> tuple[bytes, bytes]:
	"""Return ``(pem_chain, pem_private_key)`` from a PKCS#12 bundle."""

	with open(pfx_file, "rb") as cert_file:
		cert_content = cert_file.read()

	private_key, cert, additional_certs = pkcs12.load_key_and_certificates(
		cert_content, password.encode("utf-8") if password else None
	)
	if not private_key:
		raise MlsError(frappe._("The certificate {0} does not contain a private key").format(pfx_file))
	if not cert:
		raise MlsError(
			frappe._("Failed to deserialize the certificate {0} (PEM or PKCS#12 expected)").format(pfx_file)
		)

	key_bytes = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
	pem_sections = [key_bytes] + [c.public_bytes(Encoding.PEM) for c in [cert, *additional_certs]]
	return b"".join(pem_sections), key_bytes


class MlsAPI:
	"""Thin wrapper around the MLS endpoints used by the student card order flow."""

	def __init__(
		self,
		settings=None,
		*,
		host: str | None = None,
		p12_path: str | None = None,
		password: str | None = None,
		client_id: str | None = None,
		client_secret: str | None = None,
		timeout: int | None = None,
	):
		settings = settings if settings is not None else mls_config()
		self.host = (host or settings.get("host") or "").rstrip("/")
		self.p12_path = p12_path or settings.get("p12_path")
		self.password = password if password is not None else (settings.get("password") or "")
		self.client_id = client_id or settings.get("client_id")
		self.client_secret = client_secret or settings.get("client_secret")
		self.timeout = timeout or int(settings.get("timeout") or DEFAULT_TIMEOUT)

		self.access_token: str | None = None
		self._token_expires_at: datetime | None = None
		self._cert_paths: tuple[str, str] | None = None

		self._check_settings()

	def _check_settings(self) -> None:
		missing = [
			key
			for key, value in {
				"host": self.host,
				"p12_path": self.p12_path,
				"client_id": self.client_id,
				"client_secret": self.client_secret,
			}.items()
			if not value
		]
		if missing:
			frappe.throw(
				frappe._("Missing mLegitymacja configuration (site_config.json, section 'mls'): {0}").format(
					", ".join(missing)
				),
				exc=MlsError,
			)

	# ------------------------------------------------------------------
	# transport
	# ------------------------------------------------------------------
	@property
	def cert(self) -> tuple[str, str]:
		"""Paths of the certificate chain and the (decrypted) key file."""

		if self._cert_paths is None:
			self._cert_paths = self._write_certificate_files()
		return self._cert_paths

	def _write_certificate_files(self) -> tuple[str, str]:
		"""Materialise the PKCS#12 bundle as PEM files inside the site's private dir.

		``requests`` only accepts file paths for client certificates, so the
		bundle has to hit the disk. The private folder is not web-served and the
		key file is readable by the site user only.
		"""

		directory = frappe.get_site_path("private", "integrations", "mls")
		os.makedirs(directory, mode=0o700, exist_ok=True)

		pem_chain, pem_key = load_pkcs12_certificate(self.p12_path, self.password)

		cert_path = os.path.join(directory, "certificate.pem")
		key_path = os.path.join(directory, "key.pem")
		for path, content in ((cert_path, pem_chain), (key_path, pem_key)):
			with open(path, "wb") as handle:
				handle.write(content)
			os.chmod(path, 0o600)
		return cert_path, key_path

	def _request(self, method: str, path: str, **kwargs):
		with sessions.Session() as session:
			session.cert = self.cert
			return session.request(
				method,
				self.host + path,
				timeout=self.timeout,
				**kwargs,
			)

	def _headers(self, *, with_token: bool = True, json_content: bool = False) -> dict:
		headers = {"Accept": "application/json"}
		if json_content:
			headers["Content-Type"] = "application/json"
		if with_token:
			headers["Authorization"] = "Bearer " + (self.access_token or "")
		return headers

	def _check_response(self, response) -> str:
		"""Raise :class:`MlsError` unless the response is a successful one."""

		body = self._response_body(response)
		if response.status_code != 200:
			message = f"MLS API error {response.status_code} for {response.request.url}: {body}"
			frappe.log_error(title="MLS API request failed", message=message[:MAX_LOGGED_RESPONSE])
			raise MlsError(message[:MAX_LOGGED_RESPONSE])
		return body

	def _parse(self, response, model):
		"""Validate a JSON response against ``model`` or raise :class:`MlsError`."""

		body = self._check_response(response)

		try:
			payload = json.loads(body) if body else {}
		except json.JSONDecodeError as exc:
			raise MlsError(f"MLS API returned an invalid JSON payload: {body[:500]}") from exc

		try:
			return model(**payload)
		except Exception as exc:
			raise MlsError(f"MLS API returned an unexpected payload: {exc}") from exc

	@staticmethod
	def _response_body(response) -> str:
		try:
			return response.content.decode("utf-8")
		except Exception:
			return repr(response.content)

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
			TOKEN_PATH,
			data={
				"client_id": self.client_id,
				"client_secret": self.client_secret,
				"grant_type": "client_credentials",
			},
			headers={"Content-Type": "application/x-www-form-urlencoded"},
		)
		if response.status_code != 200:
			message = f"MLS token request failed ({response.status_code}): {self._response_body(response)}"
			frappe.log_error(title="MLS authentication failed", message=message[:MAX_LOGGED_RESPONSE])
			raise MlsError(message[:MAX_LOGGED_RESPONSE])

		payload = json.loads(self._response_body(response))
		self.access_token = payload["access_token"]
		expires_in = int(payload.get("expires_in") or 0)
		self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
		return self.access_token

	# ------------------------------------------------------------------
	# endpoints
	# ------------------------------------------------------------------
	def create_order(self, students: list[StudentDto]) -> StudentCardCreateOrderResponseDto:
		"""Add students to a new student card order (``CreateOrder``)."""

		order = StudentCardCreateOrderRequestDto(students=students)
		response = self._request(
			"POST",
			ORDERS_PATH,
			data=order.model_dump_json(),
			headers=self._headers(json_content=True),
		)
		return self._parse(response, StudentCardCreateOrderResponseDto)

	def get_order_status(self, order_id: str) -> StudentCardOrderStatusResponseDto:
		"""Return the processing status of an order (``GetOrderStatus``)."""

		response = self._request(
			"GET",
			ORDER_STATUS_PATH.format(order_id=order_id),
			headers=self._headers(),
		)
		return self._parse(response, StudentCardOrderStatusResponseDto)

	def get_qr_codes(self, order_id: str) -> StudentCardOrderQrCodesResponseDto:
		"""Return QR codes and activation codes for a ready order (``GetQrCodes``)."""

		response = self._request(
			"GET",
			ORDER_QR_CODES_PATH.format(order_id=order_id),
			headers=self._headers(),
		)
		return self._parse(response, StudentCardOrderQrCodesResponseDto)

	def get_student_status(
		self, order_id: str, student_card_id: str
	) -> StudentCardOrderStudentStatusResponseDto:
		"""Return whether a single card has been downloaded to a phone (``GetStudentStatus``)."""

		response = self._request(
			"GET",
			STUDENT_STATUS_PATH.format(order_id=order_id, student_card_id=student_card_id),
			headers=self._headers(),
		)
		return self._parse(response, StudentCardOrderStudentStatusResponseDto)

	def revoke_student_card(self, student_card_id: str) -> str:
		"""Revoke a card that is already installed on a phone (``RevokeStudentCard``)."""

		payload = RevokeStudentCardRequestDto(studentCardId=student_card_id)
		response = self._request(
			"POST",
			ORDERS_PATH,
			data=payload.model_dump_json(),
			headers=self._headers(json_content=True),
		)
		return self._check_response(response)
