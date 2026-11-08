# Portal API (`education.portal`)

Interfejs dla zewnętrznego serwisu studencko-wykładowczego („Wirtualna
Uczelnia”): plan zajęć, oceny, frekwencja, opłaty, korespondencja z dziekanatem
i mLegitymacja dla studenta oraz plan, listy, obecności i oceny dla wykładowcy.
Serwis nie jest częścią aplikacji Frappe — woła te metody przez REST.

## Dwa rodzaje wywołującego

| wywołujący | tożsamość | parametry `student`/`instructor` |
| --- | --- | --- |
| użytkownik zalogowany w Frappe | z sesji (`Student.user`, `Instructor` ↔ `User`) | opcjonalne, muszą się zgadzać z sesją |
| konto usługi (`portal.service_users`, rola `Portal Service`, `Administrator`) | hub, który sam uwierzytelnił użytkownika (Keycloak) | **wymagane** |

Konto usługi to galwork-hub: użytkownik loguje się w galwork-edu przez Keycloak,
hub weryfikuje tożsamość i wywołuje Frappe z `student` = numer albumu (lub
`instructor`) jako parametrem. Frappe nie widzi sesji użytkownika końcowego.

Konfiguracja (`site_config.json`):

```json
"portal": {
  "service_users": ["hub@uczelnia.pl"],
  "passing_percentage": 50,
  "reveal_codes_requires_password": true
}
```

## Uprawnienia (drugą linią obrony)

`education/portal/permissions.py` rejestruje w `hooks.py` warunki wiersza
i warunki dokumentu dla ról portalowych (`Student`, `Guardian`, `Instructor`):

- student widzi własne rekordy, opiekun — podopiecznych,
- wykładowca widzi prowadzone przez siebie grupy i przypisane do nich rekordy,
- użytkownik z rolą dziekanatu (`Academics User`, `Education Manager`,
  `System Manager`) nie jest ograniczany,
- konto usługi nie jest ograniczane (samo zawęża zapytania).

Zakres: `Assessment Result`, `Course Schedule`, `Fees`, `Program Enrollment`,
`Student Academic Status`, `Student Attendance`, `Student Card Order`,
`Student Correspondence`, `Student Group`, `Student Leave Application`.
Wcześniej warunek istniał tylko dla `Student` — przez REST można było odczytać
cudze wyniki i plany.

Wykładowca dostaje prawo do `Course Schedule` (odczyt) i `Assessment Result`
(odczyt, zapis, zatwierdzanie) patchem
`education/patches/v16_0/add_portal_permissions.py` (Custom DocPerm, żeby nie
zmieniać plików doctype'ów z upstreamu).

## Endpointy

Wszystkie przez `POST /api/method/education.portal.api.<nazwa>`.

### Wspólne

| metoda | opis |
| --- | --- |
| `get_capabilities()` | kto to jest (student/wykładowca/dziekanat/usługa), jakie funkcje włączyć, próg zaliczenia |

### Student

| metoda | parametry | wynik |
| --- | --- | --- |
| `get_schedule(from_date?, to_date?, program?)` | zakres dat (domyślnie 7 dni) | `{kind, student, from, to, entries[]}` |
| `get_grades(academic_year?, academic_term?)` | filtr opcjonalny | `{courses[], average, semesters[], passing_percentage}` |
| `get_attendance(from_date?, to_date?)` | zakres opcjonalny | `{overall, groups[], absences[]}` |
| `get_fees()` | — | `{fees[], totals, payment_required}` |
| `get_case_types()` | — | aktywne `Correspondence Type` |
| `get_cases()` | — | `{student, cases[]}` |
| `get_case(name)` | nazwa sprawy | sprawa + oś czasu + dokument |
| `create_case(correspondence_type, subject, description, program?, academic_year?, form_data?)` | dane sprawy | utworzona sprawa; emituje webhook do huba |
| `get_card()` | — | stan legitymacji (bez kodów) |
| `order_card(kind?, release_date?, expire_date?)` | `Issue`/`Renewal` | zamówienie |
| `reveal_card_codes(password?)` | hasło (dla sesji) | `{qr_code, activation_code, valid_to}` — audytowane |
| `request_card_revocation(reason?)` | powód | zamówienie unieważnienia |

### Wykładowca

| metoda | parametry | wynik |
| --- | --- | --- |
| `get_lecturer_context(academic_term?)` | — | `{instructor, groups[], courses[]}` |
| `get_group_roster(group)` | grupa | `{students[]}` |
| `get_group_attendance(group, date?)` | grupa, data | `{group, date, students[], freeze_date}` |
| `save_group_attendance(group, date, entries[, course_schedule])` | `entries=[{student,status}]` | `{saved[], skipped[], count}` (idempotentne per student) |
| `get_group_assessments(group)` | grupa | `{plans[]}` z kryteriami |
| `save_group_results(group, assessment_plan, rows[, submit])` | `rows=[{student,details{}}]` | `{saved[], count}`; ocena z `Grading Scale` |

Konto usługi dokłada `student=` / `instructor=` do każdego wywołania.

## Format i zasady

- Odpowiedzi są słownikami gotowymi do JSON-a (daty jako `YYYY-MM-DD`), bez
  `frappe._dict` i bez pól zbędnych dla UI (np. `get_card()` nie zwraca kodów).
- Zapisy są idempotentne: obecność jest unikalna per `(student, grupa, data)`,
  wynik oceny per `(student, plan oceniania)`.
- Błędy: `frappe.PermissionError` → 403, `DoesNotExistError` → 404,
  `ValidationError` → 417/422 (zależnie od klienta).
- Każdy odczyt idzie przez `frappe.get_list`/`frappe.get_doc`, więc warunki
  wiersza działają także wtedy, gdy hub pomyli się w filtrach.

## Testy

```bash
bench --site <site> run-tests --module education.portal.test_portal
```

`TestPortalStudent` sprawdza sesyjnego studenta (własny plan, brak dostępu do
cudzych ocen, brak możliwości podstawienia cudzych grup w starym endpointcie),
`TestPortalService` ścieżkę huba (kontekst wykładowcy, obrona cudzej grupy,
idempotentny zapis obecności, webhook przy nowej sprawie), `TestPortalConfig`
parsowanie konfiguracji bez bazy.
