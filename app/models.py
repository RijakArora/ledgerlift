from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship

from app.database import Base

# Monthly document quotas per plan. Flat-rate: the quota is per account,
# never per seat - that's the deliberate wedge against per-seat incumbents
# (see the v1 spec). Adding a colleague or client must never raise the bill.
PLAN_QUOTAS = {
    "free": 150,
    "low": 600,
    "mid": 2000,
}


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    name = Column(String, default="")
    plan = Column(String, default="free")
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="user")

    @property
    def quota(self) -> int:
        return PLAN_QUOTAS.get(self.plan, PLAN_QUOTAS["free"])


class Document(Base):
    """One uploaded invoice/receipt plus whatever was extracted from it.

    Failed extractions are stored too, with status + error, so a user can
    see that a file was received and why it didn't work - rather than the
    upload silently vanishing.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
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

    user = relationship("User", back_populates="documents")
    line_items = relationship("LineItem", back_populates="document", cascade="all, delete-orphan")


class LineItem(Base):
    __tablename__ = "line_items"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    description = Column(String, default="")
    quantity = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)

    document = relationship("Document", back_populates="line_items")
