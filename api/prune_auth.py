"""Remove one bounded batch of expired auth records; never removes accounts."""
import sys
import time

from auth_store import AuthStore


def main():
    try:
        store = AuthStore.from_env()
        now = int(time.time())
        sessions = store.purge_expired_sessions(now)
        limits = store.purge_expired_rate_limits(now)
    except Exception:
        print("Authentication cleanup failed. Check database settings and connectivity.", file=sys.stderr)
        return 1
    print(f"Removed {sessions} expired sessions and {limits} expired rate-limit records.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
