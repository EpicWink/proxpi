"""Server routes."""

import logging
from urllib import parse as urllib_parse

import sys
import flask
import jinja2
import collections

from . import data

fmt = "%(asctime)s [%(levelname)8s] %(name)s: %(message)s"
try:
    import coloredlogs
except ImportError:  # pragma: no cover
    logging.basicConfig(level=logging.DEBUG, format=fmt)
else:  # pragma: no cover
    coloredlogs.install(
        level=logging.DEBUG,
        fmt=fmt,
        field_styles={
            "asctime": {"faint": True, "color": "white"},
            "levelname": {"bold": True, "color": "blue"},
            "name": {"bold": True, "color": "yellow"},
        },
    )

app = flask.Flask("proxpi")
app.jinja_loader = jinja2.PackageLoader("proxpi")
cache = data.Cache.from_config()
if "--help" not in sys.argv:
    cache.list_packages()
Item = collections.namedtuple("Item", ("name", "url"))


@app.route("/index/")
def list_packages():
    """List all packages in index(es)."""
    package_names = cache.list_packages()
    packages = [Item(n, f"/index/{n}/") for n in package_names]
    return flask.render_template("packages.html", packages=packages)


@app.route("/index/<package_name>/")
def list_files(package_name: str):
    """List all files for a package."""
    try:
        files = cache.list_files(package_name)
    except data.NotFound:
        flask.abort(404)
    files = [
        Item(f.name, f"/index/{package_name}/{f.name}#sha256={f.sha}") for f in files
    ]
    return flask.render_template("files.html", package_name=package_name, files=files)


@app.route("/index/<package_name>/<file_name>")
def get_file(package_name: str, file_name: str):
    """Download a file."""
    try:
        path = cache.get_file(package_name, file_name)
    except data.NotFound:
        flask.abort(404)
    scheme = urllib_parse.urlparse(path).scheme
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
