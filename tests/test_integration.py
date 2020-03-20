"""Test ``proxpi`` server."""

import threading
import logging as lg
import subprocess as sp

import proxpi
import pytest
from werkzeug import serving as werkzeug_serving

lg.root.setLevel(lg.DEBUG)


class Thread(threading.Thread):
    exc = None

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.exc = e


@pytest.fixture
def server():
    server = werkzeug_serving.make_server(
        host="127.0.0.1", port=5042, app=proxpi.app
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
    p = sp.run([*args, "--dest", str(tmp_path / "dest1"), "Jinja2", "marshmallow"])
    assert p.returncode == 0
    contents = list((tmp_path / "dest1").iterdir())
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)
    assert any("marshmallow" in p.name.lower() for p in contents)
    sp.run([*args, "--dest", str(tmp_path / "dest2"), "Jinja2"])
    assert p.returncode == 0
    contents = list((tmp_path / "dest2").iterdir())
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)
