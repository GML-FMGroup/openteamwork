from __future__ import annotations

import io
import stat
import zipfile

import pytest
from PIL import Image
from pypdf import PdfWriter

from openppx.runtime.attachment_service import (
    AttachmentValidationError,
    prepare_attachment,
)


def _ooxml(parts: dict[str, str], content_type: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                f'<Override PartName="/document" ContentType="{content_type}"/>'
                "</Types>"
            ),
        )
        for name, value in parts.items():
            archive.writestr(name, value)
    return buffer.getvalue()


def _docx(text: str = "Quarterly summary") -> bytes:
    return _ooxml(
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
            )
        },
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )


def _xlsx() -> bytes:
    return _ooxml(
        {
            "xl/workbook.xml": (
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Revenue" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
            "xl/_rels/workbook.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                "</Relationships>"
            ),
            "xl/sharedStrings.xml": (
                '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<si><t>Month</t></si><si><t>Amount</t></si><si><t>Jan</t></si></sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><f>SUM(B1:B1)</f><v>42</v></c></row>'
                "</sheetData></worksheet>"
            ),
        },
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    )


def _pptx() -> bytes:
    return _ooxml(
        {
            "ppt/presentation.xml": (
                '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
            ),
            "ppt/slides/slide1.xml": (
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                "<p:cSld><a:p><a:r><a:t>Launch plan</a:t></a:r></a:p></p:cSld></p:sld>"
            ),
        },
        "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
    )


def _pdf(text: str = "Hello PDF") -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(value)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(payload)


def _image(image_format: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), color=(100, 150, 200)).save(buffer, image_format)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("file_name", "mime_type", "data", "expected"),
    [
        (
            "brief.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _docx(),
            "Quarterly summary",
        ),
        (
            "table.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx(),
            "Revenue",
        ),
        (
            "deck.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            _pptx(),
            "Launch plan",
        ),
        ("data.csv", "text/csv", b"name,value\nalpha,1\n", "alpha"),
        ("notes.txt", "text/plain", b"hello", "hello"),
        ("report.pdf", "application/pdf", _pdf(), "Hello PDF"),
    ],
)
def test_prepare_attachment_extracts_supported_document_text(
    file_name: str,
    mime_type: str,
    data: bytes,
    expected: str,
) -> None:
    prepared = prepare_attachment(file_name=file_name, mime_type=mime_type, data=data)

    assert prepared.mime_type != "application/octet-stream"
    assert prepared.model_text is not None
    assert expected in prepared.model_text
    assert prepared.metadata["text_char_count"] > 0


def test_prepare_attachment_accepts_octet_stream_for_a_known_extension() -> None:
    prepared = prepare_attachment(
        file_name="brief.docx",
        mime_type="application/octet-stream",
        data=_docx(),
    )

    assert prepared.mime_type.endswith("wordprocessingml.document")


def test_prepare_attachment_reads_word_table_text_and_excel_formulas() -> None:
    table = _ooxml(
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Heading</w:t></w:r></w:p>"
                "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table value</w:t></w:r></w:p>"
                "</w:tc></w:tr></w:tbl></w:body></w:document>"
            )
        },
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )

    word = prepare_attachment(
        file_name="table.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=table,
    )
    excel = prepare_attachment(
        file_name="formula.xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        data=_xlsx(),
    )

    assert "Heading" in word.model_text
    assert "Table value" in word.model_text
    assert "=SUM(B1:B1) (42)" in excel.model_text


@pytest.mark.parametrize(
    ("file_name", "mime_type", "image_format"),
    [
        ("photo.png", "image/png", "PNG"),
        ("photo.jpg", "image/jpeg", "JPEG"),
        ("photo.jpeg", "image/jpeg", "JPEG"),
        ("photo.webp", "image/webp", "WEBP"),
    ],
)
def test_prepare_attachment_keeps_supported_images_for_a_vision_model(
    file_name: str,
    mime_type: str,
    image_format: str,
) -> None:
    prepared = prepare_attachment(
        file_name=file_name,
        mime_type=mime_type,
        data=_image(image_format),
    )

    assert prepared.kind == "image"
    assert prepared.model_text is None
    assert prepared.metadata["width"] == 16
    assert prepared.metadata["height"] == 12


@pytest.mark.parametrize(
    ("file_name", "mime_type", "data", "message"),
    [
        ("legacy.doc", "application/msword", b"legacy", "not supported"),
        ("fake.pdf", "application/pdf", b"not a pdf", "does not match"),
        ("fake.jpg", "image/jpeg", b"not a jpeg", "does not match"),
        ("empty.csv", "text/csv", b"", "empty"),
        ("wrong.pdf", "image/png", b"%PDF-1.4\n", "MIME type"),
        ("nul.txt", "text/plain", b"safe\x00unsafe", "null bytes"),
    ],
)
def test_prepare_attachment_rejects_unsupported_empty_and_spoofed_files(
    file_name: str,
    mime_type: str,
    data: bytes,
    message: str,
) -> None:
    with pytest.raises(AttachmentValidationError, match=message):
        prepare_attachment(file_name=file_name, mime_type=mime_type, data=data)


def test_prepare_attachment_rejects_unsafe_ooxml_archive_members() -> None:
    data = _ooxml(
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>safe</w:t></w:r></w:p></w:body></w:document>"
            ),
            "../escape": "unsafe",
        },
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )

    with pytest.raises(AttachmentValidationError, match="unsafe archive path"):
        prepare_attachment(
            file_name="unsafe.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=data,
        )


def test_prepare_attachment_rejects_ooxml_symlinks_and_xml_entities() -> None:
    symlink_buffer = io.BytesIO()
    with zipfile.ZipFile(symlink_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            (
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Override PartName="/document" '
                'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                "</Types>"
            ),
        )
        archive.writestr("word/document.xml", "<document/>")
        link = zipfile.ZipInfo("word/linked.xml")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "target.xml")

    with pytest.raises(AttachmentValidationError, match="prohibited symlink"):
        prepare_attachment(
            file_name="symlink.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=symlink_buffer.getvalue(),
        )

    entity_document = _ooxml(
        {
            "word/document.xml": (
                '<!DOCTYPE w:document [<!ENTITY leaked SYSTEM "file:///etc/passwd">]>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>&leaked;</w:t></w:r></w:p></w:body></w:document>"
            )
        },
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )
    with pytest.raises(AttachmentValidationError, match="prohibited XML declaration"):
        prepare_attachment(
            file_name="entity.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=entity_document,
        )


def test_prepare_attachment_rejects_a_high_compression_ratio() -> None:
    data = _ooxml(
        {
            "word/document.xml": (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f"<w:body><w:p><w:r><w:t>{'x' * 2_000_000}</w:t></w:r></w:p></w:body></w:document>"
            )
        },
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    )

    with pytest.raises(AttachmentValidationError, match="compression ratio"):
        prepare_attachment(
            file_name="bomb.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=data,
        )


def test_prepare_attachment_rejects_an_ooxml_type_spoof() -> None:
    with pytest.raises(AttachmentValidationError, match="does not match"):
        prepare_attachment(
            file_name="fake.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=_pptx(),
        )


def test_prepare_attachment_rejects_duplicate_archive_members() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("word/document.xml", "<document/>")

    with pytest.raises(AttachmentValidationError, match="duplicate archive"):
        prepare_attachment(
            file_name="duplicate.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            data=buffer.getvalue(),
        )


def test_prepare_attachment_explains_scanned_and_encrypted_pdf_boundaries() -> None:
    blank = PdfWriter()
    blank.add_blank_page(width=100, height=100)
    blank_buffer = io.BytesIO()
    blank.write(blank_buffer)

    with pytest.raises(AttachmentValidationError, match="require OCR"):
        prepare_attachment(file_name="scan.pdf", mime_type="application/pdf", data=blank_buffer.getvalue())

    encrypted = PdfWriter()
    encrypted.add_blank_page(width=100, height=100)
    encrypted.encrypt("secret")
    encrypted_buffer = io.BytesIO()
    encrypted.write(encrypted_buffer)

    with pytest.raises(AttachmentValidationError, match="Encrypted PDF"):
        prepare_attachment(file_name="locked.pdf", mime_type="application/pdf", data=encrypted_buffer.getvalue())


def test_prepare_attachment_enforces_format_specific_resource_limits(monkeypatch) -> None:
    monkeypatch.setattr("openppx.runtime.attachment_service.MAX_SPREADSHEET_CELLS", 1)
    with pytest.raises(AttachmentValidationError, match="more than 1 cells"):
        prepare_attachment(
            file_name="large.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            data=_xlsx(),
        )

    monkeypatch.setattr("openppx.runtime.attachment_service.MAX_PDF_PAGES", 0)
    with pytest.raises(AttachmentValidationError, match="more than 0 pages"):
        prepare_attachment(file_name="long.pdf", mime_type="application/pdf", data=_pdf())

    monkeypatch.setattr("openppx.runtime.attachment_service.MAX_IMAGE_PIXELS", 10)
    with pytest.raises(AttachmentValidationError, match="pixel limit"):
        prepare_attachment(file_name="large.png", mime_type="image/png", data=_image("PNG"))

    monkeypatch.setattr("openppx.runtime.attachment_service.MAX_EXTRACTED_TEXT_CHARS", 4)
    with pytest.raises(AttachmentValidationError, match="more than 4 readable characters"):
        prepare_attachment(file_name="large.txt", mime_type="text/plain", data=b"hello")

    monkeypatch.setattr("openppx.runtime.attachment_service.MAX_ATTACHMENT_BYTES", 4)
    with pytest.raises(AttachmentValidationError, match="exceeds"):
        prepare_attachment(file_name="bytes.txt", mime_type="text/plain", data=b"hello")
