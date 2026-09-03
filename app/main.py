import json
import logging
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
from app.models import User, Document, LineItem, Client, Invite, PasswordReset  # noqa: E402
from app import accounts                               # noqa: E402
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


def _account_quota(db: Session, user) -> int:
    """Quota comes from the account owner's plan, never the member's own.

    An invited member's User row defaults to plan="free", so reading their
    plan would cap a paying account's colleague at the free limit.
    """
    if user.is_owner:
        return user.quota
    owner = accounts.owner_of(db, user.account_id)
    return owner.quota if owner else user.quota


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
        return RedirectResponse("/dashboard")
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
    return RedirectResponse("/dashboard", status_code=303)


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
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")


@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request, client_id: int | None = None, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    q = db.query(Document).filter(Document.user_id == user.account_id)
    active_client = None
    if client_id:
        active_client = db.query(Client).filter(
            Client.id == client_id, Client.user_id == user.account_id).first()
        if active_client:
            q = q.filter(Document.client_id == active_client.id)

    docs = q.order_by(Document.uploaded_at.desc()).limit(300).all()
    clients = db.query(Client).filter(Client.user_id == user.account_id).order_by(Client.name).all()
    return templates.TemplateResponse("documents.html", _ctx(
        request, user, documents=docs, clients=clients, active_client=active_client,
        used=_docs_this_month(db, user.account_id), quota=_account_quota(db, user)))


@app.get("/documents/{doc_id}", response_class=HTMLResponse)
def document_detail(doc_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    # Scoped by user_id as well as id: without this, any logged-in user
    # could read another account's documents by guessing an id.
    doc = db.query(Document).filter(Document.id == doc_id, Document.user_id == user.account_id).first()
    if not doc:
        return RedirectResponse("/documents")
    clients = db.query(Client).filter(Client.user_id == user.account_id).order_by(Client.name).all()
    return templates.TemplateResponse("document_detail.html",
                                      _ctx(request, user, doc=doc, clients=clients))


@app.get("/upload", response_class=HTMLResponse)
def upload_form(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    clients = db.query(Client).filter(Client.user_id == user.account_id).order_by(Client.name).all()
    return templates.TemplateResponse("upload.html", _ctx(
        request, user, clients=clients,
        used=_docs_this_month(db, user.account_id), quota=_account_quota(db, user)))


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
            Client.id == int(raw_client_id), Client.user_id == user.account_id).first()

    def render(**kw):
        return templates.TemplateResponse("upload.html", _ctx(
            request, user,
            clients=db.query(Client).filter(Client.user_id == user.account_id).order_by(Client.name).all(),
            used=_docs_this_month(db, user.account_id), quota=_account_quota(db, user), **kw))

    files = [f for f in files if f and f.filename]
    if not files:
        return render(error="No file selected.")

    used = _docs_this_month(db, user.account_id)
    remaining = _account_quota(db, user) - used
    if remaining <= 0:
        return render(error=("You have used all " + str(_account_quota(db, user)) + " documents on the "
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

        doc = Document(user_id=user.account_id, uploaded_by_id=user.id, filename=name, mime_type=mime,
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
        prior = _find_duplicate(db, user.account_id, doc.vendor_name, doc.invoice_number)
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
        Document.id == doc_id, Document.user_id == user.account_id).first()
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
    clients = db.query(Client).filter(Client.user_id == user.account_id).order_by(Client.name).all()
    counts = {}
    for c in clients:
        counts[c.id] = db.query(func.count(Document.id)).filter(
            Document.client_id == c.id).scalar() or 0
    unassigned = db.query(func.count(Document.id)).filter(
        Document.user_id == user.account_id, Document.client_id.is_(None)).scalar() or 0
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
            Client.user_id == user.account_id, Client.name == name).first()
        if not exists:
            db.add(Client(user_id=user.account_id, name=name))
            db.commit()
    return RedirectResponse("/clients", status_code=303)


@app.post("/documents/{doc_id}/assign")
async def assign_client(doc_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    doc = db.query(Document).filter(
        Document.id == doc_id, Document.user_id == user.account_id).first()
    if not doc:
        return RedirectResponse("/documents")
    form = await request.form()
    cid = form.get("client_id") or ""
    if cid:
        c = db.query(Client).filter(Client.id == int(cid), Client.user_id == user.account_id).first()
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

    q = db.query(Document).filter(Document.user_id == user.account_id, Document.status == "OK")
    label = "all"
    if client_id:
        c = db.query(Client).filter(Client.id == client_id, Client.user_id == user.account_id).first()
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


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    """The working view a bookkeeper opens each day.

    Built around one question - "what needs my attention?" - rather than
    vanity totals. Extraction is imperfect by design (blank beats a
    guess), so the useful thing is surfacing the handful of documents
    worth looking at instead of making someone re-check all of them.
    """
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")

    acct = user.account_id
    base = db.query(Document).filter(Document.user_id == acct)

    used = _docs_this_month(db, acct)
    quota = _account_quota(db, user)

    recent = base.order_by(Document.uploaded_at.desc()).limit(8).all()
    ok_docs = base.filter(Document.status == "OK").all()

    failed = base.filter(Document.status == "FAILED").order_by(
        Document.uploaded_at.desc()).limit(10).all()
    duplicates = [d for d in ok_docs if d.duplicate_of_id]
    incomplete = [d for d in ok_docs if d.missing_key_fields]

    # Totals by currency - summing mixed currencies would be nonsense.
    totals = {}
    for d in ok_docs:
        if d.total is None:
            continue
        cur = d.currency or "?"
        totals[cur] = totals.get(cur, 0.0) + d.total

    clients = db.query(Client).filter(Client.user_id == acct).order_by(Client.name).all()
    per_client = []
    for c in clients:
        docs = [d for d in ok_docs if d.client_id == c.id]
        c_totals = {}
        for d in docs:
            if d.total is not None:
                c_totals[d.currency or "?"] = c_totals.get(d.currency or "?", 0.0) + d.total
        per_client.append({"client": c, "count": len(docs), "totals": c_totals})
    unassigned_count = len([d for d in ok_docs if d.client_id is None])

    return templates.TemplateResponse("dashboard.html", _ctx(
        request, user,
        used=used, quota=quota,
        total_docs=len(ok_docs),
        recent=recent, failed=failed, duplicates=duplicates[:10],
        incomplete=incomplete[:10],
        totals=totals, per_client=per_client, unassigned_count=unassigned_count,
        team=accounts.members(db, acct)))


@app.get("/team", response_class=HTMLResponse)
def team_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    acct = user.account_id
    pending = (db.query(Invite)
               .filter(Invite.account_owner_id == acct, Invite.accepted_at.is_(None))
               .order_by(Invite.created_at.desc()).all())
    return templates.TemplateResponse("team.html", _ctx(
        request, user,
        members=accounts.members(db, acct),
        owner=accounts.owner_of(db, acct),
        pending=[i for i in pending if i.is_usable],
        base_url=str(request.base_url).rstrip("/")))


@app.post("/team/invite")
async def team_invite(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if not user.is_owner:
        # Only the account owner hands out access to their financial records.
        return RedirectResponse("/team", status_code=303)
    form = await request.form()
    accounts.create_invite(db, user.account_id, form.get("email") or "")
    return RedirectResponse("/team", status_code=303)


@app.post("/team/revoke")
async def team_revoke(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login")
    if not user.is_owner:
        return RedirectResponse("/team", status_code=303)
    form = await request.form()
    member_id = form.get("member_id")
    if member_id:
        accounts.revoke_member(db, user.account_id, int(member_id))
    return RedirectResponse("/team", status_code=303)


@app.get("/join/{token}", response_class=HTMLResponse)
def join_form(token: str, request: Request, db: Session = Depends(get_db)):
    invite = accounts.usable_invite(db, token)
    if not invite:
        return templates.TemplateResponse("join.html", _ctx(
            request, None, invalid=True))
    owner = accounts.owner_of(db, invite.account_owner_id)
    user = current_user(request, db)
    return templates.TemplateResponse("join.html", _ctx(
        request, user, token=token, invite=invite, owner=owner))


@app.post("/join/{token}", response_class=HTMLResponse)
async def join_accept(token: str, request: Request, db: Session = Depends(get_db)):
    invite = accounts.usable_invite(db, token)
    if not invite:
        return templates.TemplateResponse("join.html", _ctx(request, None, invalid=True))

    user = current_user(request, db)
    if user:
        # Already signed in - just attach this account.
        accounts.accept_invite(db, invite, user)
        return RedirectResponse("/dashboard", status_code=303)

    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    owner = accounts.owner_of(db, invite.account_owner_id)

    def bad(msg):
        return templates.TemplateResponse("join.html", _ctx(
            request, None, token=token, invite=invite, owner=owner, error=msg))

    if not email or len(password) < 8:
        return bad("Email and a password of at least 8 characters are required.")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        if not verify_password(password, existing.password_hash):
            return bad("An account with that email exists. Enter its password to join.")
        accounts.accept_invite(db, invite, existing)
        request.session["user_id"] = existing.id
        return RedirectResponse("/dashboard", status_code=303)

    new_user = User(email=email, password_hash=hash_password(password), name=name,
                    account_owner_id=invite.account_owner_id)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    invite.accepted_at = datetime.utcnow()
    db.commit()
    request.session["user_id"] = new_user.id
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_form(request: Request):
    return templates.TemplateResponse("forgot.html", _ctx(request, None))


@app.post("/forgot-password", response_class=HTMLResponse)
async def forgot_submit(request: Request, db: Session = Depends(get_db)):
    """Creates a reset token.

    There is no transactional email provider wired up yet, so the link
    cannot be emailed. Rather than pretend otherwise, the page says so
    plainly and tells the user to contact support - and the link is
    written to the server log so support can hand it over after checking
    who is asking. The same response is returned whether or not the
    address exists, so this cannot be used to discover who has an account.
    """
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user:
        pr = PasswordReset(user_id=user.id, token=PasswordReset.new_token())
        db.add(pr)
        db.commit()
        link = str(request.base_url).rstrip("/") + "/reset-password/" + pr.token
        logging.getLogger("ledgerlift.reset").warning(
            "PASSWORD RESET requested for %s -> %s", email, link)
    return templates.TemplateResponse("forgot.html", _ctx(request, None, submitted=True))


@app.get("/reset-password/{token}", response_class=HTMLResponse)
def reset_form(token: str, request: Request, db: Session = Depends(get_db)):
    pr = db.query(PasswordReset).filter(PasswordReset.token == token).first()
    if not pr or not pr.is_usable:
        return templates.TemplateResponse("reset.html", _ctx(request, None, invalid=True))
    return templates.TemplateResponse("reset.html", _ctx(request, None, token=token))


@app.post("/reset-password/{token}", response_class=HTMLResponse)
async def reset_submit(token: str, request: Request, db: Session = Depends(get_db)):
    pr = db.query(PasswordReset).filter(PasswordReset.token == token).first()
    if not pr or not pr.is_usable:
        return templates.TemplateResponse("reset.html", _ctx(request, None, invalid=True))
    form = await request.form()
    password = form.get("password") or ""
    if len(password) < 8:
        return templates.TemplateResponse("reset.html", _ctx(
            request, None, token=token, error="Password must be at least 8 characters."))
    user = db.query(User).filter(User.id == pr.user_id).first()
    if not user:
        return templates.TemplateResponse("reset.html", _ctx(request, None, invalid=True))
    user.password_hash = hash_password(password)
    pr.used_at = datetime.utcnow()
    db.commit()
    request.session["user_id"] = user.id
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/sample-invoice.png")
def sample_invoice():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_data", "sample_invoice.png")
    return FileResponse(path, media_type="image/png", filename="sample_invoice.png")
