"""Server configuration."""

import logging as lg

import flask
import jinja2

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
