"""The public listener: memoreei's /mcp for the internet.

A second, stricter front door beside the LAN listener: /mcp and the OAuth
protected-resource metadata only (gate.py), bearer tokens from one OAuth issuer
(tokens.py) or memoreei's own mem_ keys, rate limits on failures, and a local
access log. It runs inside the server (listener.py, runtime.py) when Memoreei
Cloud is connected, or when MEMOREEI_PUBLIC_PORT is set for your own tunnel.
"""
