"""Test ``proxpi`` server against real PyPI."""

import logging
import subprocess

import pytest

import proxpi.server

from . import _utils

logging.root.setLevel(logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.INFO)


@pytest.fixture(scope="module")
def server():
    yield from _utils.make_server(proxpi.server.app)


def test_pip_download(server, tmp_path):
    """Test package installation."""
    args = [
        "pip",
        "--no-cache-dir",
        "download",
        "--index-url", f"{server}/index/",
    ]

    p = subprocess.run(
        [*args, "--dest", str(tmp_path / "dest1"), "Jinja2", "marshmallow"],
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
