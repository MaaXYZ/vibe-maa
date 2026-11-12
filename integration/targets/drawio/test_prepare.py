import hashlib
import json
import stat
import zipfile

import pytest

from vibe_maa.targets.drawio import prepare


def make_archive(path):
    with zipfile.ZipFile(path, "w") as package:
        for name in ("index.html", "js/app.min.js", "js/bootstrap.js", "styles/grapheditor.css"):
            package.writestr(name, f"fixture:{name}")
        package.writestr("WEB-INF/unused.jar", "not a static web resource")


def pin_archive(monkeypatch, archive):
    metadata = {
        "release": "unit-test",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive_size": archive.stat().st_size,
        "archive_url": "https://example.invalid/unit-test.war",
    }
    monkeypatch.setattr(prepare, "manifest", lambda: metadata)
    return metadata


def test_verified_archive_is_extracted_and_reused_offline(tmp_path, monkeypatch):
    archive = tmp_path / "upstream.war"
    make_archive(archive)
    metadata = pin_archive(monkeypatch, archive)
    monkeypatch.setattr(prepare.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network"))
    webroot = prepare.provision(tmp_path / "cache", archive)
    assert (webroot / "index.html").read_text() == "fixture:index.html"
    assert not (webroot / "WEB-INF").exists()
    receipt = json.loads((webroot.parent / "receipt.json").read_text())
    assert receipt["archive_sha256"] == metadata["archive_sha256"]
    assert prepare.provision(tmp_path / "cache") == webroot


def test_corrupt_extraction_is_rebuilt_from_verified_archive(tmp_path, monkeypatch):
    archive = tmp_path / "upstream.war"
    make_archive(archive)
    pin_archive(monkeypatch, archive)
    webroot = prepare.provision(tmp_path / "cache", archive)
    (webroot / "index.html").write_text("tampered")
    (webroot / "unexpected.txt").write_text("unexpected")
    prepare.provision(tmp_path / "cache")
    assert (webroot / "index.html").read_text() == "fixture:index.html"
    assert not (webroot / "unexpected.txt").exists()


def test_untrusted_archive_is_not_extracted(tmp_path, monkeypatch):
    archive = tmp_path / "upstream.war"
    make_archive(archive)
    pin_archive(monkeypatch, archive)
    archive.write_bytes(b"wrong release")
    with pytest.raises(prepare.ProvisionError, match="pinned"):
        prepare.provision(tmp_path / "cache", archive)


@pytest.mark.parametrize(
    "name", ["../outside.txt", "/absolute.txt", "C:/windows.txt", "..\\outside.txt"]
)
def test_archive_path_traversal_is_rejected(tmp_path, name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(name, "unsafe")
    with pytest.raises(prepare.ProvisionError, match="Unsafe path"):
        prepare._extract(archive, tmp_path / "output")
    assert not (tmp_path / "outside.txt").exists()


def test_archive_symlink_is_rejected(tmp_path):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("symlink")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(link, "../../outside")
    with pytest.raises(prepare.ProvisionError, match="Unsafe path"):
        prepare._extract(archive, tmp_path / "output")


def test_incomplete_release_is_rejected(tmp_path):
    archive = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("index.html", "incomplete")
    with pytest.raises(prepare.ProvisionError, match="missing required"):
        prepare._extract(archive, tmp_path / "output")
