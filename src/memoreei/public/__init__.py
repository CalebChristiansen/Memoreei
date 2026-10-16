"""The public listener: memoreei's /mcp for the internet.

A second, stricter front door beside the LAN listener: /mcp, the OAuth
protected-resource metadata and memoreei's own sign-in only (gate.py,
memoreei.oauth), bearer tokens from that sign-in or memoreei's mem_ keys, rate
limits on failures, and a local access log. It runs inside the server (listener.py, runtime.py) when Memoreei
Cloud is connected, or when MEMOREEI_PUBLIC_PORT is set for your own tunnel.
"""
