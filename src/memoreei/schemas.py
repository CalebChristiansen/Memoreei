"""What the network tools return, as typed shapes: MCP clients get these as output schemas.

No ``from __future__ import annotations`` here: TypedDict reads NotRequired from the
annotations as written, and a string annotation hides it, making every key required.

Optional keys at the top level also allow null: the SDK fills an absent one with None
in the structured result, then checks that result against this schema.
"""
from pydantic import Field
from typing_extensions import Annotated, NotRequired, TypedDict


class Message(TypedDict):
    id: Annotated[str, Field(description="Pass to get_context to read around this message")]
    date: Annotated[str, Field(description="When it was sent, YYYY-MM-DD HH:MM, owner's time zone")]
    platform: Annotated[str, Field(description="imessage, whatsapp, signal, …")]
    chat: Annotated[str, Field(description="The group's name, or the other person in a one-to-one chat")]
    group: Annotated[bool, Field(description="True for a group chat")]
    sender: Annotated[str, Field(description="Who wrote it; 'me' is the owner")]
    content: Annotated[str, Field(description="The message, as stored: usually 'sender: text'")]
    source: Annotated[str, Field(description="The chat's id, for the source filter")]
    deleted: NotRequired[Annotated[bool, Field(description="Deleted for everyone after it was read")]]
    hit: NotRequired[Annotated[bool, Field(description="The message get_context was asked about")]]


class PersonMatch(TypedDict):
    name: str
    identifiers: Annotated[list[str], Field(description="Phone numbers and addresses known for them")]
    me: NotRequired[Annotated[bool, Field(description="This is the owner")]]


class SearchResult(TypedDict):
    results: Annotated[list[Message], Field(description="Best match first")]
    people: NotRequired[Annotated[list[PersonMatch] | None, Field(
        description="Who participant matched; more than one means the name was ambiguous")]]
    note: NotRequired[Annotated[str | None, Field(
        description="Why results are empty or broader than asked; read it before answering")]]


class Context(TypedDict):
    messages: Annotated[list[Message], Field(description="Oldest first")]


class PersonSources(TypedDict):
    name: str
    messages: int
    sources: Annotated[dict[str, int], Field(description="Each chat's source id and its message count")]


class GroupSource(TypedDict):
    name: str
    source: str
    messages: int


class OtherSource(TypedDict):
    source: str
    messages: int


class Sources(TypedDict):
    total: int
    platforms: Annotated[list[str], Field(description="Values the source filter takes")]
    people: Annotated[list[PersonSources], Field(description="Most messages first")]
    groups: list[GroupSource]
    other: Annotated[list[OtherSource], Field(description="Sources with no name to give them")]
