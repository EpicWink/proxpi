"""Test ``proxpi`` server."""

import hashlib
import logging
import posixpath
import threading
import subprocess
import html.parser
import typing as t
from urllib import parse as urllib_parse
from unittest import mock

from proxpi import server as proxpi_server
import pytest
import requests
import packaging.specifiers
from werkzeug import serving as werkzeug_serving

logging.root.setLevel(logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


class IndexParser(html.parser.HTMLParser):
    declaration: str
    title: str
    anchors: t.List[
        t.Tuple[t.Union[str, None], t.List[t.Tuple[str, t.Union[str, None]]]]
    ]
    _tag_chain: t.List[t.Tuple[str, t.List[t.Tuple[str, t.Union[str, None]]]]]
    _current_text: t.Union[str, None] = None

    def __init__(self):
        super().__init__()
        self._tag_chain = []
        self.anchors = []

    @classmethod
    def from_text(cls, text: str) -> "IndexParser":
        parser = cls()
        parser.feed(text)
        parser.close()
        return parser

    def handle_decl(self, decl):
        self.declaration = decl

    def handle_starttag(self, tag, attrs):
        self._tag_chain.append((tag, attrs))
        if self._current_text:
            self._current_text = None

    def handle_data(self, data):
        self._current_text = data

    def handle_endtag(self, tag):
        if tag == "a":
            if self._tag_chain and self._tag_chain[-1][0] == "a":
                _, attributes = self._tag_chain[-1]
                self.anchors.append((self._current_text, attributes))
        elif tag == "title":
            if self._tag_chain and self._tag_chain[-1][0] == "title":
                self.title = self._current_text
        while self._tag_chain:
            start_tag, _ = self._tag_chain.pop()
            if start_tag == tag:
                break
        self._current_text = None


class Thread(threading.Thread):
    exc = None

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.exc = e


@pytest.fixture(scope="module")
def server():
    server = werkzeug_serving.make_server(
        host="127.0.0.1", port=5042, app=proxpi_server.app
    )
    thread = Thread(target=server.serve_forever)
    thread.start()
    yield
    server.shutdown()
    thread.join(timeout=0.1)
    if thread.exc:
        raise thread.exc


def test_pip_download(server, tmp_path):
    """Test package installation."""
    args = [
        "pip",
        "--no-cache-dir",
        "download",
        "--index-url",
        "http://127.0.0.1:5042/index/",
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
    response = requests.get("http://127.0.0.1:5042/index/")
    response.raise_for_status()

    for compression_algorithm in ["gzip", "deflate"]:
        if compression_algorithm in response.request.headers["Accept-Encoding"]:
            assert response.headers["Content-Encoding"] == compression_algorithm

    parser = IndexParser.from_text(response.text)
    assert parser.declaration == "DOCTYPE html"
    assert parser.title.strip()  # required for valid HTML5
    assert parser.anchors
    for text, attributes in parser.anchors:
        (href,) = (v for k, v in attributes if k == "href")
        assert href == f"{text}/"


@pytest.mark.parametrize("project", ["proxpi", "numpy"])
def test_package(server, project):
    """Test getting package files."""
    project_url = f"http://127.0.0.1:5042/index/{project}/"
    response = requests.get(project_url)
    response.raise_for_status()

    for compression_algorithm in ["gzip", "deflate"]:
        if compression_algorithm in response.request.headers["Accept-Encoding"]:
            assert response.headers["Content-Encoding"] == compression_algorithm

    parser = IndexParser.from_text(response.text)
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
    response = requests.delete("http://127.0.0.1:5042/cache/list")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "data": None}


def test_invalidate_package(server):
    """Test invalidating package list cache."""
    response = requests.delete("http://127.0.0.1:5042/cache/jinja2")
    assert response.status_code == 200
    assert response.json() == {"status": "success", "data": None}


def test_nonexistant_package(server):
    """Test getting non-existant package file list."""
    response = requests.get("http://127.0.0.1:5042/index/ultraspampackage/")
    assert response.status_code == 404


def test_nonexistant_file(server):
    """Test getting non-existant package file."""
    response = requests.get("http://127.0.0.1:5042/index/ultraspampackage/spam.whl")
    assert response.status_code == 404


def test_nonexistant_file_from_existing_package(server):
    """Test getting non-existant package file from existing package."""
    response = requests.get("http://127.0.0.1:5042/index/Jinja2/nonexistant.whl")
    assert response.status_code == 404


def test_download_file_failed(server, tmp_path):
    """Test getting package file when caching failed."""
    (tmp_path / "packages").mkdir(mode=0o555)  # read-only directory
    cache_patch = mock.patch.object(proxpi_server.cache.file_cache, "_files", {})
    dir_patch = mock.patch.object(
        proxpi_server.cache.file_cache, "cache_dir", str(tmp_path / "packages")
    )
    with cache_patch, dir_patch:
        response = requests.get(
            "http://127.0.0.1:5042/index/jinja2/Jinja2-2.11.1-py2.py3-none-any.whl",
            allow_redirects=False,
        )
    assert response.status_code // 100 == 3
    url_parsed = urllib_parse.urlsplit(response.headers["location"])
    assert url_parsed.netloc == "files.pythonhosted.org"
    assert posixpath.split(url_parsed.path)[1] == "Jinja2-2.11.1-py2.py3-none-any.whl"
