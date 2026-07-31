"""Test ``proxpi`` server against real PyPI."""

import sys
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


@pytest.mark.parametrize(("uploaded_prior_to", "expected_jinja2_version"), [
    pytest.param(None, None, id="latest"),
    pytest.param("2025-01-01", "3.1.5", id="date_bound"),
])  # fmt: skip
def test_pip_download(
    server,
    tmp_path,
    uploaded_prior_to: str,
    expected_jinja2_version: str,
) -> None:
    """Test package installation."""
    args = [
        sys.executable,
        "-m",
        "pip",
        "--no-cache-dir",
        "download",
        "--index-url", f"{server}/index/",
    ]

    if uploaded_prior_to:
        p_v = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            check=True,
            stdout=subprocess.PIPE,
        )
        pip_version = tuple(
            int(x)
            for x in p_v.stdout.decode(encoding="utf-8").split()[1].split(".")[:2]
        )
        if pip_version < (26, 1):
            pytest.skip(reason="pip v26.1+ required for date-bound test")

        args.extend(["--uploaded-prior-to", uploaded_prior_to])

    p = subprocess.run(
        [*args, "--dest", str(tmp_path / "dest1"), "Jinja2", "marshmallow"],
    )
    assert p.returncode == 0
    contents = list((tmp_path / "dest1").iterdir())
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)
    assert any("marshmallow" in p.name.lower() for p in contents)

    if expected_jinja2_version:
        filenames = set(p.name.lower() for p in contents)
        assert (
            any(f"jinja2-{expected_jinja2_version}" in f for f in filenames)
            or any(f"jinja2_{expected_jinja2_version}" in f for f in filenames)
        )

    p = subprocess.run([*args, "--dest", str(tmp_path / "dest2"), "Jinja2"])
    assert p.returncode == 0
    contents = list((tmp_path / "dest2").iterdir())
    print(contents)
    assert any("jinja2" in p.name.lower() for p in contents)
