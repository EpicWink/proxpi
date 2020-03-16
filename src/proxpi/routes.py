"""Server routes."""

from urllib import parse as urllib_parse

import sys
import flask
import collections

from . import config
from . import data

cache = data.Cache.from_config()
if "--help" not in sys.argv:
    cache.list_packages()
Item = collections.namedtuple("Item", ("name", "url"))


@config.app.route("/index/")
def list_packages():
    package_names = cache.list_packages()
    packages = [Item(n, f"/index/{n}/") for n in package_names]
    return flask.render_template("packages.html", packages=packages)


@config.app.route("/index/<package_name>/")
def list_files(package_name: str):
    try:
        files = cache.list_files(package_name)
    except data.NotFound:
        flask.abort(404)
    files = [
        Item(f.name, f"/index/{package_name}/{f.name}#sha256={f.sha}") for f in files
    ]
    return flask.render_template("files.html", package_name=package_name, files=files)


@config.app.route("/index/<package_name>/<file_name>")
def get_file(package_name: str, file_name: str):
    try:
        path = cache.get_file(package_name, file_name)
    except data.NotFound:
        flask.abort(404)
    scheme = urllib_parse.urlparse(path).scheme
    if scheme and scheme != "file":
        return flask.redirect(path)
    return flask.send_file(path)
