# Integracje z systemami zewnętrznymi

Wydawanie legitymacji i sprawozdawczość do POLON to funkcje systemu dziekanatowego. 

Współdziałanie z systememe Galwork, który jest kolektorem i routerem informacji: 
prowadzi obiegi BPMN i wymienia z Frappe Education tylko zlecenia i ich stan.

## Zawartość

- `mlegitymacje/`: klient API mLegitymacji (`mls.py`: `CreateOrder`, `GetOrderStatus`, `GetQrCodes`, `GetStudentStatus`, `RevokeStudentCard`), modele DTO (`mls_model.py`), pomocnicze funkcje do zdjęć i dat z PESEL (`mlsapi.py`).
- `polon/`: klient API POLON 2 (`polon_api.py`) i modele wygenerowane z OpenAPI (`model.py`, `student_models.py`).
- `config.py`: konfiguracja z `site_config.json` (sekcje `mls` i `polon`).

