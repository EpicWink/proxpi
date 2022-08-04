"""Test ``proxpi`` server."""

import hashlib
import logging
import warnings
import posixpath
import subprocess
from urllib import parse as urllib_parse
from unittest import mock

from proxpi import server as proxpi_server
import pytest
import requests
import packaging.specifiers

from . import _utils

logging.root.setLevel(logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


@pytest.fixture(scope="module")
def server():
    yield from _utils.make_server(proxpi_server.app)


def test_pip_download(server, tmp_path):
    """Test package installation."""
    args = [
        "pip",
        "--no-cache-dir",
        "download",
        "--index-url",
        f"{server}/index/",
    ]
    p = subprocess.run(
        [*args, "--dest", str(tmp_path / "dest1"), "Jinja2", "marshmallow"]
    )
    assert p.returncode == 0
    contents = list((tmp_path / "dest1").iterdir())
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)
    assert any("marshmallow" in p.name.lower() for p in contents)
    subprocess.run([*args, "--dest", str(tmp_path / "dest2"), "Jinja2"])
    assert p.returncode == 0
    contents = list((tmp_path / "dest2").iterdir())
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)


def test_list(server):
    """Test getting package list."""
    response = requests.get(f"{server}/index/")
    response.raise_for_status()

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


@pytest.mark.parametrize("project", ["proxpi", "numpy"])
def test_package(server, project):
    """Test getting package files."""
    project_url = f"{server}/index/{project}/"
    response = requests.get(project_url)
    response.raise_for_status()

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


def test_download_file_failed(server, readonly_package_dir):
    """Test getting package file when caching failed."""
    cache_patch = mock.patch.object(proxpi_server.cache.file_cache, "_files", {})
    dir_patch = mock.patch.object(
        proxpi_server.cache.file_cache, "cache_dir", str(readonly_package_dir)
    )
    with cache_patch, dir_patch:
        response = requests.get(
            f"{server}/index/jinja2/Jinja2-2.11.1-py2.py3-none-any.whl",
            allow_redirects=False,
        )
    assert response.status_code // 100 == 3
    url_parsed = urllib_parse.urlsplit(response.headers["location"])
    assert url_parsed.netloc == "files.pythonhosted.org"
    assert posixpath.split(url_parsed.path)[1] == "Jinja2-2.11.1-py2.py3-none-any.whl"


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
