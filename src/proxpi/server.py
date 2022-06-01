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

try:
    import importlib.metadata
except ImportError:
    importlib = None
else:
    try:
        logger.info(f"proxpi version: {importlib.metadata.version('proxpi')}")
    except importlib.metadata.PackageNotFoundError:
        pass

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
KNOWN_LATEST_JSON_VERSION = "v1"
KNOWN_DATASET_KEYS = ["requires-python", "dist-info-metadata", "gpg-sig", "yanked"]


def _wants_json(version: str = "v1") -> bool:
    if version == KNOWN_LATEST_JSON_VERSION and _wants_json("latest"):
        return True
    json_key = f"application/vnd.pypi.simple.{version}+json"
    if flask.request.args.get("format") == json_key:
        return True
    json_quality = flask.request.accept_mimetypes.quality(json_key)
    return json_quality and json_quality >= max(
        flask.request.accept_mimetypes.quality("text/html"),
        flask.request.accept_mimetypes.quality("application/vnd.pypi.simple.v1+html"),
    )


def _build_json_response(data: dict, version: str = "v1") -> flask.Response:
    response = flask.jsonify(data)
    response.mimetype = f"application/vnd.pypi.simple.{version}+json"
    return response


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
    if _wants_json():
        response = _build_json_response({
            "meta": {"api-version": "1.0"},
            "projects": {n: {"url": f"{n}/"} for n in package_names},
        })
    else:
        response = flask.make_response(flask.render_template(
            "packages.html", package_names=package_names
        ))
    response.vary = (", " if response.vary else "") + "Accept"
    return response


@app.route("/index/<package_name>/")
def list_files(package_name: str):
    """List all files for a package."""
    try:
        files = cache.list_files(package_name)
    except _cache.NotFound:
        flask.abort(404)
        raise

    if _wants_json():
        files_data = []
        for file in files:
            file_data = {"filename": file.name, "url": file.url, "hashes": {}}
            for part in file.fragment.split(","):
                try:
                    hash_name, hash_value = part.split("=")
                except ValueError:
                    continue
                file_data["hashes"][hash_name] = hash_value
            for data_set_key in KNOWN_DATASET_KEYS:
                if f"data-{data_set_key}" in file.attributes:
                    file_data[data_set_key] = file.attributes[f"data-{data_set_key}"]
            files_data.append(file_data)
        response = _build_json_response({
            "meta": {"api-version": "1.0"},
            "name": package_name,
            "files": files_data,
        })

    else:
        response = flask.make_response(flask.render_template(
            "files.html", package_name=package_name, files=files
        ))

    response.vary = (", " if response.vary else "") + "Accept"
    return response


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
