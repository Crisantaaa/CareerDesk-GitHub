"""Explicit-scope file indexing and on-demand read-only previews."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .core import Store, now, uid
from .db import connect, transaction

SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md", ".png", ".jpg", ".jpeg"}
SKIP_DIRS = {".venv", "venv", ".git", "__pycache__"}
MAX_PREVIEW_BYTES = 50 * 1024 * 1024


def path_key(path: Path | str) -> str:
    # On Windows normcase handles drive/path case and separator spelling.
    return os.path.normcase(os.path.normpath(str(Path(path).resolve(strict=False))))


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def scan_directory(store: Store, folder: str | Path, *, recursive=False, exclude=()) -> dict:
    root = Path(folder).expanduser().resolve(strict=False)
    result = {"root": str(root), "scanned": 0, "added": 0, "updated": 0, "errors": [], "skipped": 0}
    if not root.exists() or not root.is_dir():
        raise ValueError("指定目录不存在或不是文件夹")
    excluded = SKIP_DIRS | {str(value).strip().casefold() for value in exclude if str(value).strip()}
    data_root = store.path.parent.resolve(strict=False)
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            result["errors"].append(f"无法读取 {current}：{exc}")
            continue
        for entry in entries:
            candidate = Path(entry.path)
            try:
                if _is_link(candidate):
                    result["skipped"] += 1
                    continue
                resolved = candidate.resolve(strict=False)
                if not resolved.is_relative_to(root):
                    result["skipped"] += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if candidate.name.casefold() in excluded or resolved == data_root:
                        result["skipped"] += 1
                    elif recursive:
                        stack.append(candidate)
                    continue
                if not entry.is_file(follow_symlinks=False) or candidate.suffix.lower() not in SUPPORTED:
                    result["skipped"] += 1
                    continue
                stat = entry.stat(follow_symlinks=False)
                metadata = dict(display_path=str(candidate.absolute()), path_key=path_key(candidate),
                                filename=candidate.name, ext=candidate.suffix.lower(), size_bytes=stat.st_size,
                                modified_at_utc=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                                last_scanned_at_utc=now(), availability="可用")
                with transaction(store.path) as con:
                    old = con.execute("SELECT id FROM files WHERE path_key=?", (metadata["path_key"],)).fetchone()
                    if old:
                        con.execute("""UPDATE files SET display_path=?,filename=?,ext=?,size_bytes=?,
                                       modified_at_utc=?,last_scanned_at_utc=?,availability='可用',updated_at_utc=?
                                       WHERE id=?""", (metadata["display_path"], metadata["filename"], metadata["ext"],
                                       metadata["size_bytes"], metadata["modified_at_utc"], metadata["last_scanned_at_utc"], now(), old["id"]))
                        result["updated"] += 1
                    else:
                        con.execute("""INSERT INTO files(id,display_path,path_key,filename,ext,size_bytes,
                                       modified_at_utc,last_scanned_at_utc,availability,created_at_utc,updated_at_utc)
                                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                    (uid(), *metadata.values(), now(), now()))
                        result["added"] += 1
                result["scanned"] += 1
            except (OSError, ValueError) as exc:
                result["errors"].append(f"无法处理 {candidate}：{exc}")
    return result


def list_files(store: Store, search="", ext=None, company_id=None, position_id=None) -> list[dict]:
    sql = "SELECT f.* FROM files f WHERE f.filename LIKE ?"
    args: list = [f"%{search.strip()}%"]
    if ext:
        sql += " AND f.ext=?"
        args.append(ext)
    if company_id:
        sql += " AND (EXISTS(SELECT 1 FROM file_companies x WHERE x.file_id=f.id AND x.company_id=?) OR EXISTS(SELECT 1 FROM file_positions x JOIN positions p ON p.id=x.position_id WHERE x.file_id=f.id AND p.company_id=?))"
        args.extend([company_id, company_id])
    if position_id:
        sql += " AND (EXISTS(SELECT 1 FROM file_positions x WHERE x.file_id=f.id AND x.position_id=?) OR EXISTS(SELECT 1 FROM file_applications x JOIN applications a ON a.id=x.application_id WHERE x.file_id=f.id AND a.position_id=?))"
        args.extend([position_id, position_id])
    return store.query(sql + " ORDER BY f.filename", args)


def refresh_availability(store: Store, file_id: str) -> bool:
    file = store.get("files", file_id)
    if not file:
        raise ValueError("文件索引不存在")
    exists = Path(file["display_path"]).is_file()
    availability = "可用" if exists else "失效"
    if availability != file["availability"]:
        with transaction(store.path) as con:
            con.execute("UPDATE files SET availability=?,updated_at_utc=? WHERE id=?", (availability, now(), file_id))
    return exists


def relocate(store: Store, file_id: str, new_path: str | Path) -> None:
    path = Path(new_path).expanduser().resolve(strict=False)
    if path.suffix.lower() not in SUPPORTED or not path.is_file():
        raise ValueError("新路径不存在或文件类型不受支持")
    stat = path.stat()
    key = path_key(path)
    with transaction(store.path) as con:
        old = con.execute("SELECT id FROM files WHERE id=?", (file_id,)).fetchone()
        if not old:
            raise ValueError("旧文件索引不存在")
        duplicate = con.execute("SELECT id FROM files WHERE path_key=?", (key,)).fetchone()
        if duplicate and duplicate["id"] != file_id:
            raise ValueError("新路径已被索引；请先处理冲突，原有关联未更改")
        con.execute("""UPDATE files SET display_path=?,path_key=?,filename=?,ext=?,size_bytes=?,
                     modified_at_utc=?,last_scanned_at_utc=?,availability='可用',updated_at_utc=? WHERE id=?""",
                    (str(path), key, path.name, path.suffix.lower(), stat.st_size,
                     datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                     now(), now(), file_id))


def remove_index(store: Store, file_id: str) -> None:
    with transaction(store.path) as con:
        if not con.execute("SELECT id FROM files WHERE id=?", (file_id,)).fetchone():
            raise ValueError("文件索引不存在")
        con.execute("DELETE FROM files WHERE id=?", (file_id,))


def update_file_notes(store: Store, file_id: str, tags="", version_name="", notes="") -> None:
    with transaction(store.path) as con:
        if not con.execute("SELECT id FROM files WHERE id=?", (file_id,)).fetchone():
            raise ValueError("文件索引不存在")
        con.execute("UPDATE files SET tags=?,version_name=?,notes=?,updated_at_utc=? WHERE id=?",
                    (tags, version_name, notes, now(), file_id))


def preview(store: Store, file_id: str) -> dict:
    file = store.get("files", file_id)
    if not file:
        raise ValueError("文件索引不存在")
    path = Path(file["display_path"])
    if not path.is_file():
        refresh_availability(store, file_id)
        raise ValueError("原文件不存在；请重新定位索引")
    if path.stat().st_size > MAX_PREVIEW_BYTES:
        raise ValueError("文件超过 50 MB 预览限制，请使用“打开原文件”")
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        raise ValueError("此类型不支持预览")
    if ext in {".pdf", ".png", ".jpg", ".jpeg"}:
        return {"kind": "pdf" if ext == ".pdf" else "image", "path": str(path)}
    if ext in {".txt", ".md", ".csv"}:
        raw = path.read_bytes()[:1024 * 1024]
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                content = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            content = raw.decode("utf-8", errors="replace")
        return {"kind": "text", "content": content, "truncated": path.stat().st_size > len(raw)}
    if ext == ".docx":
        from docx import Document
        doc = Document(path)
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables[:20]:
            lines.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows[:100])
        return {"kind": "text", "content": "\n".join(lines[:1000]), "truncated": len(lines) > 1000}
    if ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(path)
        lines = []
        for index, slide in enumerate(prs.slides, 1):
            if index > 100:
                break
            lines.append(f"幻灯片 {index}")
            lines.extend(shape.text for shape in slide.shapes if shape.has_text_frame and shape.text.strip())
        return {"kind": "text", "content": "\n\n".join(lines), "truncated": len(prs.slides) > 100}
    if ext == ".xlsx":
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            sheets = {}
            for sheet in wb.worksheets[:20]:
                sheets[sheet.title] = [[str(v) if v is not None else "" for v in row]
                                       for _, row in zip(range(100), sheet.iter_rows(values_only=True))]
            return {"kind": "sheets", "sheets": sheets, "truncated": len(wb.worksheets) > 20}
        finally:
            wb.close()
    raise ValueError("此类型不支持预览")
