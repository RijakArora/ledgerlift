# Ledgerlift: Project Report

**Repository:** rijakarora/ledgerlift
**Report date:** 2026-11-16
**Basis:** a read of the source code, the README and the full commit history. The app was not run for this report, so behaviour described below comes from the code and commit messages, not from a live test.

---

## 1. Summary

Ledgerlift is a web app that extracts structured data from invoices and receipts. A user uploads a PDF, a scan or a phone photo. The app returns vendor, invoice number, dates, currency, subtotal, tax, shipping, total and line items, and can export them as CSV, including shapes for Xero and QuickBooks.

It targets freelancers, solo bookkeepers and small businesses in the US. Its competitive angle is pricing: the quota belongs to the **account, not the seat**. Adding clients, colleagues or an accountant never raises the price. The README says per-seat tools such as Dext penalise exactly this kind of customer.

**Status:** a working MVP built in one day (2026-09-03, 11 commits). It is live at `ledgerlift.rijakarora.in`, on a free tier, with no billing, in pilot-recruitment mode.

---

## 2. What was built

### Core product
| Area | What it does |
|---|---|
| Extraction | Sends the uploaded file straight to Google Gemini as inline data. There is no separate OCR step and no PDF-to-image conversion. Output is constrained to a fixed JSON schema (`responseSchema`), so no prose parsing is needed. Accepts PDF, PNG, JPEG and WebP. |
| Model resilience | Tries an ordered chain of models (`gemini-flash-latest`, `gemini-flash-lite-latest`, `gemini-3.5-flash`), with a retry and backoff on each before moving to the next. This came from a 503 "high demand" seen on the first end-to-end test. Only `-latest` aliases are used because pinned model names get retired. |
| Honest output | Fields not on the document come back null and display as "—". Failed extractions are stored with their error, not dropped. The raw model output is shown on each document page. |
| Bulk upload | Several files at once. Each is processed independently, so one failure doesn't lose the rest. Failures are reported per file. |
| Editing | Users can correct extracted values. An `edited_at` stamp stops a corrected record being mistaken for raw model output. |
| Clients | Documents can be assigned to a client, filtered by client and exported per client. |
| Exports | Four CSV shapes: summary, line items, Xero bills and QuickBooks bills. Account codes and tax types are left blank on purpose, since only the bookkeeper knows their chart of accounts. |
| Duplicate flags | Detection on vendor plus invoice number, scoped to the whole account. It is a warning, never a block. |
| Dashboard | Built around "what needs attention": failed extractions, possible duplicates and documents missing key fields. Totals are grouped by currency. |

### Accounts and sharing
- **Account sharing.** `User.account_owner_id` is a self-reference. If it is NULL the user owns their account. If it is set, the user was invited in and shares that account's data and quota. Every query is scoped to `account_id`, not `user.id`.
- **Invites.** Single-use links that expire in 14 days. Only the owner can invite or revoke. Revoking returns the person to their own empty account.
- **Password reset.** Single-use tokens that expire in 2 hours. The response is identical whether or not the email exists, so it can't be used to find accounts. There is no email provider yet. The reset link is logged server-side so support can pass it on after verifying the person.
- **Auth.** bcrypt password hashes and signed session cookies (`itsdangerous`).

### Front end and presentation
- Jinja2 server-rendered templates. There is no JavaScript framework.
- Light and dark themes. Each has its own colour tokens. The theme is set before first paint to avoid a flash, follows the OS by default and remembers an explicit choice.
- Drag-and-drop upload that feeds the real file input, so the plain form POST still does the work.
- Landing page with a static "real extraction" proof section, plus Terms, Refund Policy and Privacy Policy pages. These are templated from `LEGAL_NAME`, `SUPPORT_EMAIL` and similar env vars.
- Motion is disabled under `prefers-reduced-motion`.

### Go-to-market tooling
- `PILOT_OUTREACH_DRAFTS.md` has first-person outreach drafts for pilot users. They are drafts only, and nothing has been sent.
- `scripts/daily_cycle.py` is a daily research and outreach-drafting job that writes a digest for human review. By design it does not send anything and does not collect individuals' contact details. PowerShell scripts register and unregister it as a Windows scheduled task.

---

## 3. Architecture

| Layer | Choice |
|---|---|
| Web framework | FastAPI 0.115, served by Uvicorn |
| Templates | Jinja2 (server-rendered) |
| Database | SQLAlchemy 2.0. SQLite by default for local dev, Postgres via `psycopg` in production. `database.py` normalises `postgres://` URLs and enables `pool_pre_ping` and `pool_recycle`. |
| Migrations | A small hand-written `app/migrate.py`, because `create_all()` never alters existing tables. There is no Alembic. |
| AI | Gemini REST API via `httpx` |
| Deploy | Docker image, `render.yaml` for Render (free plan, `/health` check) |

**Size:** about 3,650 lines in total. Of that, `app/main.py` is 724 lines with about 28 routes, `base.html` (the shared layout and CSS) is 389, and `extract.py` is 196.

**Data model:** `User`, `Invite`, `PasswordReset`, `Client` and `Document`. The document row holds the extracted fields, line items, status and error, edited flag and duplicate flag.

---

## 4. Timeline (all 2026-09-03, IST)

| Time | Commit |
|---|---|
| 08:23 | Initial MVP: invoice/receipt extraction |
| 08:34 | Landing page, Terms, Refund Policy, Privacy Policy |
| 08:43 to 08:50 | Pilot outreach drafts, then daily research and outreach automation |
| 09:01 to 09:28 | UI redesign, proof section, custom domain |
| 17:31 | Moved to Postgres after accounts were lost on a redeploy |
| 17:52 | Bulk upload, editing, clients, accounting exports, duplicate flags |
| 18:47 | Account sharing, password reset, dashboard |
| 19:27 | Dark mode, drag-and-drop upload, polish |

Several commit messages record bugs found and fixed along the way. Examples are duplicate detection missing a colleague's upload, invited members being capped at the free quota, and a template script that Jinja silently discarded.

---

## 5. Risks and open issues

Ordered by how soon they matter.

1. **The Render free Postgres may have expired.** The Postgres commit notes that the free database carries `expiresAt 2026-10-03` (30 days). Today is 2026-11-16, so it is past that date. If it was not migrated to a persistent tier such as Neon or Supabase, the live app may have lost its data or be failing. Check this first.
2. **User documents go to Google's unpaid Gemini tier.** `GEMINI_PAID_TIER` defaults to `false`. On that tier Google may use submitted content to improve its products and human reviewers may read it. The outreach drafts already warn pilot users, but this must change before real client invoices go in. The privacy policy follows this flag, so the flag must match reality.
3. **Billing is not wired.** Plans `free` (150 docs), `low` (600) and `mid` (2000) exist in code, but nothing charges for them. Stripe is not viable for an unregistered individual in India. The plan is a Merchant of Record (Paddle or Lemon Squeezy), and eligibility for an individual founder still has to be confirmed with the platform.
4. **Xero and QuickBooks headers are unverified.** The export code states that their import templates could not be checked at build time. Compare the headers against a template from a real account before relying on them.
5. **There is no transactional email.** Password reset depends on manual support. Invites are shared as links by hand.
6. **There are no automated tests.** The repo has no test files. The commit messages describe manual end-to-end checks, but nothing guards against regressions.
7. **`scripts/daily_cycle.py` is not portable.** It hard-codes a local Windows path to a separate `ai_business_platform` project and depends on that project's API. It will not run anywhere else as it stands.
8. **The README is partly stale.** It still lists "SQLite on ephemeral disk" as a known limitation, though Postgres has since replaced it. It also does not mention sharing, clients, bulk upload or editing.
9. **No formal compliance work.** SOC 2 and similar certifications are deferred on purpose. EU/UK customers are excluded until GDPR work is done. The service is US-only for now.
10. **Smaller technical gaps.** There is no Alembic, so schema changes rely on the hand-written migrator. Extraction accuracy has not been measured against a labelled set. No rate limiting or abuse controls are visible in the code I read.

---

## 6. Recommended next steps

1. Confirm the production database is alive and on a persistent tier. Move it if it isn't.
2. Decide on the Gemini paid tier before any pilot user uploads real documents, then set `GEMINI_PAID_TIER=true`.
3. Add a minimal test suite covering extraction parsing, account scoping, quota and invite and reset token expiry.
4. Verify the Xero and QuickBooks headers against real import templates.
5. Wire in a transactional email provider for invites and password reset.
6. Confirm Paddle or Lemon Squeezy eligibility, then connect billing to the existing plan quotas.
7. Measure extraction accuracy on a sample of real, redacted invoices, so claims to pilot users rest on a number.
8. Update the README and make the daily-cycle script configurable instead of hard-coded.

---

## 7. Provenance

The git author on all 11 commits is Rijak Arora. Several commit messages also carry `Co-Authored-By: Claude Opus 5`, and three carry a `Claude-Session` link to an earlier session. That means the project was built with AI assistance. This report does not attempt to say which lines came from whom.
