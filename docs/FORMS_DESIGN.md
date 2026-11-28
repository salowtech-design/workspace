# Salow Forms: design pass (internal forms first)

Status: private owner-only creation, editing and preview are built. Collecting answers from other people is built behind
FORMS_COLLECTION_APPROVED (False) and is OFF: publish and submit are refused until the owner scopes audience and identity privacy. Source of the requirement: the owner named Google Forms on 24 and 25 Nov 2026 as a related
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
- Working now (owner only): create_form, update_details, add_question, change_question (remove, move up, move down), set_audience
  (a PROPOSED list, not in effect), preview_check (validates like a real submit and stores nothing). Draft only; removal of
  questions and audience entries is allowed before publish. Position swaps run in two passes so UNIQUE(form, position) holds.
- Built but switched off: publish, submit_response, close, results, csv_text. After publish or close, questions and audience are
  frozen; to change them close the form and make a new one. Nothing changes the audience silently.
- Submit: lock the form row first, re-read, recheck open status and audience through the "forms.form" policy, validate every
  answer against its question, reject question ids that are not on the form, create the response, and map a duplicate-key error to
  "already answered". Number, date and scale answers are stored normalised (7.50 is stored as 7.5; ASCII digits only).
- Visibility: while collection is off a non-owner gets 404 for every form page, whatever the audience list says; with collection on, 404 for draft and closed forms unless they already answered. The owner never answers their own form (adding the owner to the audience is refused; a self-answer is a future choice). Policy and services both go through can() with no administrator bypass.
- Every check is in the service code. The tests were run on real TiDB; no rule depends on a database CHECK.
- CSV: generated on the server (not in the browser) for the owner only. Text, choice and header cells that start with = + - @ tab or CR get a leading apostrophe; number, date and scale cells are validated first (ASCII digits, ISO date, 1 to 5), so a minus there stays data. Multi-choice answers read "a, b".
- Audit rows for create, update, audience, publish, close, submit, export. Nothing notifies anyone. No email.

## Privacy defaults are PROPOSED, not decided
These shape who can see people's answers, so they are the owner's to approve before any real collection:
- Audience: an owner-chosen list of active people (no "everyone").
- Owner sees each response with the respondent's employee ID (not anonymous). No admin or other access to responses.
- A respondent sees only their own answers, also after the form closes.
- Retention: indefinite, no delete path (every foreign key is PROTECT).
- Nothing notifies respondents.

## Smallest decision needed for a working internal pilot
One question: may the form owner see which person gave which answer? Recommendation: yes, named responses, owner only, with the
form screen telling each respondent before they submit that the owner sees their name and answers. Reason: it matches how
Calendar and Tasks already show a named person to the owner, and anonymous responses need a separate design (no join from answer to
person, which also removes one-response-per-person unless done with a token). Public fill, retention, Sheets export and
notifications are not part of this decision and stay out of the first slice, not abandoned.

## UI
List (my forms, forms for me), editor (questions in order, move up and down, one page), fill page, results table with counts
and CSV download, status banners. Plain HTML forms.
Built: home with create, editor, owner Preview, results and CSV (no responses can exist while collection is off), and a fill page for a respondent. NOT built: a list of forms sent to me. Any signed-in active person can create a private draft: that is an implementation default, not an approved role policy. The fill page tells the respondent that the form's maker sees their name and answers; it is not reachable while collection is off.

## Tests planned
Owner and non-audience refusal with owner same-payload controls and full-row snapshots (the style of the Drive and Calendar
tests), one response per person under a repeated submit, required and type validation, closed and draft forms refuse fills,
results visible to the owner only, no file-type question exists.

## Later, owner-owned, not in the first slice
Public or anonymous fill; link sharing; retention and deletion; Sheets export; notifications. None are built.
