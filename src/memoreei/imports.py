"""File imports that memoreei remembers, so `sync` can re-read them later.

A local import (`memoreei import …` or a stdio import tool) records the kind, the
absolute path and its options in the ``import_sources`` table. `sync` re-runs every
registered import whose file changed, relying on the ``UNIQUE(source, source_id)``
dedup to skip what is already stored. Only local code registers a path; the network
server can re-run what is registered but never name a new one.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memoreei.tools.memory_tools import MemoryTools

# kind -> (MemoryTools method, name of its path argument)
IMPORTERS: dict[str, tuple[str, str]] = {
    "whatsapp": ("ingest_whatsapp", "file_path"),
    "sms": ("import_sms_backup", "file_path"),
    "discord-package": ("import_discord_package_tool", "package_path"),
    "messenger": ("import_messenger", "data_path"),
    "instagram": ("import_instagram", "data_path"),
    "json": ("import_json_file", "file_path"),
    "csv": ("import_csv_file", "file_path"),
    "contacts-vcf": ("import_contacts_vcf", "file_path"),
}


def path_mtime(path: Path) -> float | None:
    """mtime of a file, or the newest file under a directory. None if it's gone."""
    try:
        if path.is_dir():
            return max(
                (p.stat().st_mtime for p in path.rglob("*") if p.is_file()),
                default=path.stat().st_mtime,
            )
        return path.stat().st_mtime
    except OSError:
        return None


async def run_import(
    tools: "MemoryTools", kind: str, path: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run one importer. Adds ``new``: messages actually added, after dedup."""
    method_name, path_arg = IMPORTERS[kind]
    before = await tools.db.count_memories()
    result = await getattr(tools, method_name)(**{path_arg: path}, **(options or {}))
    result["new"] = await tools.db.count_memories() - before
    return result


async def import_and_register(
    tools: "MemoryTools", kind: str, path: str, options: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Import a file and, if that worked, remember it for `sync`."""
    resolved = Path(path).expanduser().resolve()
    result = await run_import(tools, kind, str(resolved), options)
    if "error" not in result:
        result["import_id"] = await tools.db.register_import(
            kind, str(resolved), options or {}, path_mtime(resolved)
        )
    return result


async def resync_imports(tools: "MemoryTools") -> list[dict[str, Any]]:
    """Re-import every registered file that changed since it was last read."""
    report: list[dict[str, Any]] = []
    for entry in await tools.db.list_imports():
        path = Path(entry["path"])
        item: dict[str, Any] = {"id": entry["id"], "kind": entry["kind"], "file": path.name}
        mtime = path_mtime(path)
        if mtime is None:
            item["status"] = "missing"
        elif entry["last_mtime"] is not None and mtime == entry["last_mtime"]:
            item["status"] = "unchanged"
        elif entry["kind"] not in IMPORTERS:
            item["status"] = "error"
            item["error"] = f"unknown import kind: {entry['kind']}"
        else:
            try:
                result = await run_import(tools, entry["kind"], str(path), entry["options"])
            except Exception as exc:  # one bad file mustn't stop the rest
                result = {"error": str(exc), "new": 0}
            if "error" in result:
                item["status"] = "error"
                item["error"] = result["error"]
            else:
                item["status"] = "imported"
                await tools.db.mark_import_synced(entry["id"], mtime)
            item["new"] = result.get("new", 0)
        report.append(item)
    return report
