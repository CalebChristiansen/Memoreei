"""Memoreei Cloud: reach this computer's memoreei from Claude.ai, ChatGPT and agents.

Optional, and off until the person connects. Connecting enrolls the computer by
device code (connect.py), gets a certificate for its hostname with a key made
here (certs.py), and holds a tunnel to the relay (tunnel.py), which feeds the
public listener (memoreei.public). Everything lives in <home>/cloud (state.py).
Chatbots sign in to memoreei itself (memoreei.oauth), never to Memoreei Cloud:
the service gives this computer a name and a way in, and nothing more.

None of this is needed to put memoreei on the internet. The public listener works
with any tunnel or proxy; Memoreei Cloud is the one that's set up for you.
"""
