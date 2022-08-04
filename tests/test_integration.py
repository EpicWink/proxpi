"""Test ``proxpi`` server."""

import io
import os
import hashlib
import logging
import tarfile
import warnings
import posixpath
import typing as t
from urllib import parse as urllib_parse
from unittest import mock

import flask
import jinja2
import pytest
import requests
import proxpi.server
import packaging.specifiers

from . import _utils

proxpi_server = proxpi.server

logging.root.setLevel(logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


def make_mock_index_app(projects: t.Dict[str, t.List[proxpi.File]]) -> flask.Flask:
    """Construct a mock package index app.

    Warning: uses ``proxpi``'s templates for index responses, and files are
    simple

    Args:
        projects: index projects with their files

    Returns:
        WSGI app for Python package simple repository index
    """

    files_content = {}
    for project_name_, files_ in projects.items():
        for file_ in files_:
            stream = io.BytesIO()
            with tarfile.TarFile.open(mode="w:gz", fileobj=stream) as tf:
                tf.addfile(
                    tarinfo=tarfile.TarInfo(name="spam"),
                    fileobj=io.BytesIO(file_.url.encode(encoding="utf-8")),
                )
            file_content_ = stream.getvalue()
            files_content[project_name_, file_.name] = file_content_
            if file_.fragment:
                assert file_.fragment == "sha256="
                file_.fragment += hashlib.sha256(file_content_).hexdigest()

    app = flask.Flask("proxpi-tests", root_path=os.path.split(__file__)[0])
    app.jinja_loader = jinja2.PackageLoader("proxpi")

    @app.route("/")
    def list_projects() -> str:
        return flask.render_template("packages.html", package_names=list(projects))

    @app.route("/<name>/")
    def get_project(name: str) -> str:
        files = projects.get(name)
        if not files:
            flask.abort(404)
        return flask.render_template("files.html", package_name=name, files=files)

    @app.route("/<project_name>/<file_name>")
    def get_file(project_name: str, file_name: str) -> bytes:
        file_content = files_content.get((project_name, file_name))
        if not file_content:
            flask.abort(404)
        return file_content

    return app


@pytest.fixture(scope="module")
def mock_root_index():
    app = make_mock_index_app(projects={
        "proxpi": [
            proxpi.File(
                name="proxpi-1.1.0-py3-none-any.whl",
                url="spam eggs 42",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
            proxpi.File(
                name="proxpi-1.1.0.tar.gz",
                url="foo bar 42",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
            proxpi.File(
                name="proxpi-1.0.0-py3-none-any.whl",
                url="spam eggs 41",
                fragment="",
                attributes={},
            ),
            proxpi.File(
                name="proxpi-1.0.0.tar.gz",
                url="foo bar 42",
                fragment="",
                attributes={},
            ),
        ],
        "numpy": [
            proxpi.File(
                name="numpy-1.23.1-cp310-cp310-manylinux_2_17_x86_64.whl",
                url="",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.8"},
            ),
            proxpi.File(
                name="numpy-1.23.1-cp310-cp310-win_amd64.whl",
                url="",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.8"},
            ),
            proxpi.File(
                name="numpy-1.23.1.tar.gz",
                url="foo bar 42",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.8"},
            ),
        ],
    })  # fmt: skip
    yield from _utils.make_server(app)


@pytest.fixture(scope="module")
def mock_extra_index():
    app = make_mock_index_app(projects={
        "scipy": [
            proxpi.File(
                name="scipy-1.9.0-cp310-cp310-manylinux_2_17_x86_64.whl",
                url="spam eggs 17",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
            proxpi.File(
                name="scipy-1.9.0.tar.gz",
                url="foo bar 17",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
        ],
        "numpy": [
            proxpi.File(
                name="numpy-1.23.1-cp310-cp310-macosx_10_9_x86_64.whl",
                url="spam eggs 40c",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.8"},
            ),
        ],
    })  # fmt: skip
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


@pytest.mark.parametrize("accept", ["text/html", "application/vnd.pypi.simple.v1+html"])
def test_list(server, accept):
    """Test getting package list."""
    response = requests.get(f"{server}/index/", headers={"Accept": accept})
    response.raise_for_status()

    assert response.headers["Content-Type"][:9] == "text/html"
    assert "Accept" in response.headers["Vary"]
    assert any(
        response.headers["Content-Encoding"] == a
        for a in ["gzip", "deflate"]
        if a in response.request.headers["Accept-Encoding"]
    )
    vary = {v.strip() for v in response.headers["Vary"].split(",")}
    assert "Accept-Encoding" in vary

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
def test_list_json(server, accept):
    """Test getting package list with JSON API."""
    response = requests.get(f"{server}/index/", headers={"Accept": accept})
    assert response.status_code == 200
    assert response.headers["Content-Type"][:35] == (
        "application/vnd.pypi.simple.v1+json"
    )
    assert "Accept" in response.headers["Vary"]
    assert response.json()["meta"] == {"api-version": "1.0"}
    assert any(p == {"name": "proxpi"} for p in response.json()["projects"])


@pytest.mark.parametrize("project", ["proxpi", "numpy", "scipy"])
@pytest.mark.parametrize("accept", [
    "text/html", "application/vnd.pypi.simple.v1+html", "*/*"
])
def test_package(server, project, accept):
    """Test getting package files."""
    project_url = f"{server}/index/{project}/"
    response = requests.get(project_url, headers={"Accept": accept})
    response.raise_for_status()

    assert response.headers["Content-Type"][:9] == "text/html"
    assert "Accept" in response.headers["Vary"]
    assert any(
        response.headers["Content-Encoding"] == a
        for a in ["gzip", "deflate"]
        if a in response.request.headers["Accept-Encoding"]
    )

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

        if any(k == "data-requires-python" for k, _ in attributes):
            (python_requirement,) = (
                v for k, v in attributes if k == "data-requires-python"
            )
            specifier = packaging.specifiers.SpecifierSet(python_requirement)
            assert specifier.filter(["1.2", "2.7", "3.3", "3.7", "3.10", "3.12"])


@pytest.mark.parametrize("accept", [
    "application/vnd.pypi.simple.v1+json",
    "application/vnd.pypi.simple.latest+json",
])
@pytest.mark.parametrize("query_format", [False, True])
def test_package_json(server, accept, query_format):
    """Test getting package files with JSON API."""
    params = None
    headers = None
    if query_format:
        params = {"format": accept}
    else:
        headers = {"Accept": accept}
    response = requests.get(
        f"{server}/index/proxpi/", params=params, headers=headers
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"][:35] == (
        "application/vnd.pypi.simple.v1+json"
    )
    assert "Accept" in response.headers["Vary"]
    assert response.json()["meta"] == {"api-version": "1.0"}
    assert response.json()["name"] == "proxpi"
    assert all(f["url"] and f["filename"] == f["url"] for f in response.json()["files"])
    assert all("hashes" in f for f in response.json()["files"])


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
    package_dir.mkdir(mode=0o555)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pytest.PytestUnhandledThreadExceptionWarning)
        try:
            yield package_dir
        finally:
            (tmp_path / "packages").chmod(0o755)  # allow clean-up


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
