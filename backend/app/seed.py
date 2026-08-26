from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import User
from .security import hash_password


def seed_admin(db: Session) -> None:
    settings = get_settings()
    if db.execute(select(User).where(User.username == settings.admin_username)).scalar_one_or_none():
        return
    db.add(User(username=settings.admin_username, password_hash=hash_password(settings.admin_password)))
