"""
Prefer IPv4 for outgoing connections (import for its side effect).

Why (measured 19/10/2026, docs/agent_spawn_latency.md): from Omer's PC, IPv6 to
api.telegram.org fails to connect ~80-90% of the time while IPv4 always connects
in ~0.07s. Python's urllib tries the addresses in getaddrinfo order (IPv6 first),
waits the FULL request timeout on the dead IPv6 address and only then falls back
to IPv4. Every Telegram call therefore randomly cost +timeout seconds:
dispatch_start.py / tg_silent_send.py +10s, tg_pin.py +5s per call, typing_loop +3s.

This reorders getaddrinfo results so IPv4 addresses come first. IPv6 stays in the
list as a fallback, so hosts with only IPv6 still work.

Usage (first lines of a script, before any network call):
    import net_ipv4  # noqa: F401
"""
import socket

if not getattr(socket, "_kai_ipv4_first", False):
    _orig_getaddrinfo = socket.getaddrinfo

    def _getaddrinfo_ipv4_first(*args, **kwargs):
        res = _orig_getaddrinfo(*args, **kwargs)
        return sorted(res, key=lambda ai: 0 if ai[0] == socket.AF_INET else 1)

    socket.getaddrinfo = _getaddrinfo_ipv4_first
    socket._kai_ipv4_first = True
