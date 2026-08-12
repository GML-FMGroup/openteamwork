"""Contract tests for the reviewed document-generation sandbox image."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_document_sandbox_image_pins_build_time_dependencies() -> None:
    """The image owns Office dependencies; Agent turns never install them."""

    dockerfile = (ROOT / "docker/sandbox/Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "docker/sandbox/requirements-document.txt").read_text(encoding="utf-8")
    package = json.loads((ROOT / "docker/sandbox/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "docker/sandbox/package-lock.json").read_text(encoding="utf-8"))

    assert "libreoffice-impress-nogui" in dockerfile
    assert "libreoffice-calc-nogui" in dockerfile
    assert "libreoffice-writer-nogui" in dockerfile
    assert "poppler-utils" in dockerfile
    assert "python-document-builder" in dockerfile
    assert "pip uninstall --yes pip" in dockerfile
    assert "npm ci" in dockerfile
    assert "node-document-builder" in dockerfile
    assert "rm -rf /usr/share/nodejs/corepack" in dockerfile
    assert "rm -f /usr/bin/corepack" in dockerfile
    assert "markitdown[pptx,xlsx]==" in requirements
    assert "python-pptx==" in requirements
    assert package["dependencies"] == {
        "docx": "9.7.1",
        "pptxgenjs": "4.0.1",
        "react": "19.2.8",
        "react-dom": "19.2.8",
        "react-icons": "5.7.0",
        "sharp": "0.35.3",
    }
    assert package_lock["packages"][""]["dependencies"] == package["dependencies"]
