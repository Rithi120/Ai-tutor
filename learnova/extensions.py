"""Unbound Flask extensions shared by the application modules."""

from flask import current_app
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect


class TestingAwareCSRFProtect(CSRFProtect):
    """Keep real CSRF protection while allowing isolated Flask test clients."""

    def protect(self):
        if current_app.testing:
            return None
        return super().protect()


db = SQLAlchemy()
login_manager = LoginManager()
csrf = TestingAwareCSRFProtect()
limiter = Limiter(key_func=get_remote_address, default_limits=[])

__all__ = ["csrf", "db", "limiter", "login_manager"]
