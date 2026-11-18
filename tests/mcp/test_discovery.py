"""The app's discovery file: where the app's socket is, and the token that opens it (Qt-free)."""

import json
import os
from unittest import mock

import pytest

from srim.mcp import discovery
from srim.mcp.discovery import (
    AppEndpoint,
    discovery_path,
    new_token,
    read_endpoint,
    remove_endpoint,
    same_token,
    write_endpoint,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSRIM2_HOME", str(tmp_path / "data"))
    return tmp_path / "data"


def endpoint(token=None, socket="pysrim2-arun-0a1b2c3d"):
    return AppEndpoint(socket, token or new_token(), 4242, "0.13.0")


def test_the_file_is_in_the_app_folder_of_the_data_dir(home):
    assert discovery_path() == home / "app" / "agent.json"


def test_what_is_written_is_read_back(home):
    original = endpoint()
    write_endpoint(original)
    assert read_endpoint() == original


def test_a_missing_file_reads_as_none(home):
    assert read_endpoint() is None


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        "[1, 2, 3]",
        "null",
        '{"socket": "x", "token": "short", "pid": 1, "version": "0"}',
        '{"socket": "", "token": "%s", "pid": 1, "version": "0"}' % ("a" * 64),
        '{"socket": "x", "token": "%s", "pid": true, "version": "0"}' % ("a" * 64),
        '{"socket": "x", "token": "%s", "pid": 0, "version": "0"}' % ("a" * 64),
        '{"socket": "x", "token": "%s", "pid": 1}' % ("a" * 64),
        '{"socket": "x", "token": "%s", "pid": 1, "version": "0"}' % ("G" * 64),
    ],
)
def test_a_broken_file_reads_as_none(home, text):
    discovery_path().parent.mkdir(parents=True)
    discovery_path().write_text(text, encoding="utf-8")
    assert read_endpoint() is None


def test_an_oversized_file_reads_as_none_without_reading_it_all(home):
    discovery_path().parent.mkdir(parents=True)
    discovery_path().write_bytes(b"x" * (200 * 1024))
    assert read_endpoint() is None


def test_an_endpoint_the_reader_would_reject_is_not_written(home):
    with pytest.raises(ValueError):
        write_endpoint(AppEndpoint("", "short", 0, "0"))
    assert not discovery_path().exists()


def test_a_new_write_replaces_a_stale_endpoint(home):
    write_endpoint(endpoint(token="a" * 64))
    fresh = endpoint()
    write_endpoint(fresh)
    assert read_endpoint() == fresh


def test_removing_with_another_token_keeps_the_file(home):
    write_endpoint(endpoint(token="a" * 64))
    remove_endpoint("b" * 64)
    assert read_endpoint() is not None


def test_removing_with_the_token_deletes_the_file(home):
    write_endpoint(endpoint(token="a" * 64))
    remove_endpoint("a" * 64)
    assert not discovery_path().exists()


def test_removing_a_missing_file_is_fine(home):
    remove_endpoint("a" * 64)


def test_a_failed_replace_leaves_the_old_file_and_no_temporary(home):
    old = endpoint()
    write_endpoint(old)

    def fail(*args, **kwargs):
        raise OSError("disk full")

    with mock.patch.object(discovery.os, "replace", fail), pytest.raises(OSError):
        write_endpoint(endpoint())
    assert read_endpoint() == old
    assert [p.name for p in discovery_path().parent.iterdir()] == ["agent.json"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_the_file_is_private(home):
    write_endpoint(endpoint())
    assert discovery_path().stat().st_mode & 0o777 == 0o600


def test_tokens_are_32_random_bytes_in_hex():
    first, second = new_token(), new_token()
    assert len(first) == 64 and first != second
    int(first, 16)  # hex


def test_tokens_compare_exactly():
    assert same_token("a" * 64, "a" * 64)
    assert not same_token("a" * 64, "a" * 63 + "b")
    assert not same_token("a" * 64, None)
    assert not same_token(None, None)


def test_the_file_is_plain_json_with_the_documented_fields(home):
    write_endpoint(endpoint(token="c" * 64))
    data = json.loads(discovery_path().read_text(encoding="utf-8"))
    assert set(data) == {"socket", "token", "pid", "version"}
