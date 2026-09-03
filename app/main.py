import json
import os
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from app.database import Base, engine, get_db          # noqa: E402
from app.models import User, Document, LineItem        # noqa: E402
from app.auth import hash_password, verify_password, current_user  # noqa: E402
from app.extract import extract_document, ExtractionError, SUPPORTED_MIME  # noqa: E402
from app.export import documents_to_csv                # noqa: E402

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ledgerlift")

# Must be set in production. The fallback exists so local dev runs without
# setup, and is deliberately obvious if it ever leaks into a deploy.
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

templates = Jinja2Templates(directory="app/templates")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB

# Identity used in the legal pages. Paddle's domain review requires the
# company or sole proprietor's brand in the Terms, and working buyer
# support contact details. These deliberately default to obvious
# placeholders rather than invented values - a fake support address would
# pass review and then fail a real customer.
def _env(key: str, default: str) -> str:
    """os.environ.get() returns "" for a var that's present but blank,
    which silently defeats the placeholder defaults below - the legal
    pages then render with no name and no support address at all. Treat
    blank as unset so a missing value is loudly visible instead."""
    return (os.environ.get(key) or "").strip() or default


SITE = {
    "brand": _env("BRAND_NAME", "Ledgerlift"),
    "legal_name": _env("LEGAL_NAME", "[SET LEGAL_NAME - your full name as sole proprietor]"),
    "support_email": _env("SUPPORT_EMAIL", "[SET SUPPORT_EMAIL]"),
    "jurisdiction": _env("JURISDICTION", "India"),
    "updated": "3 September 2026",
    # Drives the privacy policy's third-party processing disclosure, so it
    # stays true to the tier actually in use. On Google's unpaid tier,
    # Google's API terms state it "uses the content you submit... to
    # provide, improve, and develop Google products" and that "human
    # reviewers may read, annotate, and process your API input and
    # output". On the paid tier Google does not use prompts or responses
    # to improve its products. Defaults to False = the honest disclosure.
    # Flip only once billing is genuinely enabled on the API key.
    "gemini_paid_tier": os.environ.get("GEMINI_PAID_TIER", "").lower() in ("1", "true", "yes"),
}


def _docs_this_month(db: Session, user_id: int) -> int:
    start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(func.count(Document.id))
        .filter(Document.user_id == user_id, Document.uploaded_at >= start)
        .scalar()
    ) or 0


def _ctx(request: Request, user, **kw) -> dict:
    return {"request": request, "user": user, "site": SITE, **kw}


@app.get("/health")
def health():
    return {"status": "ok"}


def _sample_extraction() -> dict | None:
    """The extraction shown on the landing page is a real run of this
    system against sample_data/sample_invoice.png, saved to disk - not a
    mockup and not hand-written numbers. Regenerate it by re-running
    extraction on the sample if the output format ever changes."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "sample_data", "sample_extraction.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None  # landing page simply omits the proof section


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if user:
        return RedirectResponse("/documents")
    return templates.TemplateResponse("landing.html", _ctx(request, None, proof=_sample_extraction()))


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("terms.html", _ctx(request, current_user(request, db)))


@app.get("/refund-policy", response_class=HTMLResponse)
def refund_policy(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("refund.html", _ctx(request, current_user(request, db)))


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("privacy.html", _ctx(request, current_user(request, db)))


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse("signup.html", _ctx(request, None))


@app.post("/signup", response_class=HTMLResponse)
async def signup(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""

    if not email or len(password) < 8:
        return templates.TemplateResponse("signup.html", _ctx(
            request, None, error="Email and a password of at least 8 characters are required."))
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse("signup.html", _ctx(
            request, None, error="An account with that email already exists."))

    user = User(email=email, password_hash=hash_password(password), name=name)
    db.add(user)
    db.commit()
    db.refresh(user)
    request.session["user_id"] = user.id
    return RedirectResponse("/documents", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", _ctx(request, None))


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", _ctx(
            request, None, error="Incorrect email or password."))
    request.session["user_id"] = user.id
    return RedirectResponse("/documents", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    docs = (
        db.query(Document).filter(Document.user_id == user.id)
        .order_by(Document.uploaded_at.desc()).limit(200).all()
    )
    return templates.TemplateResponse("documents.html", _ctx(
        request, user, documents=docs,
        used=_docs_this_month(db, user.id), quota=user.quota))


@app.get("/documents/{doc_id}", response_class=HTMLResponse)
def document_detail(doc_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    # Scoped by user_id as well as id: without this, any logged-in user
    # could read another account's documents by guessing an id.
    doc = db.query(Document).filter(Document.id == doc_id, Document.user_id == user.id).first()
    if not doc:
        return RedirectResponse("/documents")
    return templates.TemplateResponse("document_detail.html", _ctx(request, user, doc=doc))


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("upload.html", _ctx(
        request, user, used=_docs_this_month(db, user.id), quota=user.quota))


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    def render(**kw):
        return templates.TemplateResponse("upload.html", _ctx(
            request, user, used=_docs_this_month(db, user.id), quota=user.quota, **kw))

    used = _docs_this_month(db, user.id)
    if used >= user.quota:
        return render(error=(
            f"You've used all {user.quota} documents on the {user.plan} plan this month. "
            "Upgrade to keep processing documents."))

    raw = await file.read()
    if not raw:
        return render(error="That file appears to be empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        return render(error=f"File is too large ({len(raw)//1024//1024} MB). Maximum is 15 MB.")

    mime = (file.content_type or "").lower()
    if mime not in SUPPORTED_MIME and mime != "image/jpg":
        return render(error=f"Unsupported file type '{mime or 'unknown'}'. Upload a PDF, PNG, JPG, or WEBP.")

    doc = Document(user_id=user.id, filename=file.filename or "upload", mime_type=mime)

    try:
        data = extract_document(raw, mime)
    except ExtractionError as e:
        # Record the failure rather than dropping it - the user should see
        # the file arrived and why it didn't work.
        doc.status = "FAILED"
        doc.error_message = str(e)
        db.add(doc)
        db.commit()
        return render(error=str(e))

    doc.status = "OK"
    doc.document_type = data.get("document_type") or ""
    doc.vendor_name = data.get("vendor_name") or ""
    doc.vendor_address = data.get("vendor_address") or ""
    doc.invoice_number = data.get("invoice_number") or ""
    doc.invoice_date = data.get("invoice_date") or ""
    doc.due_date = data.get("due_date") or ""
    doc.currency = data.get("currency") or ""
    doc.subtotal = data.get("subtotal")
    doc.tax = data.get("tax")
    doc.shipping = data.get("shipping")
    doc.total = data.get("total")
    doc.raw_json = json.dumps(data, indent=2)
    db.add(doc)
    db.commit()
    db.refresh(doc)

    for item in (data.get("line_items") or []):
        db.add(LineItem(
            document_id=doc.id,
            description=item.get("description") or "",
            quantity=item.get("quantity"),
            unit_price=item.get("unit_price"),
            amount=item.get("amount"),
        ))
    db.commit()

    return RedirectResponse(f"/documents/{doc.id}", status_code=303)


@app.get("/export.csv")
def export_csv(request: Request, shape: str = "summary", db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    docs = (
        db.query(Document)
        .filter(Document.user_id == user.id, Document.status == "OK")
        .order_by(Document.uploaded_at.desc()).all()
    )
    csv_text = documents_to_csv(docs, shape="line_items" if shape == "line_items" else "summary")
    filename = f"export-{shape}-{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return PlainTextResponse(csv_text, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/sample-invoice.png")
def sample_invoice():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_data", "sample_invoice.png")
    return FileResponse(path, media_type="image/png", filename="sample_invoice.png")
