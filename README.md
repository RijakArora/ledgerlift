# Ledgerlift

AI-powered invoice and receipt data extraction for freelancers, solo
bookkeepers, and small businesses. Upload a PDF, scan, or phone photo -
get back vendor, dates, totals, tax, and line items, exportable as CSV.

Built per `ai_business_platform/generated_documents/…-invoice-and-receipt-data-extraction-us-v1-spec.md`.

## The wedge

Incumbents (Dext and similar) charge **per seat**, which specifically
punishes the target customer: a solo bookkeeper who wants to share access
with a couple of clients or their accountant sees the bill multiply. A
competitor's own marketing says it outright: *"Dext per-user pricing for
three reviewers would triple the bill."*

Ledgerlift's quota is **per account, not per seat**. Adding people never
raises the price.

## How extraction works

The uploaded file goes straight to Gemini as inline data - no separate
OCR engine, no PDF→image conversion step (Gemini accepts `application/pdf`
and common image types directly). Gemini's structured-output mode
(`responseSchema`) constrains the reply to a fixed JSON schema, so there's
no prose-parsing guesswork.

**Model chain, not a single model.** The entire product depends on this
one call succeeding, and a single model *will* be congested sometimes -
during the very first end-to-end test, `gemini-flash-latest` returned
503 "high demand" on three consecutive attempts while
`gemini-flash-lite-latest` answered fine. `GEMINI_MODEL_CHAIN` is tried in
order, with a retry and backoff per model before falling through.

Only `-latest`-style aliases are used. Pinned model names get retired
without warning (`gemini-2.5-flash` already 404s for our key, and two
pinned defaults broke elsewhere in this project).

## Honest behaviour

- Fields that aren't on the document come back **null and render as "—"**.
  The prompt explicitly forbids guessing. A blank is more useful to a
  bookkeeper than a confident wrong number.
- Failed extractions are **stored with their error**, not silently dropped,
  so a user can see the file arrived and why it didn't work.
- The raw extractor output is shown on each document page, so anything
  displayed can be checked against what the model actually returned.

## Running locally

```
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # then add your GEMINI_API_KEY
uvicorn app.main:app --reload
```
Then open http://127.0.0.1:8000 and use `sample_data/sample_invoice.png`
to try it (regenerate it any time with `python sample_data/make_sample.py`).

## Deliberately not in v1

- **US customers only.** EU/UK is deferred until GDPR work (Article 27
  representatives for both EU and UK, processor DPAs, and international
  transfer safeguards) is actually done - not bolted on after someone
  from London signs up.
- QuickBooks/Xero integration, a public API, inbound-email ingest, and
  mobile apps are all fast-follows.
- No formal compliance certifications (SOC 2 etc.) - premature with zero
  customers.

## Known limitation before real customer data

Free-tier hosting stores SQLite on ephemeral disk: **data does not survive
a redeploy** without a paid persistent disk or managed Postgres. Fine for
demos and pilots; fix before anyone's books depend on it.

## Billing (not yet wired)

Stripe is not viable here - it requires a registered company, and
"individual freelancers without company registration are rarely approved"
for Stripe India. The plan is a Merchant-of-Record (Paddle or Lemon
Squeezy), which acts as the legal seller and handles sales-tax
registration and remittance. **Their onboarding eligibility for an
individual founder still needs confirming directly with the platform**
before pricing is finalized.
