"""Rate limiting helpers for bot requests."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


class TextRateLimiter:
    """Per-user text request limiter with minute/hour windows."""

    def __init__(self, per_minute: int, per_hour: int):
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._requests: Dict[int, List[datetime]] = {}
        self._lock = asyncio.Lock()

    async def check_and_record(self, user_id: int) -> tuple[bool, Optional[str]]:
        """Validate and record a request for the given user."""
        now = datetime.now(timezone.utc)
        minute_cutoff = now - timedelta(minutes=1)
        hour_cutoff = now - timedelta(hours=1)

        async with self._lock:
            # Prune expired entries for all users to prevent unbounded growth.
            for existing_user_id, timestamps in list(self._requests.items()):
                recent = [ts for ts in timestamps if ts >= hour_cutoff]
                if recent:
                    self._requests[existing_user_id] = recent
                else:
                    del self._requests[existing_user_id]

            requests = self._requests.get(user_id, [])

            minute_count = sum(1 for ts in requests if ts >= minute_cutoff)
            hour_count = len(requests)

            if minute_count >= self.per_minute:
                oldest_minute = min(ts for ts in requests if ts >= minute_cutoff)
                retry_after = oldest_minute + timedelta(minutes=1)
                retry_seconds = max(1, int((retry_after - now).total_seconds()))
                return False, (
                    f"Text rate limit reached ({self.per_minute}/minute). "
                    f"Please wait about {retry_seconds}s and try again."
                )

            if hour_count >= self.per_hour:
                oldest_hour = min(requests)
                retry_after = oldest_hour + timedelta(hours=1)
                retry_minutes = max(1, int((retry_after - now).total_seconds() // 60) + 1)
                return False, (
                    f"Text rate limit reached ({self.per_hour}/hour). "
                    f"Please try again in about {retry_minutes} minute(s)."
                )

            requests.append(now)
            self._requests[user_id] = requests

        return True, None
