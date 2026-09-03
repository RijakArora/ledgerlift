"""Account scoping and sharing.

Every query must be scoped to the *account*, never to the individual
user. A user invited into an account sees exactly the same documents and
clients as the owner, and consumes the same quota - that is what makes
"priced per account, never per seat" true in the code rather than only
in the marketing copy.
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import User, Invite


def members(db: Session, account_id: int):
    """Everyone with access to an account: the owner plus anyone invited."""
    return (db.query(User)
            .filter((User.id == account_id) | (User.account_owner_id == account_id))
            .order_by(User.created_at)
            .all())


def owner_of(db: Session, account_id: int) -> User | None:
    return db.query(User).filter(User.id == account_id).first()


def create_invite(db: Session, account_id: int, email: str) -> Invite:
    invite = Invite(account_owner_id=account_id, email=(email or "").strip().lower(),
                    token=Invite.new_token())
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def usable_invite(db: Session, token: str) -> Invite | None:
    inv = db.query(Invite).filter(Invite.token == token).first()
    return inv if inv and inv.is_usable else None


def accept_invite(db: Session, invite: Invite, user: User) -> None:
    """Attach a user to the inviting account.

    Guards against an owner being absorbed into someone else's account,
    which would orphan their own data.
    """
    if user.id == invite.account_owner_id:
        return
    user.account_owner_id = invite.account_owner_id
    invite.accepted_at = datetime.utcnow()
    db.commit()


def revoke_member(db: Session, account_id: int, member_id: int) -> bool:
    """Remove someone's access. They keep their login and become the owner
    of their own (empty) account rather than being deleted - deleting a
    person's account because a colleague revoked sharing would be wrong.
    """
    m = db.query(User).filter(User.id == member_id,
                              User.account_owner_id == account_id).first()
    if not m:
        return False
    m.account_owner_id = None
    db.commit()
    return True
