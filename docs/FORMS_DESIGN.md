# Salow Forms: design pass (internal forms first)

Status: design only, nothing built. Source of the requirement: the owner named Google Forms on 24 and 25 Nov 2026 as a related
module (blueprint section 10.3 records it as "not researched, a design pass is needed before any claim"). Forms is not one of
the 13 listed modules and is not in the blueprint build order. This note is the design pass the blueprint asks for.

## Scope of the first slice (internal only)
- A form is a schema owned by one person: title, description, ordered questions.
- People fill it while signed in. There is no public or anonymous fill page and no link sharing in this slice. Who may see a
  public form is an audience policy the owner has not set, so it is not designed here beyond "not in this slice".
- Responses are company records and live in TiDB (like Tasks and Calendar). They hold typed answers only. No file upload
  question type, because file bytes stay off the server and TiDB (storage rule, Nov 24 and Nov 25 2026).
- Results are shown to the owner as a table and a per-question count. Export is a CSV download generated in the browser from
  data the owner already sees. Writing into Sheets is not built, because Sheets files are browser-only today.

## Question types (build default, small and finite)
short_text, long_text, single_choice, multi_choice, number, date, scale_1_5. Each has a required flag. Limits: 50 questions,
question text 300, choice text 100, 20 choices, answer text 2000 (build defaults, not policy).

## Tables
- form(id, public_id uuid, owner FK PROTECT, title, description, status draft|open|closed, version int, created, updated)
- form_question(id, form FK PROTECT, position, kind, text, required, choices json, UNIQUE(form, position))
- form_audience(id, form FK PROTECT, person FK PROTECT, UNIQUE(form, person)): the people who may fill an open form
- form_response(id, form FK PROTECT, person FK PROTECT, form_version, submitted_at, UNIQUE(form, person)): one response per person
- form_answer(id, response FK PROTECT, question FK PROTECT, value text, UNIQUE(response, question))
No delete path until retention is decided. No cascade delete.

## Services and rules
- create_form, edit_question (draft only), publish (draft to open: needs at least one question and one audience member),
  close, submit_response, list_results. Each takes the actor and uses can() with a registered "forms.form" policy:
  owner edits and sees results; an audience member of an open form may submit once and sees only their own answer; everyone
  else gets 404. Default deny, as elsewhere.
- Locking: the form row first, then re-read. Publishing freezes the questions: editing an open form is refused (a new version
  of the schema is a later decision). Submit validates each answer against its question server-side and rejects extras.
- Build default (labelled, like Calendar's): the owner may add any active person to the audience. Nothing notifies anyone and
  no email is sent.
- Audit rows for publish, close, submit.

## UI
List (my forms, forms for me), editor (questions in order, move up and down, one page), fill page, results table with counts
and CSV download, status banners. Plain HTML forms and HTMX like the other modules.

## Tests planned
Owner and non-audience refusal with owner same-payload controls and full-row snapshots (the style of the Drive and Calendar
tests), one response per person under a repeated submit, required and type validation, closed and draft forms refuse fills,
results visible to the owner only, no file-type question exists.

## Open user-owned choices, none needed for this slice
Public or anonymous fill; link sharing; response retention and deletion; whether responses are anonymous to the owner;
export into Sheets; notifications. None are built or defaulted.
