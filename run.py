"""Start NEXUS locally:  python run.py"""
import ipaddress
import sys

import uvicorn

from app.config import get_settings


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


if __name__ == "__main__":
    s = get_settings()
    if not _is_loopback(s.host):
        print(
            f"WARNING: NEXUS_HOST={s.host} is not a loopback address. NEXUS has no authentication "
            "and must not be reachable from other machines.",
            file=sys.stderr,
        )
    print(f"NEXUS running at http://{'localhost' if _is_loopback(s.host) else s.host}:{s.port}")
    uvicorn.run("app.main:app", host=s.host, port=s.port, reload="--reload" in sys.argv)
