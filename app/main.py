import json
import os
from datetime import datetime

from dotenv import load_dotenv
from typing import List

from fastapi import FastAPI, Depends, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from app.database import Base, engine, get_db          # noqa: E402
from app.migrate import ensure_schema                  # noqa: E402
from app.models import User, Document, LineItem, Client  # noqa: E402
from app.auth import hash_password, verify_password, current_user  # noqa: E402
from app.extract import extract_document, ExtractionError, SUPPORTED_MIME  # noqa: E402
from app.export import documents_to_csv                # noqa: E402

Base.metadata.create_all(bind=engine)
# Adds columns introduced after a table already existed - create_all()
# alone would leave production silently missing them.
ensure_schema(engine)

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
def documents(request: Request, client_id: int | None = None, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    q = db.query(Document).filter(Document.user_id == user.id)
    active_client = None
    if client_id:
        active_client = db.query(Client).filter(
            Client.id == client_id, Client.user_id == user.id).first()
        if active_client:
            q = q.filter(Document.client_id == active_client.id)

    docs = q.order_by(Document.uploaded_at.desc()).limit(300).all()
    clients = db.query(Client).filter(Client.user_id == user.id).order_by(Client.name).all()
    return templates.TemplateResponse("documents.html", _ctx(
        request, user, documents=docs, clients=clients, active_client=active_client,
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
    clients = db.query(Client).filter(Client.user_id == user.id).order_by(Client.name).all()
    return templates.TemplateResponse("document_detail.html",
                                      _ctx(request, user, doc=doc, clients=clients))


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    clients = db.query(Client).filter(Client.user_id == user.id).order_by(Client.name).all()
    return templates.TemplateResponse("upload.html", _ctx(
        request, user, clients=clients,
        used=_docs_this_month(db, user.id), quota=user.quota))


def _find_duplicate(db: Session, user_id: int, vendor: str, invoice_no: str):
    """A prior document from the same vendor with the same invoice number.

    Returned as a warning, never a block. Re-uploading a corrected scan of
    the same invoice is normal, and refusing the upload outright would be
    more annoying than a flag the user can simply ignore.
    """
    if not vendor or not invoice_no:
        return None
    return (db.query(Document)
            .filter(Document.user_id == user_id,
                    Document.status == "OK",
                    Document.vendor_name == vendor,
                    Document.invoice_number == invoice_no)
            .order_by(Document.uploaded_at.asc())
            .first())


def _persist_extraction(doc: Document, data: dict) -> None:
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


@app.post("/upload", response_class=HTMLResponse)
async def upload(request: Request, files: List[UploadFile] = File(...),
                 db: Session = Depends(get_db)):
    """Handles one or many files in a single submission.

    Each file is processed independently: one bad file in a batch of
    thirty must not discard the twenty-nine that worked, so failures are
    recorded per-document and reported in a summary rather than aborting.
    """
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    form = await request.form()
    raw_client_id = form.get("client_id") or ""
    client = None
    if raw_client_id:
        client = db.query(Client).filter(
            Client.id == int(raw_client_id), Client.user_id == user.id).first()

    def render(**kw):
        return templates.TemplateResponse("upload.html", _ctx(
            request, user,
            clients=db.query(Client).filter(Client.user_id == user.id).order_by(Client.name).all(),
            used=_docs_this_month(db, user.id), quota=user.quota, **kw))

    files = [f for f in files if f and f.filename]
    if not files:
        return render(error="No file selected.")

    used = _docs_this_month(db, user.id)
    remaining = user.quota - used
    if remaining <= 0:
        return render(error=("You have used all " + str(user.quota) + " documents on the "
                             + user.plan + " plan this month. Upgrade to keep going."))

    truncated = False
    if len(files) > remaining:
        files = files[:remaining]
        truncated = True

    ok = 0
    dupes = 0
    failed = []
    first_id = None

    for f in files:
        raw = await f.read()
        name = f.filename or "upload"
        mime = (f.content_type or "").lower()

        if not raw:
            failed.append(name + ": file is empty")
            continue
        if len(raw) > MAX_UPLOAD_BYTES:
            failed.append(name + ": larger than 15 MB")
            continue
        if mime not in SUPPORTED_MIME and mime != "image/jpg":
            failed.append(name + ": unsupported type " + (mime or "unknown"))
            continue

        doc = Document(user_id=user.id, filename=name, mime_type=mime,
                       client_id=client.id if client else None)
        try:
            data = extract_document(raw, mime)
        except ExtractionError as e:
            doc.status = "FAILED"
            doc.error_message = str(e)
            db.add(doc)
            db.commit()
            failed.append(name + ": " + str(e))
            continue

        _persist_extraction(doc, data)
        prior = _find_duplicate(db, user.id, doc.vendor_name, doc.invoice_number)
        if prior:
            doc.duplicate_of_id = prior.id
            dupes += 1
        db.add(doc)
        db.commit()
        db.refresh(doc)

        for item in (data.get("line_items") or []):
            db.add(LineItem(document_id=doc.id,
                            description=item.get("description") or "",
                            quantity=item.get("quantity"),
                            unit_price=item.get("unit_price"),
                            amount=item.get("amount")))
        db.commit()
        ok += 1
        if first_id is None:
            first_id = doc.id

    # A clean single upload goes straight to the document, as before.
    if ok == 1 and not failed and not dupes:
        return RedirectResponse("/documents/" + str(first_id), status_code=303)

    parts = []
    if ok:
        parts.append(str(ok) + " document" + ("s" if ok != 1 else "") + " processed")
    if dupes:
        parts.append(str(dupes) + " flagged as a possible duplicate")
    if truncated:
        parts.append("some files skipped - monthly quota reached")
    return render(ok=", ".join(parts) if parts else None, failures=failed)


@app.post("/documents/{doc_id}/edit")
async def edit_document(doc_id: int, request: Request, db: Session = Depends(get_db)):
    """Lets a human correct extracted values.

    Without this, telling users to "review before posting to your books"
    is hollow: finding an error would mean falling back to manual entry,
    which defeats the point of the tool. Corrections stamp edited_at so an
    edited record is never mistaken for raw model output.
    """
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    doc = db.query(Document).filter(
        Document.id == doc_id, Document.user_id == user.id).first()
    if not doc:
        return RedirectResponse("/documents")

    form = await request.form()

    def num(key, current):
        v = (form.get(key) or "").strip().replace(",", "")
        if v == "":
            return None
        try:
            return float(v)
        except ValueError:
            return current  # keep the old value rather than destroying it

    doc.vendor_name = (form.get("vendor_name") or "").strip()
    doc.invoice_number = (form.get("invoice_number") or "").strip()
    doc.invoice_date = (form.get("invoice_date") or "").strip()
    doc.due_date = (form.get("due_date") or "").strip()
    doc.currency = (form.get("currency") or "").strip()
    doc.subtotal = num("subtotal", doc.subtotal)
    doc.tax = num("tax", doc.tax)
    doc.shipping = num("shipping", doc.shipping)
    doc.total = num("total", doc.total)

    for item in doc.line_items:
        desc = form.get("item_" + str(item.id) + "_description")
        if desc is None:
            continue
        item.description = desc.strip()
        item.quantity = num("item_" + str(item.id) + "_quantity", item.quantity)
        item.unit_price = num("item_" + str(item.id) + "_unit_price", item.unit_price)
        item.amount = num("item_" + str(item.id) + "_amount", item.amount)

    doc.edited_at = datetime.utcnow()
    db.commit()
    return RedirectResponse("/documents/" + str(doc.id), status_code=303)


@app.get("/clients", response_class=HTMLResponse)
def clients_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    clients = db.query(Client).filter(Client.user_id == user.id).order_by(Client.name).all()
    counts = {}
    for c in clients:
        counts[c.id] = db.query(func.count(Document.id)).filter(
            Document.client_id == c.id).scalar() or 0
    unassigned = db.query(func.count(Document.id)).filter(
        Document.user_id == user.id, Document.client_id.is_(None)).scalar() or 0
    return templates.TemplateResponse("clients.html", _ctx(
        request, user, clients=clients, counts=counts, unassigned=unassigned))


@app.post("/clients")
async def create_client(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    form = await request.form()
    name = (form.get("name") or "").strip()
    if name:
        exists = db.query(Client).filter(
            Client.user_id == user.id, Client.name == name).first()
        if not exists:
            db.add(Client(user_id=user.id, name=name))
            db.commit()
    return RedirectResponse("/clients", status_code=303)


@app.post("/documents/{doc_id}/assign")
async def assign_client(doc_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    doc = db.query(Document).filter(
        Document.id == doc_id, Document.user_id == user.id).first()
    if not doc:
        return RedirectResponse("/documents")
    form = await request.form()
    cid = form.get("client_id") or ""
    if cid:
        c = db.query(Client).filter(Client.id == int(cid), Client.user_id == user.id).first()
        doc.client_id = c.id if c else None
    else:
        doc.client_id = None
    db.commit()
    return RedirectResponse("/documents/" + str(doc.id), status_code=303)


@app.get("/export.csv")
def export_csv(request: Request, shape: str = "summary", client_id: int | None = None,
               db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    q = db.query(Document).filter(Document.user_id == user.id, Document.status == "OK")
    label = "all"
    if client_id:
        c = db.query(Client).filter(Client.id == client_id, Client.user_id == user.id).first()
        if c:
            q = q.filter(Document.client_id == c.id)
            label = "".join(ch if ch.isalnum() else "-" for ch in c.name).strip("-").lower() or "client"

    docs = q.order_by(Document.uploaded_at.desc()).all()
    if shape not in ("summary", "line_items", "xero", "quickbooks"):
        shape = "summary"
    csv_text = documents_to_csv(docs, shape=shape)
    fn = "ledgerlift-" + label + "-" + shape + "-" + datetime.utcnow().strftime("%Y%m%d") + ".csv"
    return PlainTextResponse(csv_text, media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="' + fn + '"'})


@app.get("/sample-invoice.png")
def sample_invoice():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_data", "sample_invoice.png")
    return FileResponse(path, media_type="image/png", filename="sample_invoice.png")
