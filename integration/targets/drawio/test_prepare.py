import hashlib
import json
import stat
import zipfile

import pytest

from vibe_maa.targets import archive as archive_provisioner
from vibe_maa.targets.drawio import prepare


def make_archive(path):
    with zipfile.ZipFile(path, "w") as package:
        for name in ("index.html", "js/app.min.js", "js/bootstrap.js", "styles/grapheditor.css"):
            package.writestr(name, f"fixture:{name}")
        package.writestr("WEB-INF/unused.jar", "not a static web resource")


def archive_pin(archive):
    return {
        "release": "unit-test",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive_size": archive.stat().st_size,
        "archive_url": "https://example.invalid/unit-test.war",
    }


def pin_archive(monkeypatch, archive):
    metadata = archive_pin(archive)
    monkeypatch.setattr(prepare, "manifest", lambda: metadata)
    return metadata


def test_verified_archive_is_extracted_and_reused_offline(tmp_path, monkeypatch):
    archive = tmp_path / "upstream.war"
    make_archive(archive)
    metadata = pin_archive(monkeypatch, archive)
    monkeypatch.setattr(
        archive_provisioner.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network")
    )
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
    receipt = webroot.parent / "receipt.json"
    receipt.write_text("null")
    assert prepare.provision(tmp_path / "cache") == webroot
    assert json.loads(receipt.read_text())["files"]


@pytest.mark.parametrize("warm_cache", [False, True])
def test_untrusted_archive_is_not_extracted(tmp_path, monkeypatch, warm_cache):
    archive = tmp_path / "upstream.war"
    make_archive(archive)
    pin_archive(monkeypatch, archive)
    if warm_cache:
        prepare.provision(tmp_path / "cache", archive)
    archive.write_bytes(b"wrong release")
    with pytest.raises(prepare.ProvisionError, match="pinned"):
        prepare.provision(tmp_path / "cache", archive)


def test_shared_provisioner_supports_a_different_archive_layout(tmp_path):
    archive = tmp_path / "pdfjs.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("web/viewer.html", "viewer")
        package.writestr("build/pdf.mjs", "pdf module")
    metadata = archive_pin(archive)
    cache = tmp_path / "cache"
    options = {"target": "pdfjs", "metadata": metadata, "archive_suffix": ".zip"}
    webroot = archive_provisioner.provision_archive(
        cache, required_files=("web/viewer.html",), archive=archive, **options
    )
    assert (webroot / "web/viewer.html").read_text() == "viewer"
    assert (
        archive_provisioner.provision_archive(
            cache, required_files=("web/viewer.html", "build/pdf.mjs"), **options
        )
        == webroot
    )
    with pytest.raises(prepare.ProvisionError, match="missing required.*app.min.js"):
        archive_provisioner.provision_archive(cache, required_files=("js/app.min.js",), **options)


@pytest.mark.parametrize(
    "name", ["../outside.txt", "/absolute.txt", "C:/windows.txt", "..\\outside.txt"]
)
def test_archive_path_traversal_is_rejected(tmp_path, monkeypatch, name):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(name, "unsafe")
    pin_archive(monkeypatch, archive)
    with pytest.raises(prepare.ProvisionError, match="Unsafe path"):
        prepare.provision(tmp_path / "cache", archive)
    assert not (tmp_path / "outside.txt").exists()


def test_archive_symlink_is_rejected(tmp_path, monkeypatch):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("symlink")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(link, "../../outside")
    pin_archive(monkeypatch, archive)
    with pytest.raises(prepare.ProvisionError, match="Unsafe path"):
        prepare.provision(tmp_path / "cache", archive)


def test_incomplete_release_is_rejected(tmp_path, monkeypatch):
    archive = tmp_path / "incomplete.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("index.html", "incomplete")
    pin_archive(monkeypatch, archive)
    with pytest.raises(prepare.ProvisionError, match="missing required"):
        prepare.provision(tmp_path / "cache", archive)
