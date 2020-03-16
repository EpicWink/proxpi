"""Local PyPI mirror cache."""

import os

import flask
import jinja2

INDEX_URL = os.environ.get("PIP_INDEX_URL", "https://pypi.org/simple/")
EXTRA_INDEX_URL = os.environ.get("PIP_EXTRA_INDEX_URL", "")
INDEX_TTL = os.environ.get("INDEX_TTL", 1800)
EXTRA_INDEX_TTL = os.environ.get(
    "EXTRA_INDEX_TTL", " ".join("180" for s in EXTRA_INDEX_URL.split() if s)
)

app = flask.Flask("pypi_mirror")
app.jinja_loader = jinja2.PackageLoader("pypi_mirror")
