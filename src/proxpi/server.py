"""Cached package index server."""

import os
import logging
import urllib.parse

import flask
import jinja2

from . import _cache

fmt = "%(asctime)s [%(levelname)8s] %(name)s: %(message)s"
try:
    import coloredlogs
except ImportError:  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format=fmt)
else:  # pragma: no cover
    coloredlogs.install(
        level=logging.INFO,
        fmt=fmt,
        field_styles={
            "asctime": {"faint": True, "color": "white"},
            "levelname": {"bold": True, "color": "blue"},
            "name": {"bold": True, "color": "yellow"},
        },
    )
logger = logging.getLogger(__name__)

_proxpi_version = _cache.get_proxpi_version()
logger.info(f"proxpi version: {_proxpi_version or '<unknown>'}")

try:
    import gunicorn.glogging
except ImportError:
    gunicorn = None
else:

    class _GunicornLogger(gunicorn.glogging.Logger):
        def __init__(self, cfg):
            super().__init__(cfg)
            self.error_log.propagate = True
            self.access_log.propagate = True

        def _set_handler(self, *_, **__):
            pass


app = flask.Flask("proxpi")
app.jinja_loader = jinja2.PackageLoader("proxpi")
cache = _cache.Cache.from_config()
if app.debug or app.testing:
    logging.root.setLevel(logging.DEBUG)
    for handler in logging.root.handlers:
        if handler.level > logging.DEBUG:
            handler.level = logging.DEBUG
logger.info("Cache: %r", cache)


@app.route("/")
def index():
    """Home page."""
    max_age = app.get_send_file_max_age("index.html")
    return flask.send_from_directory(
        os.path.join(app.root_path, app.template_folder), "index.html", max_age=max_age
    )


@app.route("/index/")
def list_packages():
    """List all packages in index(es)."""
    package_names = cache.list_packages()
    return flask.render_template("packages.html", package_names=package_names)


@app.route("/index/<package_name>/")
def list_files(package_name: str):
    """List all files for a package."""
    try:
        files = cache.list_files(package_name)
    except _cache.NotFound:
        flask.abort(404)
        raise
    return flask.render_template("files.html", package_name=package_name, files=files)


@app.route("/index/<package_name>/<file_name>")
def get_file(package_name: str, file_name: str):
    """Download a file."""
    try:
        path = cache.get_file(package_name, file_name)
    except _cache.NotFound:
        flask.abort(404)
        raise
    scheme = urllib.parse.urlparse(path).scheme
    if scheme and scheme != "file":
        return flask.redirect(path)
    return flask.send_file(path)


@app.route("/cache/list", methods=["DELETE"])
def invalidate_list():
    """Invalidate package list cache."""
    cache.invalidate_list()
    return {"status": "success", "data": None}


@app.route("/cache/<package_name>", methods=["DELETE"])
def invalidate_package(package_name):
    """Invalidate package file list cache."""
    cache.invalidate_package(package_name)
    return {"status": "success", "data": None}
