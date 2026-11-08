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
  "reveal_codes_requires_password": true,
  "semester_card": {
    "attempt_labels": {"Termin I": "I", "Poprawka": "II", "Komis": "K"}
  }
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
| `get_semester_card(academic_term?, program?)` | domyślnie ostatni rozliczony semestr | Karta Okresowych Osiągnięć — zob. niżej |
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

## Karta Okresowych Osiągnięć (`get_semester_card`)

`education/portal/semester_card.py` — jedno wywołanie na kartę jednego
semestru (zapis na program × semestr akademicki):

| element karty | źródło |
| --- | --- |
| nagłówek: student, kierunek, stopień, tryb | `Student`, `Program` (`degree_type`, `study_mode`) |
| rok akademicki, semestr, rok studiów, semestr następny | `Academic Term`, `Student Academic Status.semester_number`, plan programu |
| rozliczenie semestru | `Student Academic Status` |
| przedmioty semestru | `Program Course.semester_number` + przedmioty z ocenami / grupami w tym semestrze |
| forma zajęć, forma zaliczenia, ECTS | `Course.class_type`, `passing_type`, `ects_credits` |
| podejścia i oceny | zatwierdzone `Assessment Result` semestru; zaliczenie jak w `get_grades` |
| termin (I, II, poprawka, komis) | `Assessment Group` wyniku → `portal.semester_card.attempt_labels`; bez mapy — numer podejścia |
| wykładowca | egzaminator planu oceniania ostatniego podejścia, potem wcześniejszych, potem prowadzący grupy |
| tytuł naukowy | `Employee.salutation` pracownika powiązanego z `Instructor` |
| przedmiot dodatkowy | z planu: wcześniejszy semestr → `repeat` (powtarzanie, warunek), spoza planu → `program_difference`, późniejszy → `other_semester` |

Wynik: `{student, program, academic_year, academic_term, semester_number,
year_of_study, year_of_study_roman, next_semester_number, semester_status,
courses[], totals}`. `totals` rozdziela ECTS planowe i dodatkowe (planowane /
uzyskane) i podaje średnie ważone ECTS: procentową oraz z ocen liczbowych.

Ograniczenia: kategoria przedmiotu dodatkowego jest wyliczana z planu, a nie
zapisana przy studencie (warunek i powtarzanie są nierozróżnialne);
specjalności nie ma w modelu. Pomocnicze odczyty (plan programu, plany
oceniania wyników studenta, prowadzący jego grup) idą przez `frappe.get_all`,
zawężone do danych tego studenta.

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

```bash
bench --site <site> run-tests --module education.portal.test_semester_card
```

Składanie karty (`build_card`) bez bazy oraz dostęp do `get_semester_card`.
