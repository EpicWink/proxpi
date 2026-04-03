"""Test ``proxpi`` server against real PyPI."""

import sys
import logging
import subprocess
import concurrent.futures

import pytest

import proxpi.server

from . import _utils

logging.root.setLevel(logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


@pytest.fixture(scope="module")
def server():
    yield from _utils.make_server(proxpi.server.app)


def run_pip(server, dest, pkgs):
    args = [
        sys.executable,
        "-m",
        "pip",
        "--no-cache-dir",
        "download",
        "--index-url",
        f"{server}/index/",
    ]
    p = subprocess.run([*args, "--dest", str(dest), *pkgs], check=True)
    assert p.returncode == 0
    return list(dest.iterdir())


def test_pip_download(server, tmp_path):
    """Test package installation."""
    contents = run_pip(server, tmp_path / "dest1", ["Jinja2", "marshmallow"])
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)
    assert any("marshmallow" in p.name.lower() for p in contents)

    contents = run_pip(server, tmp_path / "dest2", ["Jinja2"])
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)


def test_concurrent_pip_download(server, tmp_path):
    """Test concurrent package installation."""
    dest = tmp_path / "concurrent_dest"
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(run_pip, server, dest / str(i), ["flask"]) for i in range(4)
        ]
        results = [f.result() for f in futures]

    for contents in results:
        assert any("flask" in p.name.lower() for p in contents)
