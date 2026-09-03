import secrets
from datetime import datetime, timedelta

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.database import Base

# Monthly document quotas per plan. Flat-rate: the quota belongs to the
# account, never to a seat or a client. Inviting a colleague, a client or
# an accountant must never raise the bill - that is the whole wedge
# against per-seat incumbents, so it has to be true in the code, not just
# in the marketing copy.
PLAN_QUOTAS = {"free": 150, "low": 600, "mid": 2000}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, default="")
    plan = Column(String, default="free")
    created_at = Column(DateTime, default=datetime.utcnow)

    # NULL means this user owns their own account. Set means they were
    # invited into someone else's account and share all of its data and
    # its quota. Kept as a self-FK rather than a separate Account table
    # so existing single-user rows stay valid with no data migration.
    account_owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    @property
    def account_id(self) -> int:
        """The account whose data this user sees. Everything is scoped to
        this, never to user.id directly."""
        return self.account_owner_id or self.id

    @property
    def is_owner(self) -> bool:
        return self.account_owner_id is None

    @property
    def quota(self) -> int:
        return PLAN_QUOTAS.get(self.plan, PLAN_QUOTAS["free"])


class Invite(Base):
    """A pending invitation to share an account.

    Single-use and time-limited: an invite link that works forever is a
    standing key to someone's financial records.
    """
    __tablename__ = "invites"

    id = Column(Integer, primary_key=True)
    account_owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email = Column(String, default="")
    token = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime, nullable=True)

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() > (self.created_at or datetime.utcnow()) + timedelta(days=14)

    @property
    def is_usable(self) -> bool:
        return self.accepted_at is None and not self.is_expired


class PasswordReset(Base):
    """Single-use, short-lived password reset token."""
    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)

    @staticmethod
    def new_token() -> str:
        return secrets.token_urlsafe(32)

    @property
    def is_usable(self) -> bool:
        if self.used_at is not None:
            return False
        return datetime.utcnow() < (self.created_at or datetime.utcnow()) + timedelta(hours=2)


class Client(Base):
    """A set of books the account keeps. The target customer is a solo
    bookkeeper running several clients, who needs them kept apart both on
    screen and at export time."""
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # account id
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)



class Document(Base):
    """One uploaded invoice/receipt plus whatever was extracted from it.

    Failed extractions are stored too, with status + error, so a user can
    see the file arrived and why it didn't work rather than it vanishing.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    # This is the ACCOUNT id, not the individual uploader - so everyone
    # invited into an account sees the same documents.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True, index=True)
    filename = Column(String, default="")
    mime_type = Column(String, default="")
    uploaded_at = Column(DateTime, default=datetime.utcnow, index=True)

    status = Column(String, default="OK")          # OK | FAILED
    error_message = Column(String, default="")

    document_type = Column(String, default="")
    vendor_name = Column(String, default="")
    vendor_address = Column(String, default="")
    invoice_number = Column(String, default="")
    invoice_date = Column(String, default="")
    due_date = Column(String, default="")
    currency = Column(String, default="")
    subtotal = Column(Float, nullable=True)
    tax = Column(Float, nullable=True)
    shipping = Column(Float, nullable=True)
    total = Column(Float, nullable=True)

    raw_json = Column(Text, default="")

    # Set when a human corrects extracted values, so an edited record is
    # never mistaken for raw model output.
    edited_at = Column(DateTime, nullable=True)

    # Points at an earlier document with the same vendor + invoice number.
    # A warning, never a block: same-numbered invoices do legitimately
    # recur, and silently refusing an upload would be worse than flagging.
    duplicate_of_id = Column(Integer, ForeignKey("documents.id"), nullable=True)

    client = relationship("Client", backref="documents")
    line_items = relationship("LineItem", back_populates="document",
                              cascade="all, delete-orphan")

    @property
    def missing_key_fields(self) -> list:
        """Fields a bookkeeper needs that came back empty. Drives the
        'needs attention' list so they review a handful of documents
        rather than re-checking every one."""
        missing = []
        if not self.vendor_name:
            missing.append("vendor")
        if self.total is None:
            missing.append("total")
        if not self.invoice_date:
            missing.append("date")
        return missing


class LineItem(Base):
    __tablename__ = "line_items"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    description = Column(String, default="")
    quantity = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)

    document = relationship("Document", back_populates="line_items")
