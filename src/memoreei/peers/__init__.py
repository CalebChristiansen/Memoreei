"""Another memoreei as a source.

One memoreei (the *source*, a laptop say) pushes its messages to another (the
*receiver*, a server or the cloud), which keeps them beside its own and searches them
like any other source. The source only ever sends: it needs no public address, no open
port and no Relay, just a way to reach the receiver's.

Pairing (store, http, client):

1. On the receiver, "Add another memoreei" (``memoreei sources invite``) makes an
   invitation: an address with a secret in it, ``…/peer/pair/<token>``, and a short code.
   It lasts ten minutes and is good for one memoreei.
2. On the source, ``memoreei sources add-to <address>`` (or the dashboard) claims it and
   is told the same code, which the person compares, then approves *on the source*: it
   is the source's messages that are about to leave.
3. Confirming hands the source a push key (``mpk_…``). It can push to that receiver and
   nothing else: it is never an MCP key, and the push endpoint answers with counts only.
   Either side can end the pairing; the receiver then refuses the key.

Every message carries an origin stamp (``memories.origin``, ``origin_source``,
``origin_id``): the memoreei that first read it, and its source and id there. The
receiver keeps one copy per origin, never takes back one that started with itself, and
so any web of memoreeis (A↔B, A→B→C→A, two paths to C) holds each message once.

Deletes and edits made in a memoreei are logged by trigger (``memory_changes``) while it
sends anywhere, and pushed on, so a delete removes every copy with that origin down a
chain. A receiver never deletes its own messages because someone else said so.
"""
from __future__ import annotations

PUSH_KEY_PREFIX = "mpk_"
