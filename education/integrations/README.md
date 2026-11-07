# Integrations with external systems

Student cards (mLegitymacja) and POL-on reporting are functions of the dean's
office system. This package talks to both national systems and to the Galwork
hub, which is the collector and router of academic information: the hub runs
the BPMN processes and exchanges only orders and their state with Frappe
Education.

## Contents

- `config.py` - settings read from `site_config.json` (sections `mls`, `polon`,
  `hub`); credentials never live in the repository.
- `hub.py` - signed webhook notifications to the hub.
- `mlegitymacje/` - mLegitymacja connector:
  - `client.py` - `MlsAPI` (mutual TLS, Keycloak token, `CreateOrder`,
    `GetOrderStatus`, `GetQrCodes`, `GetStudentStatus`, `RevokeStudentCard`),
  - `mls_model.py` - DTOs generated from the MLS OpenAPI schema,
  - `helpers.py` - PESEL decoding and photo normalisation,
  - `service.py` - order orchestration (Frappe `Student` -> MLS payload ->
    `Student Card Order` document),
  - `api.py` - whitelisted endpoints, `tasks.py` - scheduler entry point.
- `polon/` - POL-on 2 connector:
  - `polon_api.py` - `PolonAPI` client (`fields-of-study-api`),
  - `model.py`, `student_models.py` - models generated from the OpenAPI schema,
  - `service.py` - report building from `Student` + `Program Enrollment`,
    sending and verification,
  - `api.py` - whitelisted endpoints used by the hub.

## Configuration

```json
{
  "mls": {
    "host": "https://mls.example.org",
    "p12_path": "/home/frappe/private/mls/client.p12",
    "password": "p12-password",
    "client_id": "...",
    "client_secret": "...",
    "timeout": 60
  },
  "polon": {
    "polon_user": "...",
    "polon_password": "...",
    "polon_institution": "...",
    "polon_api_url": "https://polon2.opi.org.pl"
  },
  "hub": {
    "url": "https://hub.example.org/api/edu/events",
    "hmac_secret": "shared-secret",
    "timeout": 10
  }
}
```

The PKCS#12 bundle is converted to PEM files in `private/integrations/mls/`
(site private folder, not web-served, key file mode `0600`) because `requests`
only accepts file paths for client certificates.

## mLegitymacja

### Data flow

1. The hub calls `create_card_order(student, kind)` (or the dean's office does
   it from Desk); `kind` is `Issue`, `Renewal` or `Revocation`.
2. Frappe builds the MLS payload from the `Student` record (first/last name,
   middle name, birth date, PESEL, picture from the `image` field) and creates a
   `Student Card Order` document.
3. The order is sent to MLS; the returned order id, the card id and the masked
   request payload are stored on the document.
4. Every ten minutes `tasks.poll_pending_orders` polls `GetOrderStatus` and, for
   ready orders, `GetQrCodes` + `GetStudentStatus`; the QR code, the activation
   code and the validity date land on the document.
5. Every state change is sent to the hub as a signed webhook.

Order statuses: `Draft` -> `Queued` -> `Submitted` -> `Processing` -> `Ready`,
plus `Revoked` and `Error`. A failed order can be retried from the form
("Send to MLS").

### Whitelisted endpoints

| Method | Purpose |
| --- | --- |
| `create_card_order(student, kind, release_date, expire_date, student_card_id)` | create and send an order |
| `get_card_order(name)` | state of a single order |
| `submit_card_order(name)` | retry a draft/failed order |
| `refresh_card_order(name)` | synchronise one order with MLS |
| `revoke_student_card(name)` | create a revocation order for an issued card |
| `list_card_orders(student)` | orders of a student |

### Hub webhook contract

`hub.notify(event, data)` sends:

```
POST <hub.url>
Content-Type: application/json
X-Galwork-Event: student_card_order.status_changed
X-Galwork-Signature: sha256=<hex HMAC-SHA256 of the raw body>

{"data":{...},"event":"student_card_order.status_changed","sent_at":"2026-11-07T06:30:00+00:00"}
```

The body is compact JSON with sorted keys, so the hub can verify the signature
over the bytes it received (`hub.verify_signature` implements the same rule).
Events: `student_card_order.created`, `student_card_order.status_changed`.
A missing configuration or an unreachable hub is logged to `Error Log` and never
rolls back the student flow; `hub_notified_on` records the last acceptance.

## POL-on

The BPMN process (preparation -> approval -> sending -> verification) lives in
the hub; Frappe exposes the steps:

| Method | Purpose |
| --- | --- |
| `prepare_student_report(student, program_enrollment, ...)` | build the payload from Frappe data, nothing is sent |
| `send_student_report(...)` | send the prepared payload to POL-on |
| `verify_student_report(student)` | what POL-on holds, for comparison |
| `add_semester(student_in_university_id, study_id, semester_number)` | semester correction after verification |
| `list_fields_of_study()` | POL-on programmes of the institution |
| `polon_add(student, program_enrollment, ...)` | legacy name of `send_student_report` |

Mapping decisions:

- personal data come from `Student` (PESEL, identity document, citizenship,
  place of residence type, album number as `externalId`),
- degree programme, academic year, term and start date come from
  `Program Enrollment`; the level comes from `Program.degree_type`
  (Bachelor/Engineer -> `LEVEL_I`, Master -> `LEVEL_II`, Postp -> `JM`) and the
  form from `Program.study_mode`,
- the absolute semester number is derived from the student's programme
  enrollments (first enrollment = semester 1) and can be overridden,
- accumulated ECTS default to the sum of `Course.ects_credits` planned for the
  semesters up to the reported one,
- dictionaries (levels, document types, basis for admission, ...) stay in the
  hub's dictionary service; the connector only maps them onto the POL-on codes.

## Tests

```bash
bench --site <site> run-tests --app education
```

`mlegitymacje/test_helpers.py` and `test_hub.py` are pure unit tests (no site,
no network) and can also be run directly:

```bash
python -m unittest education.integrations.test_hub education.integrations.mlegitymacje.test_helpers
```

`mlegitymacje/test_service.py` and `polon/test_service.py` drive the flows with
in-memory fake API clients, so they need a migrated site but no credentials.
