# Integration work list

Status of the tasks from the original Polish list (`git log` keeps the history
of that version).

## mLegitymacja

- [x] DocType `Student Card Order` (student, type: issue, renewal or
      revocation, MLS order number, status, dates, QR and activation codes,
      error) - `education/education/doctype/student_card_order/`.
- [x] `@frappe.whitelist()` methods: `create_card_order(student, kind)` and
      `get_card_order(name)` in `mlegitymacje/api.py`; personal data and the
      picture are read from `Student` on the Frappe side (`service.py`).
- [x] Scheduler task in `hooks.py` (`scheduler_events` -> `cron`, every ten
      minutes) polling `GetOrderStatus` and `GetQrCodes` for orders in flight.
- [x] Hub notification on status change - signed webhook (`hub.py`,
      HMAC-SHA256 over the raw body).
- [x] `MlsAPI` reads the `.p12` certificate and its password from the site
      configuration, not from code; `print` replaced by `frappe.log_error`.
- [x] Removed the fictional `students_list()` sample from the old `mlsapi.py`
      (the file was split into `client.py` and `helpers.py`).

## POL-on

- [x] Preparation of report data in Frappe (`service.build_student_request`),
      acceptance in the hub BPMN process, sending and verification through the
      connector (`prepare_student_report`, `send_student_report`,
      `verify_student_report`).
- [x] `polon_add` no longer uses hardcoded sample values (year, programme,
      semester 2024/2025, `Gender.MALE`); it is built from `Student` and
      `Program Enrollment`.
- [x] Decision on POL-on dictionaries: they stay in the hub
      (`galwork-hub/wf/services/dictionary_service.py`) as form dictionaries;
      the connector only maps their codes onto POL-on values.

## Open items

- Semester reporting of students whose study is assigned to a field of study
  (`CorrectSemesterWithFosRequest`) is not exposed yet - only the
  "without field of study" variant is implemented.
- Basis for admission / exemption from fees and financial aids are sent as empty
  lists until the hub provides them per student.
- `education/integrations` has no Desk doctype for POL-on reports: the process
  state lives in the hub, Frappe keeps no report documents.
