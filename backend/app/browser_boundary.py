"""Loopback browser boundary; origin-less native API clients stay supported."""
import ipaddress
from urllib.parse import urlsplit
from starlette.responses import JSONResponse


def authority(value: str):
    if not value or any(c.isspace() or ord(c) < 32 for c in value) or any(c in value for c in '/\\?#@,'):
        raise ValueError('Invalid authority')
    parsed = urlsplit('http://' + value)
    host = parsed.hostname
    if not host or parsed.username or parsed.password:
        raise ValueError('Invalid host')
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    return host.lower().rstrip('.'), port or 80


def origin(value: str):
    if not value or value == 'null' or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('Invalid origin')
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('Invalid origin')
    host, port = authority(parsed.netloc)
    if parsed.scheme == 'https' and parsed.port is None:
        port = 443
    return parsed.scheme, host, port


class LocalBrowserBoundaryMiddleware:
    def __init__(self, app, allowed_origins=()):
        self.app = app
        self.allowed = set()
        for item in allowed_origins:
            try: self.allowed.add(origin(item))
            except (ValueError, TypeError): pass

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = scope.get('headers', [])
        hosts = [value.decode('latin-1') for key, value in headers if key.lower() == b'host']
        origins = [value.decode('latin-1') for key, value in headers if key.lower() == b'origin']
        try:
            if len(hosts) != 1: raise ValueError('A single Host is required')
            host, port = authority(hosts[0])
            try: loopback = ipaddress.ip_address(host).is_loopback
            except ValueError: loopback = host == 'localhost'
            if not loopback: raise ValueError('Local AI requires a loopback Host')
        except ValueError as exc:
            return await JSONResponse({'detail': str(exc)}, status_code=400)(scope, receive, send)
        if origins:
            try:
                if len(origins) != 1: raise ValueError('A single Origin is required')
                supplied = origin(origins[0])
                # Forwarded host/protocol headers never establish trust.
                if supplied not in self.allowed and supplied != ('http', host, port):
                    raise ValueError('Browser origin is not allowed')
            except ValueError as exc:
                return await JSONResponse({'detail': str(exc)}, status_code=403)(scope, receive, send)
        elif scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
            site = next((v.decode('latin-1') for k, v in headers if k.lower() == b'sec-fetch-site'), '')
            if site in {'cross-site', 'same-site'}:
                return await JSONResponse({'detail': 'Cross-origin browser mutation rejected'}, status_code=403)(scope, receive, send)
        return await self.app(scope, receive, send)
