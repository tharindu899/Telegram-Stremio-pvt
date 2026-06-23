from fastapi import HTTPException, Request
from fastapi.security import HTTPBearer
from Backend.config import Telegram
from typing import Optional
import hashlib
import hmac

security = HTTPBearer(auto_error=False)


def _password_hash(password: str) -> str:
    return hashlib.sha256(str(password or "").encode()).hexdigest()


def verify_password(password: str) -> bool:
    """Use the current runtime password so WebUI changes take effect live."""
    return hmac.compare_digest(_password_hash(password), _password_hash(Telegram.ADMIN_PASSWORD))


def verify_credentials(username: str, password: str) -> bool:
    return hmac.compare_digest(str(username or ""), str(Telegram.ADMIN_USERNAME or "")) and verify_password(password)


def is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated", False)


def require_auth(request: Request):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return True


def get_current_user(request: Request) -> Optional[str]:
    if is_authenticated(request):
        return request.session.get("username")
    return None
