"""Configuration helpers for the external-system integrations.

Settings live in the site configuration file (``site_config.json``), one
section per integrated system::

	{
		"mls": {
			"host": "https://mls.example.org",
			"p12_path": "/home/frappe/private/mls/client.p12",
			"password": "p12-password",
			"client_id": "...",
			"client_secret": "..."
		},
		"polon": {
			"polon_user": "...",
			"polon_password": "...",
			"polon_institution": "...",
			"polon_api_url": "https://polon2.opi.org.pl"
		},
		"hub": {
			"url": "https://hub.example.org/api/edu/events",
			"hmac_secret": "...",
			"timeout": 10
		}
	}

Reads are lazy on purpose: the connector modules must stay importable without
an initialised site (unit tests, DTO generators, ``bench execute`` dry runs).
Credentials are never read from source code.
"""

from __future__ import annotations

import frappe


class IntegrationNotConfigured(frappe.ValidationError):
	"""Raised when a required setting of an external system is missing."""


class SectionConfig:
	"""Read-only view over one integration section of ``site_config.json``."""

	def __init__(self, section: str):
		self.section = section

	def _raw(self) -> dict:
		section = (frappe.conf or {}).get(self.section)
		return section if isinstance(section, dict) else {}

	def get(self, key: str, default=None):
		"""Return a single setting or ``default`` when it is not set."""

		value = self._raw().get(key, default)
		return default if value in (None, "") else value

	def get_all(self) -> dict:
		"""Return the whole section as a plain dict (empty when not configured)."""

		return dict(self._raw())

	def is_configured(self, *keys: str) -> bool:
		"""Return True when the section defines every given key."""

		return all(self.get(key) is not None for key in keys)

	def require(self, *keys: str) -> dict:
		"""Return the section and raise when one of the required keys is missing."""

		missing = [key for key in keys if self.get(key) is None]
		if missing:
			frappe.throw(
				frappe._("Missing {0} configuration (site_config.json): {1}").format(
					self.section, ", ".join(missing)
				),
				exc=IntegrationNotConfigured,
			)
		return self.get_all()


def section_config(section: str) -> SectionConfig:
	"""Return the configuration view of an arbitrary section."""

	return SectionConfig(section)


def mls_config() -> SectionConfig:
	"""Settings of the mLegitymacja (MLS) connector."""

	return SectionConfig("mls")


def polon_config() -> SectionConfig:
	"""Settings of the POL-on connector."""

	return SectionConfig("polon")


def hub_config() -> SectionConfig:
	"""Settings of the Galwork hub notifications."""

	return SectionConfig("hub")
