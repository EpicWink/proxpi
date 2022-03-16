"""Test ``proxpi`` server."""

import logging
import posixpath
import threading
import subprocess
from urllib import parse as urllib_parse
from unittest import mock

from proxpi import server as proxpi_server
import pytest
import requests
from werkzeug import serving as werkzeug_serving

logging.root.setLevel(logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


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
    assert response.status_code == 200
    assert "simplejson" in response.text


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
