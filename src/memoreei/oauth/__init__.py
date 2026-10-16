"""memoreei's own OAuth sign-in, for chatbots and agents reaching it from the internet.

Claude.ai, ChatGPT and agent harnesses sign in to memoreei itself, at its
public address, and the person approves each one **on the computer memoreei runs
on**: in the dashboard, from a notification, or with `memoreei chatbots approve`.
No outside server can mint a token for this memoreei.

  server.py   the endpoints (authorize, token, revoke, register, device codes)
  clients.py  CIMD and DCR clients, fetched carefully; private_key_jwt
  tokens.py   refresh tokens: signed, numbered, rotated
  store.py    the rows, in the encrypted database
  notify.py   a desktop notification when someone asks
"""
