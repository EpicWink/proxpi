"""Test ``proxpi`` server."""

import io
import os
import hashlib
import logging
import tarfile
import warnings
import posixpath
import contextlib
import typing as t
from urllib import parse as urllib_parse
from unittest import mock

import flask
import jinja2
import pytest
import requests
import proxpi._cache  # noqa
import proxpi.server
import packaging.specifiers

from . import _utils

proxpi_server = proxpi.server
# noinspection PyProtectedMember
File = proxpi._cache.FileFromHTML

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


def make_mock_index_app(projects: t.Dict[str, t.List[File]]) -> flask.Flask:
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

    def build_json_v1_response(data: dict) -> flask.Response:
        response = flask.jsonify(data)
        response.mimetype = f"application/vnd.pypi.simple.v1+json"
        return response

    @app.route("/")
    def list_projects() -> t.Union[str, flask.Response]:
        if mock_index_response_is_json:
            return build_json_v1_response({
                "meta": {"api-version": "1.0"},
                "projects": [{"name": n} for n in list(projects)],
            })  # fmt: skip
        return flask.render_template("packages.html", package_names=list(projects))

    @app.route("/<name>/")
    def get_project(name: str) -> t.Union[str, flask.Response]:
        files = projects.get(name)
        if not files:
            flask.abort(404)
        if mock_index_response_is_json:
            files_data = []
            for file in files:
                file_data = file.to_json_response()
                file_data["url"] = file.name
                if (
                    mock_index_response_is_json == "yanked"
                    and not file_data.get("yanked")
                ):
                    file_data["yanked"] = False
                files_data.append(file_data)
            return build_json_v1_response({
                "meta": {"api-version": "1.0"},
                "name": name,
                "files": files_data,
            })  # fmt: skip
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
            File(
                name="proxpi-1.1.0-py3-none-any.whl",
                url="spam eggs 42",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
            File(
                name="proxpi-1.1.0.tar.gz",
                url="foo bar 42",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
            File(
                name="proxpi-1.0.0-py3-none-any.whl",
                url="spam eggs 41",
                fragment="",
                attributes={},
            ),
            File(
                name="proxpi-1.0.0.tar.gz",
                url="foo bar 42",
                fragment="",
                attributes={"data-requires-python": ""},
            ),
        ],
        "numpy": [
            File(
                name="numpy-1.23.1-cp310-cp310-manylinux_2_17_x86_64.whl",
                url="",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.8"},
            ),
            File(
                name="numpy-1.23.1-cp310-cp310-win_amd64.whl",
                url="",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.8", "data-yanked": ""},
            ),
            File(
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
            File(
                name="scipy-1.9.0-cp310-cp310-manylinux_2_17_x86_64.whl",
                url="spam eggs 17",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
            File(
                name="scipy-1.9.0.tar.gz",
                url="foo bar 17",
                fragment="sha256=",
                attributes={"data-requires-python": ">=3.7"},
            ),
        ],
        "numpy": [
            File(
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
    with set_mock_index_response_is_json(index_json_response):
        response = requests.get(
            f"{server}/index/{project}/", params=params, headers=headers
        )

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
