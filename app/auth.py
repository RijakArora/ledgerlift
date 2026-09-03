"""Session-cookie auth. Passwords hashed with bcrypt directly, not via
passlib - passlib 1.7.4 is incompatible with modern bcrypt releases and
crashes on hash(). That bug cost real debugging time on the previous
product in this project; not repeating it here."""
import bcrypt
from fastapi import Request
from sqlalchemy.orm import Session

from app.models import User

_MAX_PASSWORD_BYTES = 72  # bcrypt's hard input limit


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:_MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_MAX_PASSWORD_BYTES], password_hash.encode("utf-8"))
    except ValueError:
        return False


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    return db.query(User).filter(User.id == user_id).first() if user_id else None
