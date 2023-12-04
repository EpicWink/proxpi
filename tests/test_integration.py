"""Test ``proxpi`` server."""

import os
import hashlib
import logging
import pathlib
import warnings
import posixpath
import contextlib
import typing as t
from urllib import parse as urllib_parse
from unittest import mock

import flask
import pytest
import requests
import proxpi.server
import packaging.specifiers

from . import _utils

proxpi_server = proxpi.server

logging.root.setLevel(logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)

mock_index_response_is_json = False


@contextlib.contextmanager
def set_mock_index_response_is_json(value: t.Union[bool, str]):
    global mock_index_response_is_json

    original_mock_index_response_is_json = mock_index_response_is_json
    mock_index_response_is_json = value
    try:
        yield
    finally:
        mock_index_response_is_json = original_mock_index_response_is_json


def make_mock_index_app(index_name: str) -> flask.Flask:
    """Construct a mock package index app.

    Args:
        index_name: index name in test data indexes directory

    Returns:
        WSGI app for Python package simple repository index
    """

    app = flask.Flask("proxpi-tests", root_path=os.path.split(__file__)[0])
    indexes_dir_relative_path = pathlib.PurePath("data") / "indexes"

    @app.route("/")
    def list_projects() -> flask.Response:
        if mock_index_response_is_json:
            file_name = "index.json"
            mime_type = "application/vnd.pypi.simple.v1+json"
        else:
            file_name = "index.html"
            mime_type = "text/html"

        return flask.send_from_directory(
            directory=indexes_dir_relative_path,
            path=pathlib.PurePath(index_name) / file_name,
            mimetype=mime_type,
        )

    @app.route("/<name>/")
    def get_project(name: str) -> flask.Response:
        if mock_index_response_is_json:
            stem = "yanked" if mock_index_response_is_json == "yanked" else "index"
            file_name = f"{stem}.json"
            mime_type = "application/vnd.pypi.simple.v1+json"
        else:
            file_name = "index.html"
            mime_type = "text/html"

        return flask.send_from_directory(
            directory=indexes_dir_relative_path,
            path=pathlib.PurePath(index_name) / name / file_name,
            mimetype=mime_type,
        )

    @app.route("/<project_name>/<file_name>")
    def get_file(project_name: str, file_name: str) -> flask.Response:
        return flask.send_from_directory(
            directory=indexes_dir_relative_path,
            path=pathlib.PurePath(index_name) / project_name / file_name,
            mimetype="application/octect-stream",
        )

    return app


@pytest.fixture(scope="module")
def mock_root_index():
    app = make_mock_index_app(index_name="root")
    yield from _utils.make_server(app)


@pytest.fixture(scope="module")
def mock_extra_index():
    app = make_mock_index_app(index_name="extra")
    yield from _utils.make_server(app)


@pytest.fixture(scope="module")
def server(mock_root_index, mock_extra_index):
    session = proxpi.server.cache.root_cache.session
    # noinspection PyProtectedMember
    root_patch = mock.patch.object(
        proxpi.server.cache,
        "root_cache",
        proxpi.server.cache._index_cache_cls(f"{mock_root_index}/", 15, session),
    )
    # noinspection PyProtectedMember
    extras_patch = mock.patch.object(
        proxpi.server.cache,
        "extra_caches",
        [proxpi.server.cache._index_cache_cls(f"{mock_extra_index}/", 10, session)],
    )
    with root_patch, extras_patch:
        yield from _utils.make_server(proxpi_server.app)


@pytest.fixture
def clear_index_cache(server):
    proxpi.server.cache.invalidate_list()


@pytest.fixture
def clear_projects_cache(server):
    for project_name in proxpi.server.cache.list_projects():
        proxpi.server.cache.invalidate_project(project_name)


@pytest.mark.parametrize("accept", ["text/html", "application/vnd.pypi.simple.v1+html"])
@pytest.mark.parametrize("index_json_response", [False, True])
def test_list(server, accept, index_json_response, clear_index_cache):
    """Test getting package list."""
    with set_mock_index_response_is_json(index_json_response):
        response = requests.get(f"{server}/index/", headers={"Accept": accept})
    response.raise_for_status()

    assert response.headers["Content-Type"][:9] == "text/html"
    assert any(
        response.headers["Content-Encoding"] == a
        for a in ["gzip", "deflate"]
        if a in response.request.headers["Accept-Encoding"]
    )
    vary = {v.strip() for v in response.headers["Vary"].split(",")}
    assert "Accept-Encoding" in vary
    assert "Accept" in vary

    parser = _utils.IndexParser.from_text(response.text)
    assert parser.declaration == "DOCTYPE html"
    assert parser.title.strip()  # required for valid HTML5
    assert parser.anchors
    for text, attributes in parser.anchors:
        (href,) = (v for k, v in attributes if k == "href")
        assert href == f"{text}/"


@pytest.mark.parametrize("accept", [
    "application/vnd.pypi.simple.v1+json",
    "application/vnd.pypi.simple.latest+json",
])
@pytest.mark.parametrize("index_json_response", [False, True])
def test_list_json(
    server, accept, index_json_response, mock_root_index, clear_index_cache
):
    """Test getting package list with JSON API."""
    with set_mock_index_response_is_json(index_json_response):
        response = requests.get(f"{server}/index/", headers={"Accept": accept})
    assert response.status_code == 200
    assert response.headers["Content-Type"][:35] == (
        "application/vnd.pypi.simple.v1+json"
    )
    vary = {v.strip() for v in response.headers["Vary"].split(",")}
    assert "Accept-Encoding" in vary
    assert "Accept" in vary
    assert response.json()["meta"] == {"api-version": "1.0"}
    assert any(p == {"name": "proxpi"} for p in response.json()["projects"])


@pytest.mark.parametrize("project", ["proxpi", "numpy", "scipy"])
@pytest.mark.parametrize("accept", [
    "text/html", "application/vnd.pypi.simple.v1+html", "*/*"
])
@pytest.mark.parametrize("index_json_response", [False, True, "yanked"])
def test_package(server, project, accept, index_json_response, clear_projects_cache):
    """Test getting package files."""
    project_url = f"{server}/index/{project}/"
    with set_mock_index_response_is_json(index_json_response):
        response = requests.get(project_url, headers={"Accept": accept})
    response.raise_for_status()

    assert response.headers["Content-Type"][:9] == "text/html"
    assert any(
        response.headers["Content-Encoding"] == a
        for a in ["gzip", "deflate"]
        if a in response.request.headers["Accept-Encoding"]
    )
    vary = {v.strip() for v in response.headers["Vary"].split(",")}
    assert "Accept-Encoding" in vary
    assert "Accept" in vary

    parser = _utils.IndexParser.from_text(response.text)
    assert parser.declaration == "DOCTYPE html"
    assert parser.title == project
    assert parser.anchors

    file_downloaded = False
    for text, attributes in parser.anchors:
        (href,) = (v for k, v in attributes if k == "href")
        href_parsed: urllib_parse.SplitResult = urllib_parse.urlsplit(href)
        href_parsed_stripped = href_parsed._replace(fragment="")
        href_stripped = href_parsed_stripped.geturl()
        assert href_stripped == text

        if href_parsed.fragment and not file_downloaded:
            file_response = requests.get(urllib_parse.urljoin(project_url, href))
            file_response.raise_for_status()

            for part in href_parsed.fragment.split(","):
                hash_name, hash_value = part.split("=")
                hash_method = getattr(hashlib, hash_name)
                file_hash = hash_method(file_response.content)
                assert hash_value == file_hash.hexdigest()
                file_downloaded = True

        if any(k == "data-gpg-sig" for k, _ in attributes):
            (has_gpg_sig,) = (v for k, v in attributes if k == "data-gpg-sig")
            gpg_response = requests.get(urllib_parse.urljoin(
                project_url, href_stripped + ".asc"
            ))
            if has_gpg_sig:
                gpg_response.raise_for_status()
            else:
                assert gpg_response.status_code == 404

        if any(k == "data-dist-info-metadata" for k, _ in attributes):
            (value,) = (v for k, v in attributes if k == "data-dist-info-metadata")
            (expected,) = (v for k, v in attributes if k == "data-core-metadata")
            assert value == expected

        if any(k == "data-core-metadata" for k, _ in attributes):
            (expected_core_metadata_hash,) = (
                v for k, v in attributes if k == "data-core-metadata"
            )
            core_metadata_response = requests.get(urllib_parse.urljoin(
                project_url, href_stripped + ".metadata"
            ))
            core_metadata_response.raise_for_status()
            if expected_core_metadata_hash and expected_core_metadata_hash != "true":
                hash_name, expected_hash_value = expected_core_metadata_hash.split("=")
                core_metadata_hash_value = hashlib.new(
                    hash_name, core_metadata_response.content
                ).hexdigest()
                assert core_metadata_hash_value == expected_hash_value

        if any(k == "data-requires-python" for k, _ in attributes):
            (python_requirement,) = (
                v for k, v in attributes if k == "data-requires-python"
            )
            specifier = packaging.specifiers.SpecifierSet(python_requirement)
            assert specifier.filter(["1.2", "2.7", "3.3", "3.7", "3.10", "3.12"])

    attributes_by_filename = dict(parser.anchors)
    if project == "proxpi":
        attributes = attributes_by_filename["proxpi-1.0.0.tar.gz"]
        assert not any(v for k, v in attributes if k == "data-requires-python")

    elif project == "numpy":
        assert any(k == "data-yanked" for k, _ in attributes_by_filename.pop(
            "numpy-1.23.1-cp310-cp310-win_amd64.whl",
        ))  # fmt: skip

        for filename, attributes in attributes_by_filename.items():
            assert not any(k == "data-yanked" for k, _ in attributes), attributes


@pytest.mark.parametrize("project", ["proxpi", "numpy", "scipy"])
@pytest.mark.parametrize("accept", [
    "application/vnd.pypi.simple.v1+json",
    "application/vnd.pypi.simple.latest+json",
])
@pytest.mark.parametrize("query_format", [False, True])
@pytest.mark.parametrize("index_json_response", [False, True, "yanked"])
def test_package_json(
    server, project, accept, query_format, index_json_response, clear_projects_cache
):
    """Test getting package files with JSON API."""
    params = None
    headers = None
    if query_format:
        params = {"format": accept}
    else:
        headers = {"Accept": accept}
    project_url = f"{server}/index/{project}/"

    with set_mock_index_response_is_json(index_json_response):
        response = requests.get(project_url, params=params, headers=headers)

    assert response.status_code == 200
    assert response.headers["Content-Type"][:35] == (
        "application/vnd.pypi.simple.v1+json"
    )
    vary = {v.strip() for v in response.headers["Vary"].split(",")}
    assert "Accept-Encoding" in vary
    assert "Accept" in vary

    response_data = response.json()
    assert response_data["meta"] == {"api-version": "1.0"}
    assert response_data["name"] == project

    for file in response_data["files"]:
        assert file["url"]
        assert file["filename"] == file["url"]
        assert isinstance(file["hashes"], dict)

        assert not file.get("dist-info-metadata")

        url_parts: urllib_parse.SplitResult = urllib_parse.urlsplit(file["url"])
        url_parts_stripped = url_parts._replace(fragment="")
        url_stripped = url_parts_stripped.geturl()
        assert url_stripped == file["filename"]

        if file.get("core-metadata"):
            core_metadata_response = requests.get(
                urllib_parse.urljoin(project_url, url_stripped + ".metadata"),
            )
            core_metadata_response.raise_for_status()

            if isinstance(file["core-metadata"], dict):
                for hash_name, expected_hash_value in file["core-metadata"].items():
                    core_metadata_hash_value = hashlib.new(
                        hash_name, core_metadata_response.content
                    ).hexdigest()
                    assert core_metadata_hash_value == expected_hash_value

    files_by_filename = {f["filename"]: f for f in response_data["files"]}
    if project == "proxpi":
        assert not files_by_filename["proxpi-1.0.0.tar.gz"].get("requires-python")

    elif project == "numpy":
        yanked_file = files_by_filename.pop("numpy-1.23.1-cp310-cp310-win_amd64.whl")
        assert yanked_file.get("yanked")
        assert not any(f.get("yanked") for f in files_by_filename.values())


def test_package_unknown_accept(server):
    """Test getting package files raises 406 with unknown accept-type."""
    response = requests.get(
        f"{server}/index/proxpi/",
        headers={"Accept": "application/vnd.pypi.simple.v42+xml"}
    )
    assert response.status_code == 406


def test_invalidate_list(server):
    """Test invalidating package list cache."""
    response = requests.delete(f"{server}/cache/list")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "data": None}


def test_invalidate_package(server):
    """Test invalidating package list cache."""
    response = requests.delete(f"{server}/cache/jinja2")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "data": None}


def test_health(server):
    """Test health endpoint."""
    response = requests.get(f"{server}/health")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.json() == {"status": "success", "data": None}


def test_nonexistant_package(server):
    """Test getting non-existant package file list."""
    response = requests.get(f"{server}/index/ultraspampackage/")
    assert response.status_code == 404


def test_nonexistant_file(server):
    """Test getting non-existant package file."""
    response = requests.get(f"{server}/index/ultraspampackage/spam.whl")
    assert response.status_code == 404


def test_nonexistant_file_from_existing_package(server):
    """Test getting non-existant package file from existing package."""
    response = requests.get(f"{server}/index/Jinja2/nonexistant.whl")
    assert response.status_code == 404


@pytest.fixture
def readonly_package_dir(tmp_path):
    package_dir = tmp_path / "packages"
    package_dir.touch()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pytest.PytestUnhandledThreadExceptionWarning)
        yield package_dir


def test_download_file_failed(mock_root_index, server, readonly_package_dir):
    """Test getting package file when caching failed."""
    cache_patch = mock.patch.object(proxpi_server.cache.file_cache, "_files", {})
    dir_patch = mock.patch.object(
        proxpi_server.cache.file_cache, "cache_dir", str(readonly_package_dir)
    )
    with cache_patch, dir_patch:
        response = requests.get(
            f"{server}/index/numpy/numpy-1.23.1.tar.gz",
            allow_redirects=False,
        )
    assert response.status_code // 100 == 3
    url_parsed = urllib_parse.urlsplit(response.headers["location"])
    mock_root_index_parsed = urllib_parse.urlsplit(mock_root_index)
    assert url_parsed.netloc == mock_root_index_parsed.netloc
    assert posixpath.split(url_parsed.path)[1] == "numpy-1.23.1.tar.gz"


@pytest.mark.parametrize("file_mime_type", ["application/octet-stream", None])
def test_download_file_representation(server, tmp_path, file_mime_type):
    """Test package file content type and encoding."""
    (tmp_path / "packages").mkdir()
    file_mime_type_patch = mock.patch.object(
        proxpi_server, "_file_mime_type", file_mime_type
    )
    with file_mime_type_patch:
        response = requests.get(
            f"{server}/index/proxpi/proxpi-1.0.0.tar.gz",
            allow_redirects=False,
        )
    assert response.status_code == 200
    if file_mime_type:
        assert response.headers["Content-Type"] == "application/octet-stream"
        assert not response.headers.get("Content-Encoding")
    else:
        assert response.headers["Content-Type"] == "application/x-tar"
        assert response.headers["Content-Encoding"] == "gzip"
    response.close()
