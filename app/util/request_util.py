import ipaddress

from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    if not peer:
        return None
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return None