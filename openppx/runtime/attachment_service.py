"""Validation and deterministic model projection for uploaded attachments."""

from __future__ import annotations

import csv
import io
import re
import stat
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable
from xml.etree import ElementTree


MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_MESSAGE_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_MESSAGE_ATTACHMENTS = 10
MAX_EXTRACTED_TEXT_CHARS = 250_000
MAX_ARCHIVE_ENTRIES = 2_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 200
MAX_SPREADSHEET_CELLS = 200_000
MAX_PDF_PAGES = 300
MAX_IMAGE_PIXELS = 40_000_000


class AttachmentValidationError(ValueError):
    """Raised when an attachment violates a supported format or safety rule."""


@dataclass(frozen=True)
class PreparedAttachment:
    """Validated attachment data and its bounded model-facing projection."""

    file_name: str
    mime_type: str
    kind: str
    data: bytes
    model_text: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _AttachmentSpec:
    mime_type: str
    accepted_mime_types: frozenset[str]
    kind: str
    extractor: Callable[[str, bytes], tuple[str | None, dict[str, Any]]]


_WORD_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_POWERPOINT_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_LEGACY_OFFICE_EXTENSIONS = {".doc", ".xls", ".ppt"}
_TEXT_EXTENSIONS = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _validate_file_name(file_name: str) -> str:
    name = str(file_name or "").strip()
    if not name or len(name) > 255 or name in {".", ".."}:
        raise AttachmentValidationError("Attachment filename must contain between 1 and 255 characters.")
    if "/" in name or "\\" in name or any(ord(character) < 32 for character in name):
        raise AttachmentValidationError("Attachment filename must not contain a path or control characters.")
    return name


def _normalized_mime_type(mime_type: str) -> str:
    normalized = str(mime_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if not normalized or len(normalized) > 127 or "/" not in normalized:
        raise AttachmentValidationError("Attachment MIME type is invalid.")
    if any(character.isspace() or ord(character) < 32 for character in normalized):
        raise AttachmentValidationError("Attachment MIME type is invalid.")
    return normalized


def _extension(file_name: str) -> str:
    dot = file_name.rfind(".")
    return file_name[dot:].lower() if dot >= 0 else ""


def _safe_xml(data: bytes, *, member_name: str) -> ElementTree.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise AttachmentValidationError(f"{member_name} contains a prohibited XML declaration.")
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise AttachmentValidationError(f"{member_name} is malformed XML.") from exc


def _open_safe_ooxml(data: bytes) -> dict[str, bytes]:
    if not data.startswith(b"PK"):
        raise AttachmentValidationError("Attachment content does not match its OOXML extension.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, zipfile.BadZipFile) as exc:
        raise AttachmentValidationError("The Office document is damaged or is not a valid OOXML archive.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise AttachmentValidationError("The Office document contains too many archive entries.")
        total_uncompressed = 0
        members: dict[str, bytes] = {}
        seen_names: set[str] = set()
        for info in infos:
            normalized = info.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            if path.is_absolute() or ".." in path.parts or normalized.startswith("/"):
                raise AttachmentValidationError("The Office document contains an unsafe archive path.")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise AttachmentValidationError("The Office document contains a prohibited symlink.")
            if info.flag_bits & 0x1:
                raise AttachmentValidationError("Encrypted Office archive members are not supported.")
            if normalized in seen_names:
                raise AttachmentValidationError("The Office document contains duplicate archive members.")
            seen_names.add(normalized)
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise AttachmentValidationError("The Office document expands beyond the supported size limit.")
            if info.file_size > 1_000_000 and info.file_size / max(1, info.compress_size) > MAX_ARCHIVE_COMPRESSION_RATIO:
                raise AttachmentValidationError("The Office document exceeds the safe compression ratio.")
            if not info.is_dir():
                try:
                    members[normalized] = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise AttachmentValidationError("The Office document could not be read safely.") from exc
        if "[Content_Types].xml" not in members:
            raise AttachmentValidationError("The Office document is missing [Content_Types].xml.")
        _safe_xml(members["[Content_Types].xml"], member_name="[Content_Types].xml")
        return members


def _require_ooxml_content_type(members: dict[str, bytes], expected: str) -> None:
    root = _safe_xml(members["[Content_Types].xml"], member_name="[Content_Types].xml")
    declared = {str(node.attrib.get("ContentType") or "") for node in root}
    if expected not in declared:
        raise AttachmentValidationError("Attachment content does not match its OOXML extension.")


def _bounded_text(file_name: str, kind: str, text: str) -> str:
    normalized = text.replace("\x00", "").strip()
    if not normalized:
        raise AttachmentValidationError(f"{file_name} has no readable {kind} content.")
    if len(normalized) > MAX_EXTRACTED_TEXT_CHARS:
        raise AttachmentValidationError(
            f"{file_name} contains more than {MAX_EXTRACTED_TEXT_CHARS:,} readable characters."
        )
    return f"[Attachment: {file_name}]\nFormat: {kind}\n\n{normalized}\n[End attachment]"


def _extract_docx(file_name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    members = _open_safe_ooxml(data)
    _require_ooxml_content_type(
        members,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )
    document = members.get("word/document.xml")
    if document is None:
        raise AttachmentValidationError("The Word document is missing word/document.xml.")
    root = _safe_xml(document, member_name="word/document.xml")
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_W}t")).strip()
        if text:
            paragraphs.append(text)
    projected = _bounded_text(file_name, "Word document", "\n".join(paragraphs))
    return projected, {"paragraph_count": len(paragraphs), "text_char_count": len(projected)}


def _xlsx_shared_strings(members: dict[str, bytes]) -> list[str]:
    payload = members.get("xl/sharedStrings.xml")
    if payload is None:
        return []
    root = _safe_xml(payload, member_name="xl/sharedStrings.xml")
    return ["".join(node.text or "" for node in item.iter(f"{_S}t")) for item in root.iter(f"{_S}si")]


def _xlsx_sheet_names(members: dict[str, bytes]) -> dict[str, str]:
    workbook_data = members.get("xl/workbook.xml")
    relationships_data = members.get("xl/_rels/workbook.xml.rels")
    if workbook_data is None or relationships_data is None:
        return {}
    workbook = _safe_xml(workbook_data, member_name="xl/workbook.xml")
    relationships = _safe_xml(relationships_data, member_name="xl/_rels/workbook.xml.rels")
    targets = {
        str(item.attrib.get("Id") or ""): str(item.attrib.get("Target") or "")
        for item in relationships.iter(f"{_PR}Relationship")
    }
    result: dict[str, str] = {}
    for sheet in workbook.iter(f"{_S}sheet"):
        relation_id = str(sheet.attrib.get(f"{_R}id") or "")
        target = targets.get(relation_id, "").lstrip("/")
        if target.startswith("xl/"):
            member = target
        else:
            member = str(PurePosixPath("xl") / target)
        if target:
            result[member] = str(sheet.attrib.get("name") or PurePosixPath(member).stem)
    return result


def _natural_member_key(value: str) -> tuple[Any, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value))


def _extract_xlsx(file_name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    members = _open_safe_ooxml(data)
    _require_ooxml_content_type(
        members,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )
    if "xl/workbook.xml" not in members:
        raise AttachmentValidationError("The Excel workbook is missing xl/workbook.xml.")
    shared_strings = _xlsx_shared_strings(members)
    sheet_names = _xlsx_sheet_names(members)
    worksheet_members = sorted(
        (name for name in members if name.startswith("xl/worksheets/") and name.endswith(".xml")),
        key=_natural_member_key,
    )
    if not worksheet_members:
        raise AttachmentValidationError("The Excel workbook contains no worksheets.")
    rendered_sheets: list[str] = []
    total_cells = 0
    for index, member_name in enumerate(worksheet_members, 1):
        root = _safe_xml(members[member_name], member_name=member_name)
        rows: list[str] = []
        for row in root.iter(f"{_S}row"):
            values: list[str] = []
            for cell in row.iter(f"{_S}c"):
                total_cells += 1
                if total_cells > MAX_SPREADSHEET_CELLS:
                    raise AttachmentValidationError(
                        f"{file_name} contains more than {MAX_SPREADSHEET_CELLS:,} cells."
                    )
                value_node = cell.find(f"{_S}v")
                inline = cell.find(f"{_S}is")
                value = value_node.text if value_node is not None and value_node.text is not None else ""
                if cell.attrib.get("t") == "s" and value.isdigit():
                    position = int(value)
                    value = shared_strings[position] if position < len(shared_strings) else value
                elif inline is not None:
                    value = "".join(node.text or "" for node in inline.iter(f"{_S}t"))
                formula = cell.find(f"{_S}f")
                if formula is not None and formula.text:
                    value = f"={formula.text}" + (f" ({value})" if value else "")
                values.append(value)
            if values:
                rows.append("\t".join(values))
        sheet_name = sheet_names.get(member_name, f"Sheet {index}")
        rendered_sheets.append(f"## Sheet: {sheet_name}\n" + "\n".join(rows))
    projected = _bounded_text(file_name, "Excel workbook", "\n\n".join(rendered_sheets))
    return projected, {
        "sheet_count": len(worksheet_members),
        "cell_count": total_cells,
        "text_char_count": len(projected),
    }


def _extract_pptx(file_name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    members = _open_safe_ooxml(data)
    _require_ooxml_content_type(
        members,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    )
    if "ppt/presentation.xml" not in members:
        raise AttachmentValidationError("The PowerPoint presentation is missing ppt/presentation.xml.")
    slide_members = sorted(
        (
            name
            for name in members
            if name.startswith("ppt/slides/slide") and name.endswith(".xml") and "/_rels/" not in name
        ),
        key=_natural_member_key,
    )
    slides: list[str] = []
    for index, member_name in enumerate(slide_members, 1):
        root = _safe_xml(members[member_name], member_name=member_name)
        text = "\n".join((node.text or "").strip() for node in root.iter(f"{_A}t") if (node.text or "").strip())
        if text:
            slides.append(f"## Slide {index}\n{text}")
    projected = _bounded_text(file_name, "PowerPoint presentation", "\n\n".join(slides))
    return projected, {"slide_count": len(slide_members), "text_char_count": len(projected)}


def _decode_utf8(file_name: str, data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AttachmentValidationError(f"{file_name} must use UTF-8 encoding.") from exc
    if "\x00" in text:
        raise AttachmentValidationError(f"{file_name} contains prohibited null bytes.")
    return text


def _extract_csv(file_name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    text = _decode_utf8(file_name, data)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows: list[str] = []
    row_count = 0
    try:
        for row_count, row in enumerate(csv.reader(io.StringIO(text), dialect), 1):
            if row_count > 100_000:
                raise AttachmentValidationError(f"{file_name} contains more than 100,000 rows.")
            rows.append("\t".join(row))
    except csv.Error as exc:
        raise AttachmentValidationError(f"{file_name} contains malformed CSV data.") from exc
    projected = _bounded_text(file_name, "CSV", "\n".join(rows))
    return projected, {"row_count": row_count, "text_char_count": len(projected)}


def _extract_text(file_name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    projected = _bounded_text(file_name, "text", _decode_utf8(file_name, data))
    return projected, {"text_char_count": len(projected)}


def _extract_pdf(file_name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    if not data.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").startswith(b"%PDF-"):
        raise AttachmentValidationError("Attachment content does not match its PDF extension.")
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - packaging gate covers the dependency
        raise AttachmentValidationError("PDF support is unavailable in this Node installation.") from exc
    try:
        reader = PdfReader(io.BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise AttachmentValidationError("Encrypted PDF files are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise AttachmentValidationError(f"{file_name} contains more than {MAX_PDF_PAGES} pages.")
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except AttachmentValidationError:
        raise
    except (PdfReadError, OSError, ValueError, TypeError) as exc:
        raise AttachmentValidationError("The PDF is damaged or cannot be read safely.") from exc
    readable = [f"## Page {index}\n{text}" for index, text in enumerate(pages, 1) if text]
    if not readable:
        raise AttachmentValidationError(
            "The PDF contains no extractable text. Scanned PDFs require OCR, which is not supported yet."
        )
    projected = _bounded_text(file_name, "PDF", "\n\n".join(readable))
    return projected, {"page_count": len(pages), "text_char_count": len(projected)}


def _extract_image(file_name: str, data: bytes) -> tuple[None, dict[str, Any]]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - packaging gate covers the dependency
        raise AttachmentValidationError("Image support is unavailable in this Node installation.") from exc
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise AttachmentValidationError("The image exceeds the safe pixel limit.")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image_format = str(image.format or "").upper()
    except AttachmentValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AttachmentValidationError("The image exceeds the safe pixel limit.") from exc
    except (OSError, ValueError) as exc:
        raise AttachmentValidationError("Attachment content does not match its image extension.") from exc
    expected_formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
    if image_format != expected_formats[_extension(file_name)]:
        raise AttachmentValidationError("Attachment content does not match its image extension.")
    return None, {"width": width, "height": height, "pixel_count": width * height}


def _spec_for(file_name: str) -> _AttachmentSpec:
    extension = _extension(file_name)
    if extension in _LEGACY_OFFICE_EXTENSIONS:
        raise AttachmentValidationError(
            f"{extension} files are not supported. Convert the file to a modern Office format first."
        )
    specs = {
        ".docx": _AttachmentSpec(_WORD_MIME, frozenset({_WORD_MIME}), "document", _extract_docx),
        ".xlsx": _AttachmentSpec(_EXCEL_MIME, frozenset({_EXCEL_MIME}), "spreadsheet", _extract_xlsx),
        ".csv": _AttachmentSpec("text/csv", frozenset({"text/csv", "application/csv", "application/vnd.ms-excel"}), "spreadsheet", _extract_csv),
        ".pdf": _AttachmentSpec("application/pdf", frozenset({"application/pdf"}), "document", _extract_pdf),
        ".pptx": _AttachmentSpec(_POWERPOINT_MIME, frozenset({_POWERPOINT_MIME}), "presentation", _extract_pptx),
        ".png": _AttachmentSpec("image/png", frozenset({"image/png"}), "image", _extract_image),
        ".jpg": _AttachmentSpec("image/jpeg", frozenset({"image/jpeg", "image/jpg"}), "image", _extract_image),
        ".jpeg": _AttachmentSpec("image/jpeg", frozenset({"image/jpeg", "image/jpg"}), "image", _extract_image),
        ".webp": _AttachmentSpec("image/webp", frozenset({"image/webp"}), "image", _extract_image),
    }
    if extension in specs:
        return specs[extension]
    if extension in _TEXT_EXTENSIONS:
        return _AttachmentSpec("text/plain", frozenset({"text/plain", "text/markdown", "application/json", "application/xml", "text/xml"}), "text", _extract_text)
    raise AttachmentValidationError(
        f"{extension or 'Files without an extension'} are not supported as message attachments."
    )


def prepare_attachment(*, file_name: str, mime_type: str, data: bytes) -> PreparedAttachment:
    """Validate an upload and build the bounded projection supplied to the model.

    The original bytes remain the persisted Artifact. Office, PDF, CSV, and
    text formats are projected into deterministic text; supported images keep
    their original bytes so a vision-capable model can consume them.
    """

    resolved_name = _validate_file_name(file_name)
    if not data:
        raise AttachmentValidationError("Attachment content must not be empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentValidationError(
            f"Attachment exceeds the {MAX_ATTACHMENT_BYTES // 1024 // 1024} MB limit."
        )
    resolved_mime = _normalized_mime_type(mime_type)
    spec = _spec_for(resolved_name)
    if resolved_mime != "application/octet-stream" and resolved_mime not in spec.accepted_mime_types:
        raise AttachmentValidationError(
            f"Attachment MIME type '{resolved_mime}' does not match the { _extension(resolved_name) } extension."
        )
    model_text, metadata = spec.extractor(resolved_name, data)
    return PreparedAttachment(
        file_name=resolved_name,
        mime_type=spec.mime_type,
        kind=spec.kind,
        data=data,
        model_text=model_text,
        metadata={"attachment_kind": spec.kind, **metadata},
    )


__all__ = [
    "AttachmentValidationError",
    "MAX_ATTACHMENT_BYTES",
    "MAX_MESSAGE_ATTACHMENT_BYTES",
    "MAX_MESSAGE_ATTACHMENTS",
    "PreparedAttachment",
    "prepare_attachment",
]
