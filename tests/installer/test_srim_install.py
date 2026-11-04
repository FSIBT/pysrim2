import hashlib
import http.server
import os
import threading
import zipfile
from pathlib import Path

import pytest

from srim.installer import srim_install
from srim.installer.assets import Asset
from srim.installer.srim_install import MARKER_NAME, SrimInstallError, install_srim

ZIP_NAME = "srim.2013.zip"
MANUAL_URL = "https://github.com/FSIBT/pysrim2/releases/tag/srim-2013"


def build_zip(path, members):
    """Write a zip with the given {name: bytes} members (names ending in / are dirs)."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def fake_srim_members():
    return {
        "srim/": b"",
        "srim/TRIM.exe": b"MZ fake trim",
        "srim/Data/": b"",
        "srim/Data/LEGAL.rtf": b"{\\rtf1 SRIM legal notice}",
        "srim/SR Module/": b"",
        "srim/SR Module/SR.exe": b"MZ fake sr",
    }


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def fake_zip(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    return build_zip(source / ZIP_NAME, fake_srim_members())


def make_asset(zip_path, urls, *, sha=None, size=None):
    return Asset(
        name=ZIP_NAME,
        sha256=sha or sha256_of(zip_path),
        size=size or zip_path.stat().st_size,
        urls=tuple(urls),
    )


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        server.user_agents.append(self.headers.get("User-Agent"))
        status, body, declared = server.routes.get(self.path, (404, b"not found", None))
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(declared if declared is not None else len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    """Local HTTP server on 127.0.0.1.

    Set server.routes[path] = (status, body, declared_length) to serve a response.
    """
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.routes = {}
    server.user_agents = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def url_for(server, path):
    host, port = server.server_address
    return f"http://{host}:{port}{path}"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "pysrim2-home"))


def leftover_temp_dirs(parent):
    return sorted(p.name for p in parent.glob(".srim-install-*"))


def test_installs_contents_of_top_folder(tmp_path, fake_zip):
    asset = make_asset(fake_zip, [fake_zip.as_uri()])
    dest = tmp_path / "install"

    result = install_srim(dest, asset, cache=tmp_path / "cache")

    assert result == dest
    assert (dest / "TRIM.exe").read_bytes() == b"MZ fake trim"
    assert (dest / "Data" / "LEGAL.rtf").is_file()
    assert (dest / "SR Module" / "SR.exe").is_file()
    assert not (dest / "srim").exists()
    assert (dest / "TRIMAUTO").read_text().strip() == "1"
    assert (dest / ".pysrim2-installed").read_text().strip() == asset.sha256
    assert leftover_temp_dirs(tmp_path) == []


def test_first_url_404_falls_back_to_second(tmp_path, fake_zip, http_server):
    http_server.routes["/good.zip"] = (200, fake_zip.read_bytes(), None)
    asset = make_asset(
        fake_zip, [url_for(http_server, "/missing.zip"), url_for(http_server, "/good.zip")]
    )
    dest = tmp_path / "install"

    install_srim(dest, asset, cache=tmp_path / "cache")

    assert (dest / "TRIM.exe").is_file()


def test_hash_mismatch_raises_and_leaves_no_dest(tmp_path, fake_zip):
    wrong = "0" * 64
    asset = make_asset(fake_zip, [fake_zip.as_uri()], sha=wrong)
    dest = tmp_path / "install"
    cache = tmp_path / "cache"

    with pytest.raises(SrimInstallError) as info:
        install_srim(dest, asset, cache=cache)

    assert wrong in str(info.value)
    assert sha256_of(fake_zip) in str(info.value)
    assert not dest.exists()
    assert leftover_temp_dirs(tmp_path) == []
    assert list(cache.glob("*.part")) == []


def test_partial_download_raises_and_leaves_no_dest(tmp_path, fake_zip, http_server):
    body = fake_zip.read_bytes()
    # Declares more bytes than it sends, then closes the connection.
    http_server.routes["/short.zip"] = (200, body[: len(body) // 2], len(body))
    asset = make_asset(fake_zip, [url_for(http_server, "/short.zip")])
    dest = tmp_path / "install"
    cache = tmp_path / "cache"

    with pytest.raises(SrimInstallError):
        install_srim(dest, asset, cache=cache)

    assert not dest.exists()
    assert leftover_temp_dirs(tmp_path) == []
    assert list(cache.glob("*.part")) == []
    assert not (cache / ZIP_NAME).exists()


def test_corrupt_cached_file_is_deleted_and_redownloaded(tmp_path, fake_zip):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / ZIP_NAME).write_bytes(b"garbage")
    asset = make_asset(fake_zip, [fake_zip.as_uri()])
    dest = tmp_path / "install"

    install_srim(dest, asset, cache=cache)

    assert (dest / "TRIM.exe").is_file()
    assert sha256_of(cache / ZIP_NAME) == asset.sha256


def test_second_call_is_a_noop(tmp_path, fake_zip, monkeypatch):
    asset = make_asset(fake_zip, [fake_zip.as_uri()])
    dest = tmp_path / "install"
    cache = tmp_path / "cache"
    install_srim(dest, asset, cache=cache)

    def no_network(*args, **kwargs):
        raise AssertionError("network used on a no-op install")

    monkeypatch.setattr(srim_install.urllib.request, "urlopen", no_network)

    assert install_srim(dest, asset, cache=cache) == dest


def test_verified_cache_is_reused_for_another_dest(tmp_path, fake_zip):
    asset = make_asset(fake_zip, [fake_zip.as_uri()])
    cache = tmp_path / "cache"
    install_srim(tmp_path / "first", asset, cache=cache)
    fake_zip.unlink()  # the source is gone; only the cached copy can satisfy this install

    install_srim(tmp_path / "second", asset, cache=cache)

    assert (tmp_path / "second" / "TRIM.exe").is_file()


def test_default_cache_lives_in_data_dir(tmp_path, fake_zip):
    asset = make_asset(fake_zip, [fake_zip.as_uri()])
    install_srim(tmp_path / "install", asset)

    assert (tmp_path / "pysrim2-home" / "cache" / ZIP_NAME).is_file()


def test_dest_with_space_works(tmp_path, fake_zip):
    asset = make_asset(fake_zip, [fake_zip.as_uri()])
    dest = tmp_path / "my srim dir"

    install_srim(dest, asset, cache=tmp_path / "cache")

    assert (dest / "TRIM.exe").is_file()


def test_user_agent_identifies_pysrim2(tmp_path, fake_zip, http_server):
    http_server.routes["/good.zip"] = (200, fake_zip.read_bytes(), None)
    asset = make_asset(fake_zip, [url_for(http_server, "/good.zip")])

    install_srim(tmp_path / "install", asset, cache=tmp_path / "cache")

    assert http_server.user_agents
    assert http_server.user_agents[0].startswith("pysrim2/")


@pytest.mark.parametrize(
    "member",
    ["../evil.txt", "srim/../../evil.txt", "/abs/evil.txt", "srim\\..\\..\\evil.txt"],
)
def test_zip_path_traversal_is_rejected(tmp_path, member):
    source = tmp_path / "source"
    source.mkdir()
    zip_path = build_zip(source / ZIP_NAME, {"srim/TRIM.exe": b"MZ", member: b"owned"})
    asset = make_asset(zip_path, [zip_path.as_uri()])
    dest = tmp_path / "work" / "install"

    with pytest.raises(SrimInstallError, match="unsafe"):
        install_srim(dest, asset, cache=tmp_path / "cache")

    assert not dest.exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "work" / "evil.txt").exists()
    assert leftover_temp_dirs(tmp_path / "work") == []


def test_existing_dest_without_marker_is_refused(tmp_path, fake_zip):
    dest = tmp_path / "install"
    dest.mkdir()
    (dest / "user-file.txt").write_text("keep me")
    asset = make_asset(fake_zip, ["file:///does/not/exist.zip"])

    with pytest.raises(SrimInstallError, match="PYSRIM2_SRIM_DIR"):
        install_srim(dest, asset, cache=tmp_path / "cache")

    assert (dest / "user-file.txt").read_text() == "keep me"
    assert sorted(p.name for p in dest.iterdir()) == ["user-file.txt"]


def test_all_urls_failing_gives_manual_instructions(tmp_path, fake_zip, http_server):
    urls = [url_for(http_server, "/a.zip"), url_for(http_server, "/b.zip")]
    asset = make_asset(fake_zip, urls)
    dest = tmp_path / "my install"

    with pytest.raises(SrimInstallError) as info:
        install_srim(dest, asset, cache=tmp_path / "cache")

    message = str(info.value)
    for url in urls:
        assert url in message
    assert f"Download {ZIP_NAME} manually from {MANUAL_URL}" in message
    assert f"check its SHA-256 ({asset.sha256})" in message
    assert (
        "unzip it and copy the contents of its top-level srim/ folder "
        f"into {dest}, or unzip it anywhere and set PYSRIM2_SRIM_DIR to that srim/ folder."
    ) in message
    assert message.count("SRIM © SRIM.com (James F. Ziegler)") == 1
    assert message.endswith(
        "SRIM © SRIM.com (James F. Ziegler) — free for non-commercial use only; see LEGAL.rtf"
    )
    assert not dest.exists()


def test_corrupt_zip_with_matching_hash_raises_and_leaves_no_dest(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    bad = source / ZIP_NAME
    bad.write_bytes(b"not a zip at all")
    asset = make_asset(bad, [bad.as_uri()])

    with pytest.raises(SrimInstallError):
        install_srim(tmp_path / "install", asset, cache=tmp_path / "cache")

    assert not (tmp_path / "install").exists()


def test_wrong_hash_from_first_url_falls_back_to_good_second(tmp_path, fake_zip, http_server):
    good = fake_zip.read_bytes()
    http_server.routes["/bad.zip"] = (200, b"\0" * len(good), None)  # same size, wrong bytes
    http_server.routes["/good.zip"] = (200, good, None)
    asset = make_asset(
        fake_zip, [url_for(http_server, "/bad.zip"), url_for(http_server, "/good.zip")]
    )
    dest = tmp_path / "install"
    cache = tmp_path / "cache"

    install_srim(dest, asset, cache=cache)

    assert (dest / "TRIM.exe").is_file()
    assert sorted(p.name for p in cache.iterdir()) == [ZIP_NAME]
    assert sha256_of(cache / ZIP_NAME) == asset.sha256
    assert leftover_temp_dirs(tmp_path) == []


def test_manual_copy_with_trim_exe_is_accepted_without_writing(tmp_path, fake_zip, monkeypatch):
    dest = tmp_path / "manual"
    (dest / "Data").mkdir(parents=True)
    (dest / "TRIM.exe").write_bytes(b"MZ manual copy")
    asset = make_asset(fake_zip, [fake_zip.as_uri()])

    def no_network(*args, **kwargs):
        raise AssertionError("network used for an existing install")

    monkeypatch.setattr(srim_install.urllib.request, "urlopen", no_network)

    assert install_srim(dest, asset, cache=tmp_path / "cache") == dest
    assert (dest / "TRIM.exe").read_bytes() == b"MZ manual copy"
    assert not (dest / MARKER_NAME).exists()
    assert not (dest / "TRIMAUTO").exists()


def test_empty_dest_directory_is_installed_into(tmp_path, fake_zip):
    dest = tmp_path / "empty"
    dest.mkdir()
    asset = make_asset(fake_zip, [fake_zip.as_uri()])

    install_srim(dest, asset, cache=tmp_path / "cache")

    assert (dest / "TRIM.exe").is_file()


def test_replace_os_error_becomes_install_error(tmp_path, fake_zip, monkeypatch):
    dest = tmp_path / "install"
    asset = make_asset(fake_zip, [fake_zip.as_uri()])
    real_replace = os.replace

    def failing_replace(src, dst):
        if Path(dst) == dest:
            raise OSError(28, "No space left on device")
        return real_replace(src, dst)

    monkeypatch.setattr(srim_install.os, "replace", failing_replace)

    with pytest.raises(SrimInstallError, match="No space left on device") as info:
        install_srim(dest, asset, cache=tmp_path / "cache")

    assert str(dest) in str(info.value)
    assert not dest.exists()
    assert leftover_temp_dirs(tmp_path) == []


def test_extract_os_error_becomes_install_error(tmp_path, fake_zip, monkeypatch):
    dest = tmp_path / "install"
    asset = make_asset(fake_zip, [fake_zip.as_uri()])

    def locked(self, path=None, *args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(zipfile.ZipFile, "extractall", locked)

    with pytest.raises(SrimInstallError, match="Permission denied") as info:
        install_srim(dest, asset, cache=tmp_path / "cache")

    assert "could not extract srim.2013.zip" in str(info.value)
    assert not dest.exists()
    assert leftover_temp_dirs(tmp_path) == []
