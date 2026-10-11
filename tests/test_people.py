"""Finding people by name or number, across apps, and filters that narrow before ranking."""
from __future__ import annotations

from datetime import datetime

import pytest
from ulid import ULID

from memoreei.search.people import Filter, fold, looks_like_identifier, matches_name, parse_date
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem
from memoreei.tools.memory_tools import MemoryTools

ZEZIMA = "+12025550142"
HANS_G = "+12025550143"
HANS_Z = "+12025550144"
GROUP = "imessage:chat555"


class WordEmbedder:
    """Bag of words over a small vocabulary, so ranking is predictable."""

    VOCAB = ["printer", "pizza", "donut", "heist", "rune", "scimitar", "boat", "party"]

    def _embed(self, text: str) -> list[float]:
        lower = text.lower()
        return [1.0 if w in lower else 0.0 for w in self.VOCAB]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def ts(day: str, hour: int = 12) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00").astimezone().timestamp())


def item(source: str, sender: str, text: str, when: int, **meta) -> MemoryItem:
    from_me = sender == "me"
    return MemoryItem(
        id=str(ULID()), source=source, source_id=f"{source}:{ULID()}",
        content=f"{sender}: {text}", summary=None, participants=[sender], ts=when,
        ingested_at=when, metadata={"is_from_me": from_me, **meta},
        embedding=WordEmbedder()._embed(text),
    )


@pytest.fixture
async def tools(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMOREEI_ME", "Guthix Balance")
    db = Database(db_path=str(tmp_path / "people.db"))
    await db.connect()
    await db.upsert_contacts([
        (ZEZIMA, "Zezima", "addressbook"),
        (HANS_G, "Hans Gruber", "addressbook"),
        (HANS_Z, "Hans Zimmer", "addressbook"),
        ("+12025550100", "Guthix Balance", "addressbook"),
        ("signal:sz", "Zezima", "signal"),
    ])
    rows = [
        # iMessage stores the handle, not the name
        item(f"imessage:{ZEZIMA}", ZEZIMA, "the rune scimitar is in the bank", ts("2026-02-10"),
             chat_name=""),
        item(f"imessage:{ZEZIMA}", "me", "can I borrow the boat", ts("2026-02-11"), chat_name=""),
        # WhatsApp and Signal store a name, with the number beside it
        item("whatsapp:z@lid", "Zezima", "pizza party saturday", ts("2026-03-01"),
             is_group=False, sender_phone=ZEZIMA, chat_name="Zezima"),
        item("signal:sz", "Zezima", "donut heist is off", ts("2026-03-02"),
             is_group=False, sender_phone=ZEZIMA, chat_name="Zezima"),
        # a namesake on Signal with no number: same name, not linked by number
        item("signal:other", "Zezima", "printer printer printer", ts("2026-03-03"),
             is_group=False, chat_name="Zezima"),
        # a group chat
        item(GROUP, ZEZIMA, "who has the boat", ts("2026-02-28", 9), chat_name="Party"),
        item(GROUP, "me", "the boat is mine", ts("2026-02-28", 10), chat_name="Party"),
        item(f"imessage:{HANS_G}", HANS_G, "the vault heist", ts("2026-01-05"), chat_name=""),
        item(f"imessage:{HANS_Z}", HANS_Z, "scoring the heist film", ts("2026-01-06"), chat_name=""),
    ]
    # Plenty of better keyword matches from someone else, to bury the targets
    rows += [item("imessage:+12025550199", "+12025550199", f"rune scimitar rune {n}",
                  ts("2026-02-01")) for n in range(60)]
    for row in rows:
        await db.insert_memory(row)
    yield MemoryTools(db=db, embedder=WordEmbedder())
    await db.close()


def senders(found: dict) -> list[str]:
    return [r["sender"] for r in found["results"]]


def contents(found: dict) -> list[str]:
    return [r["content"] for r in found["results"]]


async def test_a_name_finds_messages_stored_under_a_number(tools):
    found = await tools.search_memoreei("rune scimitar", participant="Zezima", limit=5)
    assert found["results"][0]["content"] == f"{ZEZIMA}: the rune scimitar is in the bank"
    assert found["results"][0]["sender"] == "Zezima"
    assert found["results"][0]["chat"] == "Zezima"


async def test_filters_narrow_before_ranking(tools):
    """Sixty better matches from someone else don't crowd the one from Zezima out."""
    found = await tools.search_memoreei("rune scimitar", participant="Zezima", limit=1)
    assert contents(found) == [f"{ZEZIMA}: the rune scimitar is in the bank"]


async def test_a_number_in_any_format_finds_every_app(tools):
    found = await tools.search_memoreei("pizza donut", participant="(202) 555-0142", limit=10)
    platforms = {r["platform"] for r in found["results"]}
    assert {"whatsapp", "signal"} <= platforms
    assert found["people"] == [{"name": "Zezima", "identifiers": [ZEZIMA]}]


async def test_a_number_does_not_reach_a_namesake_without_one(tools):
    found = await tools.search_memoreei("printer", participant=ZEZIMA, limit=20)
    assert "Zezima: printer printer printer" not in contents(found)


async def test_part_of_a_name_works(tools):
    found = await tools.search_memoreei("rune", participant="zez", limit=3)
    assert [p["name"] for p in found["people"]] == ["Zezima"]
    assert "note" not in found
    assert contents(found)[0] == f"{ZEZIMA}: the rune scimitar is in the bank"


async def test_scope_sent_is_only_what_they_wrote(tools):
    found = await tools.search_memoreei("boat", participant="Zezima", participant_scope="sent")
    assert "me" not in senders(found)


async def test_scope_conversation_adds_my_side_of_one_to_one_chats(tools):
    found = await tools.search_memoreei("boat", participant="Zezima")
    assert "me: can I borrow the boat" in contents(found)
    assert "me: the boat is mine" not in contents(found)  # a group, not one-to-one


async def test_scope_any_chat_includes_their_groups(tools):
    found = await tools.search_memoreei("boat", participant="Zezima", participant_scope="any_chat")
    assert "me: the boat is mine" in contents(found)


async def test_an_ambiguous_name_searches_everyone_and_says_so(tools):
    found = await tools.search_memoreei("heist", participant="Hans")
    assert {p["name"] for p in found["people"]} == {"Hans Gruber", "Hans Zimmer"}
    assert "2 people" in found["note"]
    assert {r["sender"] for r in found["results"]} == {"Hans Gruber", "Hans Zimmer"}


async def test_nobody_by_that_name(tools):
    found = await tools.search_memoreei("heist", participant="Bob")
    assert found["results"] == [] and found["people"] == []
    assert "Nobody called 'Bob'" in found["note"]


async def test_me_by_name_with_sent_scope(tools):
    found = await tools.search_memoreei("boat", participant="Guthix", participant_scope="sent")
    assert set(senders(found)) == {"me"}
    assert found["people"][0]["me"] is True


async def test_me_with_conversation_scope_narrows_nothing_and_says_so(tools):
    found = await tools.search_memoreei("boat", participant="me")
    assert "narrowed nothing" in found["note"]
    assert "Zezima" in senders(found)


async def test_before_includes_the_whole_day(tools):
    found = await tools.search_memoreei("boat", before="2026-02-11", after="2026-02-11")
    assert contents(found) == ["me: can I borrow the boat"]


async def test_source_takes_a_platform(tools):
    found = await tools.search_memoreei("pizza donut heist", source="signal")
    assert {r["platform"] for r in found["results"]} == {"signal"}


async def test_an_unknown_source_says_what_it_takes(tools):
    with pytest.raises(ValueError, match="participant instead"):
        await tools.search_memoreei("heist", source="Hans Gruber")


async def test_empty_results_explain_themselves(tools):
    found = await tools.search_memoreei("heist", participant="Zezima", after="2030-01-01")
    assert found["results"] == []
    assert "Nothing matched within" in found["note"] and "fewer filters" in found["note"]


async def test_get_context_counts_messages_and_marks_the_hit(tools):
    found = await tools.search_memoreei("borrow boat", participant="Zezima", limit=1)
    hit = found["results"][0]["id"]
    context = await tools.get_context(hit, before=1, after=0)
    assert [m["content"] for m in context["messages"]] == [
        f"{ZEZIMA}: the rune scimitar is in the bank", "me: can I borrow the boat"]
    assert context["messages"][1]["hit"] is True


async def test_get_context_unknown_id(tools):
    with pytest.raises(ValueError, match="Ids come from"):
        await tools.get_context("nope")


async def test_list_sources_groups_by_person(tools):
    listed = await tools.list_sources()
    hans = next(p for p in listed["people"] if p["name"] == "Hans Gruber")
    assert hans["sources"] == {f"imessage:{HANS_G}": 1}
    assert any(g["source"] == GROUP and g["name"] == "Party" for g in listed["groups"])
    assert {"imessage", "signal", "whatsapp"} <= set(listed["platforms"])


def test_filter_sql_and_mask_agree_on_platforms():
    sql, params = Filter(platforms={"signal"}).sql()
    assert "substr(source" in sql and params == ["signal", 7, "signal:"]


def test_name_matching():
    assert matches_name(["dav", "all"], "David Allen")
    assert not matches_name(["allen", "x"], "David Allen")
    assert fold("Zoë") == "zoe"
    assert looks_like_identifier("+1 (202) 555-0142")
    assert looks_like_identifier("someone@example.com")
    assert not looks_like_identifier("Agent 47")


def test_parse_date_end_of_day():
    start = parse_date("2026-02-11")
    assert parse_date("2026-02-11", end=True) - start == 86400
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        parse_date("last tuesday")


async def test_the_network_server_documents_itself():
    from memoreei.server import build_network_server

    server = build_network_server()
    assert "participant" in (server.instructions or "")
    tools = {t.name: t for t in await server.list_tools()}
    search = tools["search_memoreei"]
    props = search.inputSchema["properties"]
    assert props["participant_scope"]["enum"] == ["conversation", "sent", "any_chat"]
    assert all(p.get("description") for p in props.values())
    assert search.outputSchema and "note" in search.outputSchema["properties"]
    assert all(tools[n].annotations.readOnlyHint for n in ("search_memoreei", "get_context", "list_sources"))


async def test_results_pass_the_output_schema_through_a_real_client(tools, monkeypatch):
    """Through a client session, as Hermes sees it: the SDK checks each result against
    the tool's output schema there, and optional keys come back as null."""
    from mcp.shared.memory import create_connected_server_and_client_session

    import memoreei.server as server

    async def fake_tools():
        return tools

    monkeypatch.setattr(server, "_get_tools", fake_tools)
    mcp = server.build_network_server()
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        plain = await client.call_tool("search_memoreei", {"query": "rune", "participant": "Zezima"})
        assert not plain.isError, plain.content
        assert plain.structuredContent["results"]
        assert all("hit" not in row for row in plain.structuredContent["results"])
        assert '"note"' not in plain.content[0].text
        noted = await client.call_tool("search_memoreei", {"query": "heist", "participant": "Hans"})
        assert not noted.isError and "2 people" in noted.structuredContent["note"]
        context = await client.call_tool("get_context", {"memory_id": plain.structuredContent["results"][0]["id"]})
        assert not context.isError, context.content
        listed = await client.call_tool("list_sources", {})
        assert not listed.isError, listed.content


async def test_weak_matches_say_so_when_asked(tools):
    found = await tools.search_memoreei("zeppelin", participant="Zezima", weak_match_note=True)
    assert found["results"] and "nearest by meaning" in found["note"]
    found = await tools.search_memoreei("scimitar", participant="Zezima", weak_match_note=True)
    assert "nearest by meaning" not in (found.get("note") or "")
    found = await tools.search_memoreei("zeppelin", participant="Zezima")
    assert "note" not in found


async def test_any_words_reaches_the_keyword_search(tools, monkeypatch):
    """The fallback itself is tested in test_fts5_sanitizer; here, that the switch gets there."""
    seen = []
    real = tools.db.search_fts

    async def spy(*args, **kwargs):
        seen.append(kwargs.get("any_words"))
        return await real(*args, **kwargs)

    monkeypatch.setattr(tools.db, "search_fts", spy)
    await tools.search_memoreei("bank zeppelin", participant="Zezima")
    await tools.search_memoreei("bank zeppelin", participant="Zezima", any_words=True)
    assert seen == [False, True]


async def test_get_context_centres_on_the_anchor_in_a_dense_chat(tmp_path):
    """Backlog 30: messages seconds apart all fell in the old minutes-wide window, and
    'ORDER BY ts LIMIT n' returned the chat's first rows, whatever the anchor."""
    from pathlib import Path

    from memoreei.connectors.generic_connector import import_json

    sample = Path(__file__).resolve().parent.parent / "data/samples/donut_heist.jsonl"
    items, _ = import_json(sample, {"content": "text", "sender": "sender", "timestamp": "ts"},
                           source_label="donut")
    db = Database(db_path=str(tmp_path / "ctx.db"))
    await db.connect()
    for it in items:
        await db.insert_memory(it)
    tools = MemoryTools(db=db, embedder=WordEmbedder())
    anchor = items[48]
    context = await tools.get_context(anchor.id, before=3, after=3)
    got = [m["content"] for m in context["messages"]]
    assert got == [it.content for it in items[45:52]]
    assert [m.get("hit", False) for m in context["messages"]] == [False] * 3 + [True] + [False] * 3
    await db.close()


async def test_list_sources_is_bounded_and_says_what_it_left_out(tools):
    listed = await tools.list_sources(limit=1)
    assert len(listed["people"]) == 1 and len(listed["groups"]) == 1
    assert listed["not_shown"]["people"] >= 2 and "name" in listed["note"]
    assert listed["not_shown"]["unnamed chats"] >= 1  # the number with no contact


async def test_list_sources_finds_someone_by_name(tools):
    listed = await tools.list_sources(name="hans")
    assert {p["name"] for p in listed["people"]} == {"Hans Gruber", "Hans Zimmer"}
    assert "not_shown" not in listed
