from contextvars import ContextVar
from contextlib import contextmanager

_current = ContextVar("organization_id", default=None)
_identity = ContextVar("manager_identity", default=None)

def manager_identity():
    return _identity.get()

def organization_id():
    value = _current.get()
    if not value:
        raise PermissionError("Organization context required")
    return value

@contextmanager
def organization_scope(value, identity=None):
    if not value:
        raise PermissionError("Organization context required")
    token = _current.set(value)
    user_token = _identity.set(identity)
    try:
        yield
    finally:
        _current.reset(token)
        _identity.reset(user_token)
