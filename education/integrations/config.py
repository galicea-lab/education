# konfiguracja integracji z systemami zewnętrznymi
# (mLegitymacja, POLON) z site_config.json Frappe, np.:
#   "polon": {"polon_user": "...", "polon_password": "...", "polon_institution": "..."}
#   "mls": {"host": "...", "p12_path": "...", "password": "...", "client_id": "...", "client_secret": "..."}
# Odczyt leniwy: moduły można importować bez kontekstu strony Frappe.
import frappe


class SectionConfig:
	def __init__(self, section: str):
		self.section = section

	def get(self, key: str, default=None):
		return (frappe.conf.get(self.section) or {}).get(key, default)


def section_config(section: str) -> SectionConfig:
	return SectionConfig(section)
