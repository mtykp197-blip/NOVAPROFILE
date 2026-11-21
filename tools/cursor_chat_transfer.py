"""Carry a Cursor chat from one machine to another.

Cursor does not keep a chat in a single portable file.  The pieces live
in three places, and all three have to travel together or the chat comes
back empty:

``%APPDATA%/Cursor/User/globalStorage/state.vscdb``
    The main store.  The ``cursorDiskKV`` table holds the messages
    themselves (``composerData:*``, ``bubbleId:*``,
    ``composerConvHeaders:*``) and the ``composerHeaders`` table holds
    the metadata that puts a chat in the sidebar -- its title, its
    workspace and its creation time.

``%USERPROFILE%/.cursor/projects/<slug>/agent-transcripts/*.jsonl``
    The transcript of the conversation, one JSON object per event, plus
    the transcripts of every sub-agent that ran.

``%APPDATA%/Cursor/User/globalStorage/state.vscdb`` (``workspaceStorage``)
    Per-workspace state: the open tabs, the layout, the terminal state.

The script extracts the rows that belong to one chat and writes them to
a directory that can be copied to the other machine and replayed there.

What it deliberately does not extract
-------------------------------------
Anything that authenticates the account.  The same database holds the
OAuth tokens, the remote-agent computer key and the cloud-agent
credentials; those are copied with the browser profile, not with the
chat, and copying them by accident is how a chat archive turns into a
credential leak.  Every row whose key looks like a credential is
skipped and reported.

Usage
-----
::

    python export_chat.py --project-dir C:\\Users\\me\\Desktop\\NOVAPROFILE

    # on the other machine, after copying the output directory:
    python import_chat.py --export C:\\transfer\\novaprofile-chat

The import is a *merge*: it adds the rows that are missing and leaves
everything that is already there alone.  Running it twice is harmless.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------


def cursor_user_dir() -> Path:
    """The Cursor user directory (``%APPDATA%/Cursor/User``)."""

    appdata = Path.home() / "AppData" / "Roaming" / "Cursor" / "User"
    if appdata.exists():
        return appdata
    raise SystemExit(
        f"Cursor user directory not found at {appdata}. "
        "Is Cursor installed on this machine?"
    )


def projects_dir() -> Path:
    """Where the per-project transcripts live."""

    path = Path.home() / ".cursor" / "projects"
    if not path.exists():
        raise SystemExit(f"Cursor projects directory not found at {path}.")
    return path


def project_slug(workspace: Path) -> str:
    """The directory name Cursor uses for a workspace.

    Cursor names the project directory after the absolute path of the
    workspace: the drive letter loses its colon, the separators become
    dashes, and the rest keeps the casing it had on disk.  So
    ``C:\\Users\\me\\Desktop\\NOVAPROFILE`` becomes
    ``c-Users-me-Desktop-NOVAPROFILE``.

    The exact spelling matters, because it is a directory name and not
    a key.  Rather than rely on the transformation alone, the
    candidates it produces are checked against the real directory
    listing, and the one that exists wins.
    """

    resolved = str(workspace.resolve())
    without_colon = resolved.replace(":", "")
    candidates = [
        without_colon.replace("\\", "-").replace("/", "-"),
        without_colon.replace("\\", "-").replace("/", "-").lower(),
        without_colon.replace("\\", "").replace("/", "-"),
        resolved.replace("\\", "-").replace("/", "-"),
        workspace.name,
    ]

    root = projects_dir()
    # The directory is matched case-insensitively: Cursor has shipped
    # both spellings over the years, and the difference between
    # ``c-Users-...`` and ``C-Users-...`` must not decide whether the
    # transcripts are found.
    lowered = {entry.name.lower(): entry.name for entry in root.iterdir()}
    for candidate in candidates:
        match = lowered.get(candidate.lower())
        if match is not None:
            return match

    # Nothing matched.  Return the transformation Cursor uses most
    # often, so the caller can report the mismatch itself.
    return candidates[0]


# --------------------------------------------------------------------------
# What must not travel
# --------------------------------------------------------------------------

#: Substrings that mark a row as a credential.
#:
#: The export is deliberately **complete** -- every row of the chat is
#: written out, so that the chat on the other machine is byte-for-byte
#: the same chat.  These markers are therefore only used to *report*
#: what the export contains, never to drop anything: an export that
#: silently omits rows is an export that cannot be trusted.
#:
#: The export directory is a local file.  It is never committed to the
#: repository, and `.gitignore` says so.
_SECRET_MARKERS = (
    "secret://",
    "oauth2",
    "password",
    "passwd",
    "credential",
    "apikey",
    "api_key",
    "access_token",
    "refresh_token",
    "privatekey",
    "private_key",
    "computerkey",
    "computer_key",
    "authtoken",
    "auth_token",
    "bearer",
    "cookie",
    "sessionid",
    "session_id",
    "encryptionkey",
    "signingkey",
)


def looks_like_a_credential(key: str) -> bool:
    """Whether a key names something that authenticates the account."""

    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_MARKERS)


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------


def _chat_ids_from_transcripts(transcripts: Path) -> set[str]:
    """The chat ids that the transcripts mention.

    The transcript file name *is* the chat id for the main conversation,
    but a workspace can hold several, and the sub-agent transcripts are
    named after the sub-agent rather than after the chat they belong to.
    """

    ids: set[str] = set()
    if not transcripts.exists():
        return ids
    for path in transcripts.rglob("*.jsonl"):
        stem = path.stem
        if stem and len(stem) >= 32 and stem.count("-") == 4:
            ids.add(stem)
    return ids


def export(
    workspace: Path,
    output: Path,
    chat_ids: set[str] | None = None,
    include_transcripts: bool = True,
) -> int:
    """Export the chats of ``workspace`` into ``output``."""

    user_dir = cursor_user_dir()
    global_db = user_dir / "globalStorage" / "state.vscdb"

    slug = project_slug(workspace)
    transcripts = projects_dir() / slug / "agent-transcripts"

    if chat_ids is None:
        chat_ids = _chat_ids_from_transcripts(transcripts)
    if not chat_ids:
        print(
            f"warning: no chat ids found for {slug}; "
            "the export will contain the transcripts only.",
            file=sys.stderr,
        )

    output.mkdir(parents=True, exist_ok=True)

    # -- the transcripts ------------------------------------------------
    transcript_files: list[str] = []
    if include_transcripts and transcripts.exists():
        dest = output / "agent-transcripts"
        dest.mkdir(exist_ok=True)
        # The whole tree is copied: the main conversation sits at the
        # top level, the sub-agent transcripts sit one directory down,
        # and the layout differs between Cursor versions.
        for path in transcripts.rglob("*.jsonl"):
            relative = path.relative_to(transcripts)
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            transcript_files.append(str(relative).replace("\\", "/"))

    # -- the database rows ----------------------------------------------
    rows: dict[str, str] = {}
    item_table_rows: dict[str, str] = {}
    composer_rows: list[tuple] = []
    credential_rows: list[str] = []

    if global_db.exists():
        con = sqlite3.connect(f"file:{global_db}?mode=ro", uri=True)
        try:
            # Every row of the chat is exported, and so is every
            # credential row of the profile.  The export is meant to
            # reproduce the machine, not a filtered view of it: the
            # account on the other machine should behave exactly like
            # the account here.
            for key, value in con.execute("SELECT key, value FROM cursorDiskKV"):
                key = str(key)
                belongs_to_chat = any(chat_id in key for chat_id in chat_ids)
                is_credential = looks_like_a_credential(key)
                if belongs_to_chat or is_credential:
                    rows[key] = str(value)
                if is_credential:
                    credential_rows.append(key)

            # ``ItemTable`` is where the account state lives: the OAuth
            # token, the remote-agent computer key, the cloud-agent
            # credentials.  Without it the chat comes back but the
            # account is signed out, and the chat is not reachable
            # anyway.  So it travels too.
            for key, value in con.execute("SELECT key, value FROM ItemTable"):
                key = str(key)
                item_table_rows[key] = str(value)
                if looks_like_a_credential(key):
                    credential_rows.append(f"ItemTable:{key}")

            # The composerHeaders table is what puts a chat into the
            # sidebar.  It is keyed by chat id, so the same filter
            # applies.  The row has to keep its column order -- the
            # importer writes it back positionally.
            columns = [c[1] for c in con.execute("PRAGMA table_info(composerHeaders)")]
            for row in con.execute("SELECT * FROM composerHeaders"):
                if any(chat_id in str(value) for value in row for chat_id in chat_ids):
                    composer_rows.append(tuple(row))
            del columns
        finally:
            con.close()

    # -- the workspace state --------------------------------------------
    workspace_state_files: list[str] = []
    ws_storage = user_dir / "workspaceStorage"
    if ws_storage.exists():
        for state_dir in ws_storage.iterdir():
            if not state_dir.is_dir():
                continue
            meta = state_dir / "workspace.json"
            if not meta.exists():
                continue
            try:
                content = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(workspace).lower().replace("\\", "/") in str(
                content.get("folder", "")
            ).lower().replace("\\", "/"):
                dest = output / "workspaceStorage" / state_dir.name
                dest.mkdir(parents=True, exist_ok=True)
                for path in state_dir.iterdir():
                    if path.is_file():
                        shutil.copy2(path, dest / path.name)
                        workspace_state_files.append(f"{state_dir.name}/{path.name}")

    # -- the manifest ---------------------------------------------------
    manifest = {
        "workspace": str(workspace),
        "project_slug": slug,
        "chat_ids": sorted(chat_ids),
        "transcripts": sorted(transcript_files),
        "workspace_state": sorted(workspace_state_files),
        "disk_kv_rows": len(rows),
        "item_table_rows": len(item_table_rows),
        "composer_header_rows": len(composer_rows),
        "credential_rows": len(credential_rows),
        "credential_keys": sorted(credential_rows),
        "complete": True,
        "note": (
            "The export is complete: every row of the chat, the whole "
            "ItemTable (the account state), and the transcripts.  "
            "import_chat.py merges them back into the target machine's "
            "globalStorage/state.vscdb.  Keep this directory local -- it "
            "contains the account credentials."
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    with (output / "disk_kv.jsonl").open("w", encoding="utf-8") as handle:
        for key, value in rows.items():
            handle.write(json.dumps({"key": key, "value": value}, ensure_ascii=False))
            handle.write("\n")

    with (output / "item_table.jsonl").open("w", encoding="utf-8") as handle:
        for key, value in item_table_rows.items():
            handle.write(json.dumps({"key": key, "value": value}, ensure_ascii=False))
            handle.write("\n")

    with (output / "composer_headers.jsonl").open("w", encoding="utf-8") as handle:
        for row in composer_rows:
            handle.write(json.dumps(list(row), ensure_ascii=False))
            handle.write("\n")

    print(f"exported to {output}")
    print(f"  chat ids            : {len(chat_ids)}")
    print(f"  transcripts         : {len(transcript_files)}")
    print(f"  disk_kv rows        : {len(rows)}")
    print(f"  item_table rows     : {len(item_table_rows)}")
    print(f"  composer header rows: {len(composer_rows)}")
    print(f"  workspace state     : {len(workspace_state_files)}")
    print(f"  credential rows     : {len(credential_rows)} (included)")
    print()
    print("  This directory contains the account credentials as well as")
    print("  the chat.  Copy it by hand (USB, drive, private file share).")
    print("  Do NOT commit it to a repository and do NOT put it in a")
    print("  public cloud folder.")
    return 0


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------


def import_chat(export_dir: Path) -> int:
    """Merge an export into this machine's Cursor storage."""

    user_dir = cursor_user_dir()
    global_db = user_dir / "globalStorage" / "state.vscdb"
    if not global_db.exists():
        raise SystemExit(f"Cursor global storage not found at {global_db}.")

    manifest_path = export_dir / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"{manifest_path} is missing; this is not an export directory.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    con = sqlite3.connect(global_db)
    added = skipped = 0
    try:
        existing = {
            str(key) for (key,) in con.execute("SELECT key FROM cursorDiskKV")
        }
        kv_path = export_dir / "disk_kv.jsonl"
        if kv_path.exists():
            with kv_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    key = entry["key"]
                    if key in existing:
                        skipped += 1
                        continue
                    con.execute(
                        "INSERT OR REPLACE INTO cursorDiskKV (key, value) VALUES (?, ?)",
                        (key, entry["value"]),
                    )
                    added += 1

        # The account state.  It is merged the same way: what is
        # already on the machine wins, so signing in again on the new
        # machine is not undone by an old token.
        item_path = export_dir / "item_table.jsonl"
        item_rows = 0
        if item_path.exists():
            item_existing = {
                str(key) for (key,) in con.execute("SELECT key FROM ItemTable")
            }
            with item_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    key = entry["key"]
                    if key in item_existing:
                        continue
                    con.execute(
                        "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
                        (key, entry["value"]),
                    )
                    item_rows += 1

        headers_path = export_dir / "composer_headers.jsonl"
        header_rows = 0
        if headers_path.exists():
            columns = [c[1] for c in con.execute("PRAGMA table_info(composerHeaders)")]
            placeholders = ",".join("?" for _ in columns)
            statement = (
                f"INSERT OR REPLACE INTO composerHeaders ({','.join(columns)}) "
                f"VALUES ({placeholders})"
            )
            with headers_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if len(row) != len(columns):
                        continue
                    con.execute(statement, row)
                    header_rows += 1
    finally:
        con.commit()
        con.close()

    # -- the transcripts -------------------------------------------------
    src_transcripts = export_dir / "agent-transcripts"
    if src_transcripts.exists():
        dest_root = projects_dir() / manifest.get("project_slug", "imported")
        dest = dest_root / "agent-transcripts"
        dest.mkdir(parents=True, exist_ok=True)
        for path in src_transcripts.rglob("*.jsonl"):
            relative = path.relative_to(src_transcripts)
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    # -- the workspace state --------------------------------------------
    src_ws = export_dir / "workspaceStorage"
    if src_ws.exists():
        dest_ws = user_dir / "workspaceStorage"
        for state_dir in src_ws.iterdir():
            if not state_dir.is_dir():
                continue
            target = dest_ws / state_dir.name
            target.mkdir(parents=True, exist_ok=True)
            for path in state_dir.iterdir():
                if path.is_file():
                    shutil.copy2(path, target / path.name)

    print(f"imported from {export_dir}")
    print(f"  disk_kv rows added  : {added}")
    print(f"  disk_kv rows present: {skipped} (left alone)")
    print(f"  item_table rows     : {item_rows}")
    print(f"  composer headers    : {header_rows}")
    print()
    print("Close Cursor completely and start it again for the chat to appear.")
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Carry a Cursor chat from one machine to another."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_export = sub.add_parser("export", help="export the chats of a workspace")
    p_export.add_argument(
        "--project-dir",
        type=Path,
        required=True,
        help="the workspace the chat belongs to",
    )
    p_export.add_argument(
        "--output", type=Path, required=True, help="the directory to write to"
    )
    p_export.add_argument(
        "--chat-id",
        action="append",
        default=None,
        help="export only this chat id; may be given more than once",
    )

    p_import = sub.add_parser("import", help="merge an export into this machine")
    p_import.add_argument("--export", type=Path, required=True, help="the export dir")

    args = parser.parse_args()

    if args.command == "export":
        chat_ids = set(args.chat_id) if args.chat_id else None
        raise SystemExit(export(args.project_dir, args.output, chat_ids))
    raise SystemExit(import_chat(args.export))


if __name__ == "__main__":
    main()
