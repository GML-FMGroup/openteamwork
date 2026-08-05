"""Security and provenance tests for Extension Source Adapters."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from openppx.extensions import ExtensionError, ExtensionSourceRef
from openppx.extensions.sources import (
    BuiltinSourceAdapter,
    CatalogArtifact,
    CatalogSourceAdapter,
    GitSourceAdapter,
    LocalArchiveSourceAdapter,
    LocalDirectorySourceAdapter,
    NpmSourceAdapter,
    SourceLimits,
    StagingStore,
)


def _npm_plugin_archive(path: Path) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        payloads = {
            "package/.agent-plugin/plugin.json": '{"name":"demo-plugin","description":"Demo plugin."}',
            "package/skills/demo/SKILL.md": "---\nname: demo\ndescription: Demo skill.\n---\n",
        }
        for name, payload in payloads.items():
            data = payload.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return path


def _skill(root: Path, name: str = "demo", *, body: str = "# Demo\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Demo skill.\n---\n\n{body}",
        encoding="utf-8",
    )
    return root


def _zip_skill(path: Path, *, name: str = "demo") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", f"---\nname: {name}\ndescription: Demo skill.\n---\n")
        archive.writestr("scripts/run.sh", "#!/bin/sh\necho demo\n")
    return path


def test_local_directory_is_copied_and_digest_is_source_independent(tmp_path: Path) -> None:
    source = _skill(tmp_path / "source")
    store = StagingStore(tmp_path / "node", limits=SourceLimits())
    staged = LocalDirectorySourceAdapter().stage(
        ExtensionSourceRef(type="local_directory", locator=str(source)),
        store,
    )
    expected = staged.digest

    shutil.rmtree(source)

    assert staged.content_root.joinpath("SKILL.md").is_file()
    assert staged.digest == expected
    assert staged.source.locator == "local-directory:source"
    assert staged.file_count == 1


def test_builtin_source_requires_an_explicit_registered_identity(tmp_path: Path) -> None:
    builtin = _skill(tmp_path / "builtin")
    store = StagingStore(tmp_path / "node")
    adapter = BuiltinSourceAdapter({"demo": builtin})

    staged = adapter.stage(ExtensionSourceRef(type="builtin", locator="demo"), store)

    assert staged.source.locator == "builtin:demo"
    with pytest.raises(ExtensionError, match="not registered") as exc_info:
        adapter.stage(ExtensionSourceRef(type="builtin", locator="missing"), store)
    assert exc_info.value.code == "invalid_source"


def test_local_directory_rejects_symlinked_content(tmp_path: Path) -> None:
    source = _skill(tmp_path / "source")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        (source / "escape.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ExtensionError) as exc_info:
        LocalDirectorySourceAdapter().stage(
            ExtensionSourceRef(type="local_directory", locator=str(source)),
            StagingStore(tmp_path / "node"),
        )

    assert exc_info.value.code == "unsafe_path"


def test_local_directory_rejects_hardlinked_content(tmp_path: Path) -> None:
    source = _skill(tmp_path / "source")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        os.link(outside, source / "linked.txt")
    except OSError as exc:
        pytest.skip(f"hardlink unavailable: {exc}")

    with pytest.raises(ExtensionError) as exc_info:
        LocalDirectorySourceAdapter().stage(
            ExtensionSourceRef(type="local_directory", locator=str(source)),
            StagingStore(tmp_path / "node"),
        )

    assert exc_info.value.code == "unsafe_path"


def test_local_sources_reject_symlinked_roots(tmp_path: Path) -> None:
    source = _skill(tmp_path / "source")
    archive = _zip_skill(tmp_path / "source.zip")
    directory_link = tmp_path / "directory-link"
    archive_link = tmp_path / "archive-link.zip"
    try:
        directory_link.symlink_to(source, target_is_directory=True)
        archive_link.symlink_to(archive)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(ExtensionError) as directory_error:
        LocalDirectorySourceAdapter().stage(
            ExtensionSourceRef(type="local_directory", locator=str(directory_link)),
            StagingStore(tmp_path / "directory-node"),
        )
    with pytest.raises(ExtensionError) as archive_error:
        LocalArchiveSourceAdapter().stage(
            ExtensionSourceRef(type="local_archive", locator=str(archive_link)),
            StagingStore(tmp_path / "archive-node"),
        )

    assert directory_error.value.code == "unsafe_path"
    assert archive_error.value.code == "unsafe_path"


@pytest.mark.parametrize("unsafe_name", ["../escape", "/absolute", "C:/drive"])
def test_archive_rejects_unsafe_paths(tmp_path: Path, unsafe_name: str) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: demo\ndescription: Demo.\n---\n")
        archive.writestr(unsafe_name, "bad")

    with pytest.raises(ExtensionError) as exc_info:
        LocalArchiveSourceAdapter().stage(
            ExtensionSourceRef(type="local_archive", locator=str(archive_path)),
            StagingStore(tmp_path / "node"),
        )

    assert exc_info.value.code == "unsafe_path"


def test_archive_rejects_symlink_duplicate_and_limits(tmp_path: Path) -> None:
    symlink_archive = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink_archive, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: demo\ndescription: Demo.\n---\n")
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "SKILL.md")
    with pytest.raises(ExtensionError, match="unsafe"):
        LocalArchiveSourceAdapter().stage(
            ExtensionSourceRef(type="local_archive", locator=str(symlink_archive)),
            StagingStore(tmp_path / "node"),
        )

    duplicate_archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate_archive, "w") as archive:
        archive.writestr("SKILL.md", "first")
        archive.writestr("SKILL.md", "second")
    with pytest.raises(ExtensionError) as duplicate:
        LocalArchiveSourceAdapter().stage(
            ExtensionSourceRef(type="local_archive", locator=str(duplicate_archive)),
            StagingStore(tmp_path / "node"),
        )
    assert duplicate.value.code == "unsafe_path"

    limited_archive = _zip_skill(tmp_path / "limited.zip")
    with pytest.raises(ExtensionError) as limited:
        LocalArchiveSourceAdapter().stage(
            ExtensionSourceRef(type="local_archive", locator=str(limited_archive)),
            StagingStore(tmp_path / "node", limits=SourceLimits(max_files=1)),
        )
    assert limited.value.code == "archive_limit_exceeded"


def test_git_source_resolves_and_records_a_fixed_commit(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is unavailable")
    repository = _skill(tmp_path / "repository")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "SKILL.md"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=OpenPPX Test",
            "-c",
            "user.email=test@openppx.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    staged = GitSourceAdapter().stage(
        ExtensionSourceRef(type="git", locator=str(repository), revision=commit),
        StagingStore(tmp_path / "node"),
    )

    assert staged.source.revision == commit
    assert staged.source.locator.startswith("git:")
    assert staged.content_root.joinpath("SKILL.md").is_file()


def test_git_source_locator_redacts_credentials_and_query() -> None:
    from openppx.extensions.sources.git import _git_locator

    assert _git_locator("https://token@example.com/org/repo.git?auth=secret") == (
        "https://example.com/org/repo"
    )
    assert _git_locator("git@example.com:org/repo.git") == "example.com:org/repo"


def test_npm_source_requires_exact_version_and_safely_stages_package(tmp_path: Path) -> None:
    archive = _npm_plugin_archive(tmp_path / "demo-plugin-1.2.3.tgz")
    fake_npm = tmp_path / "npm"
    fake_npm.write_text(
        "#!/bin/sh\n"
        'destination=""\n'
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--pack-destination" ]; then destination="$2"; shift 2; else shift; fi\n'
        "done\n"
        f'cp "{archive}" "$destination/demo-plugin-1.2.3.tgz"\n'
        "printf '[{\"filename\":\"demo-plugin-1.2.3.tgz\"}]'\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o755)
    staged = NpmSourceAdapter(npm_binary=str(fake_npm)).stage(
        ExtensionSourceRef(type="npm", locator="@openppx/demo-plugin", version="1.2.3"),
        StagingStore(tmp_path / "node", required_root_file=".agent-plugin/plugin.json"),
    )
    assert staged.source.type == "npm"
    assert staged.source.locator == "npm:@openppx/demo-plugin"
    assert staged.source.version == "1.2.3"
    assert staged.content_root.joinpath(".agent-plugin/plugin.json").is_file()

    with pytest.raises(ExtensionError, match="exact version"):
        NpmSourceAdapter(npm_binary=str(fake_npm)).stage(
            ExtensionSourceRef(type="npm", locator="@openppx/demo-plugin", version="latest"),
            StagingStore(tmp_path / "invalid", required_root_file=".agent-plugin/plugin.json"),
        )


@dataclass(frozen=True)
class _Catalog:
    artifact: CatalogArtifact

    def fetch(self, extension_id: str, version: str | None) -> CatalogArtifact:
        assert extension_id == "demo"
        assert version == "1.2.3"
        return self.artifact


def test_catalog_requires_fixed_version_and_matching_digest(tmp_path: Path) -> None:
    archive_path = _zip_skill(tmp_path / "demo.zip")
    baseline = LocalArchiveSourceAdapter().stage(
        ExtensionSourceRef(type="local_archive", locator=str(archive_path)),
        StagingStore(tmp_path / "baseline"),
    )
    artifact = CatalogArtifact(
        archive_path=archive_path,
        version="1.2.3",
        revision="catalog-revision-7",
        digest=baseline.digest,
    )
    staged = CatalogSourceAdapter("fixture", _Catalog(artifact)).stage(
        ExtensionSourceRef(type="catalog", locator="demo", provider="fixture", version="1.2.3"),
        StagingStore(tmp_path / "node"),
    )
    assert staged.digest == baseline.digest
    assert staged.source.locator == "catalog:fixture/demo"

    bad = CatalogArtifact(
        archive_path=archive_path,
        version="1.2.3",
        revision="catalog-revision-8",
        digest="sha256:" + hashlib.sha256(b"wrong").hexdigest(),
    )
    with pytest.raises(ExtensionError) as exc_info:
        CatalogSourceAdapter("fixture", _Catalog(bad)).stage(
            ExtensionSourceRef(type="catalog", locator="demo", provider="fixture", version="1.2.3"),
            StagingStore(tmp_path / "other"),
        )
    assert exc_info.value.code == "digest_mismatch"
