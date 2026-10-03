import base64
from collections import OrderedDict
from datetime import timedelta
import hashlib
import secrets
from threading import BoundedSemaphore, Lock
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select, update

from .models import LoginSession, User, utcnow

# Bound password-hashing memory when many clients log in together (~128 MiB each).
PASSWORD_WORKERS = BoundedSemaphore(2)
SUPPORTED_ROLES = {"admin", "registrar", "doctor", "reviewer"}


def hash_password(password: str) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise ValueError("密码长度必须为12～1024个字符")
    salt = secrets.token_bytes(16)
    with PASSWORD_WORKERS:
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=131072, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32)
    return "scrypt$131072$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt" or int(n) != 131072 or int(r) != 8 or int(p) != 1:
            return False
        with PASSWORD_WORKERS:
            actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=256 * 1024 * 1024, dklen=32)
        return secrets.compare_digest(actual, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session(user: User, hours: int):
    token = secrets.token_urlsafe(32)
    record = LoginSession(token_hash=token_hash(token), user_id=user.id, csrf_token=secrets.token_urlsafe(32), expires_at=utcnow() + timedelta(hours=hours))
    return token, record


def get_db(request: Request):
    with request.app.state.Session() as session:
        yield session


def current_user(request: Request, db=Depends(get_db)) -> User:
    token = request.cookies.get(request.app.state.settings.session_cookie)
    record = db.get(LoginSession, token_hash(token)) if token and len(token) <= 128 else None
    if record is None or record.expires_at <= utcnow() or record.user is None or not record.user.is_active:
        raise HTTPException(401, "登录已失效，请重新登录")
    if record.user.role not in SUPPORTED_ROLES:
        raise HTTPException(403, "账号角色配置无效，请联系管理员")
    request.state.login_session = record
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        provided = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(provided, record.csrf_token):
            raise HTTPException(403, "安全校验失败，请刷新页面后重试")
    return record.user


def roles(*allowed: str):
    def check(user: User = Depends(current_user)):
        if user.role not in allowed:
            raise HTTPException(403, "您没有权限执行此操作")
        return user
    return check


def lock_data_anchor(db):
    # Account and foundation APIs never delete users. Use a non-key write so
    # PostgreSQL FK KEY SHARE locks remain compatible with the shared anchor.
    anchor = db.scalar(select(func.min(User.id)))
    if anchor is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    claim = db.execute(update(User).where(User.id == anchor).values(display_name=User.display_name).execution_options(synchronize_session=False))
    if claim.rowcount != 1:
        raise HTTPException(409, "系统资料已变化，请刷新后重试")


def lock_and_recheck(request, db, *allowed):
    lock_data_anchor(db)
    db.expire_all()
    actor = current_user(request, db)
    if actor.role not in allowed:
        raise HTTPException(403, "您没有权限执行此操作")
    return actor


class LoginThrottle:
    """Single-server bounded login limiter; reverse proxy must add a global limit in production."""
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()

    def check(self, address: str, username: str):
        now = time.monotonic()
        # IP limit also catches username rotation; no proxy-provided IP is trusted here.
        keys = [("address", address), ("account", username.casefold())]
        with self.lock:
            for key in keys:
                self.entries[key] = [t for t in self.entries.get(key, []) if now - t < 300]
                limit = 30 if key[0] == "address" else 10
                if len(self.entries[key]) >= limit:
                    raise HTTPException(429, "登录尝试过于频繁，请5分钟后再试")
            for key in keys:
                self.entries[key].append(now)
                self.entries.move_to_end(key)
            while len(self.entries) > 20000:
                self.entries.popitem(last=False)


def user_json(user: User) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role, "department_id": user.department_id}
