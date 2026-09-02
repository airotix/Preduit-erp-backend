"""HTTP security-headers middleware.

Adds the standard hardening headers to every response. Kept deliberately
API-appropriate: we set clickjacking / MIME-sniffing / referrer protections
unconditionally, and HSTS only outside dev (it's only meaningful over HTTPS and
would otherwise pin http-only local dev). No script-src CSP is set because this
service also serves the Swagger UI at /docs, which loads its assets from a CDN;
`frame-ancestors 'none'` still blocks framing without affecting that.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, hsts: bool = False):
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for k, v in _STATIC_HEADERS.items():
            response.headers.setdefault(k, v)
        if self._hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
