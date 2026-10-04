import time
from email.utils import parsedate_to_datetime

class GuestyError(RuntimeError):
    def __init__(self, endpoint: str, status: int, retry_at: float = 0):
        self.endpoint, self.status, self.retry_at = endpoint, status, retry_at
        super().__init__(f"Guesty endpoint={endpoint} status={status}")

class LiveSendsBlocked(RuntimeError):
    pass

class ContractError(RuntimeError):
    pass

def retry_timestamp(headers) -> float:
    value = headers.get("Retry-After", "")
    try:
        return time.time() + max(1, float(value))
    except ValueError:
        try:
            return max(time.time() + 1, parsedate_to_datetime(value).timestamp())
        except (ValueError, TypeError):
            try:
                return time.time() + max(60, float(headers.get("ratelimit-reset", 60)))
            except ValueError:
                return time.time() + 60
