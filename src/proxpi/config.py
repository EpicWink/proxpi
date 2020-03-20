"""Server configuration."""

import os
import logging as lg

import flask
import jinja2

INDEX_URL = os.environ.get("PIP_INDEX_URL", "https://pypi.org/simple/")
EXTRA_INDEX_URL = os.environ.get("PIP_EXTRA_INDEX_URL", "")
INDEX_TTL = os.environ.get("INDEX_TTL", 1800)
EXTRA_INDEX_TTL = os.environ.get(
    "EXTRA_INDEX_TTL", " ".join("180" for s in EXTRA_INDEX_URL.split() if s)
)
CACHE_SIZE = os.environ.get("CACHE_SIZE", 5 * 1024 ** 3)

fmt = "%(asctime)s [%(levelname)8s] %(name)s: %(message)s"
try:
    import coloredlogs
except ImportError:  # pragma: no cover
    lg.basicConfig(level=lg.DEBUG, format=fmt)
else:  # pragma: no cover
    coloredlogs.install(
        level=lg.DEBUG,
        fmt=fmt,
        field_styles={
            "asctime": {"faint": True, "color": "white"},
            "levelname": {"bold": True, "color": "blue"},
            "name": {"bold": True, "color": "yellow"},
        },
    )

app = flask.Flask("proxpi")
app.jinja_loader = jinja2.PackageLoader("proxpi")
