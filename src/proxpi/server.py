"""Cached package index server."""

import os
import http
import gzip
import zlib
import typing as t
import logging
import urllib.parse

import jinja2
import fastapi.responses
import fastapi.templating
import fastapi.exceptions

from . import _cache
from . import _server_utils

try:
    import importlib_resources  # prefer PyPI version (for Python < 3.9)
except ImportError:
    import importlib.resources as importlib_resources

try:
    import colored_traceback
except ImportError:  # pragma: no cover
    pass
else:  # pragma: no cover
    colored_traceback.add_hook()

logging_level = os.environ.get("PROXPI_LOGGING_LEVEL", "INFO")
fmt = "%(asctime)s [%(levelname)8s] %(name)s: %(message)s"
try:
    import coloredlogs
except ImportError:  # pragma: no cover
    logging.basicConfig(level=logging_level, format=fmt)
else:  # pragma: no cover
    coloredlogs.install(
        level=logging_level,
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


app = fastapi.FastAPI(title=__package__, openapi_url=None, redirect_slashes=False)
templates = fastapi.templating.Jinja2Templates(
    env=jinja2.Environment(loader=jinja2.PackageLoader(__package__)),
)
cache = _cache.Cache.from_config()
if app.debug:
    logging.root.setLevel(logging.DEBUG)
    for handler in logging.root.handlers:
        if handler.level > logging.DEBUG:
            handler.level = logging.DEBUG
logger.info("Cache: %r", cache)
KNOWN_LATEST_JSON_VERSION = "v1"
KNOWN_DATASET_KEYS = ["requires-python", "dist-info-metadata", "gpg-sig", "yanked"]


def _wants_json(request: fastapi.Request, version: str = "v1") -> bool:
    """Determine if client wants a JSON response.

    First checks `format` request query paramater, and if its value is a
    known content-type, decides if client wants JSON. Then falls back to
    HTTP content-negotiation, where the decision is based on the quality
    of the JSON content-type (JSON must be equally or more preferred to
    HTML, but strictly more preferred to 'text/html').

    Args:
        version: PyPI JSON response content-type version
    """

    if version == KNOWN_LATEST_JSON_VERSION:
        try:
            wants_json = _wants_json(request, version="latest")
        except fastapi.exceptions.HTTPException as e:
            if e.status_code != http.HTTPStatus.NOT_ACCEPTABLE.value:
                raise
        else:
            if wants_json:
                return True

    json_key = f"application/vnd.pypi.simple.{version}+json"
    html_keys = {
        "text/html",
        "application/vnd.pypi.simple.v1+html",
        "application/vnd.pypi.simple.latest+html",
    }

    if request.query_params.get("format"):
        if request.query_params["format"] == json_key:
            return True
        elif request.query_params["format"] in html_keys:
            return False

    get_quality = _server_utils.parse_accept_header(request.headers.get("Accept"))
    json_quality = get_quality(json_key)
    html_quality = max(get_quality(k) for k in html_keys)
    iana_html_quality = get_quality("text/html")

    if not json_quality and not html_quality:
        raise fastapi.exceptions.HTTPException(
            status_code=http.HTTPStatus.NOT_ACCEPTABLE.value,
        )

    return (
        json_quality
        and json_quality >= html_quality
        and json_quality > iana_html_quality
    )


def _build_json_response(
    data: dict,
    version: str = "v1",
) -> fastapi.responses.JSONResponse:
    return fastapi.responses.JSONResponse(
        data, media_type=f"application/vnd.pypi.simple.{version}+json"
    )


BINARY_FILE_MIME_TYPE = (
    os.environ.get("PROXPI_BINARY_FILE_MIME_TYPE", "")
).lower() not in ("", "0", "no", "off", "false")
_file_mime_type = "application/octet-stream" if BINARY_FILE_MIME_TYPE else None


def _compress(
    response: t.Union[str, fastapi.Response],
    request: fastapi.Request,
) -> fastapi.Response:
    if isinstance(response, str):
        response = fastapi.Response(response)

    get_quality = _server_utils.parse_accept_encoding_header(
        request.headers.get("Accept-Encoding"),
    )
    gzip_quality = get_quality("gzip")
    zlib_quality = get_quality("deflate")
    identity_quality = get_quality("identity")

    if gzip_quality and gzip_quality >= max(identity_quality, zlib_quality):
        response.body = gzip.compress(response.body)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(response.body))
    elif zlib_quality and zlib_quality >= identity_quality:
        response.body = zlib.compress(response.body)
        response.headers["Content-Encoding"] = "deflate"
        response.headers["Content-Length"] = str(len(response.body))
    elif not identity_quality:
        raise fastapi.exceptions.HTTPException(
            status_code=http.HTTPStatus.NOT_ACCEPTABLE.value,
        )

    _server_utils.add_vary("Accept-Encoding", response)

    return response


@app.get("/")
def index() -> fastapi.responses.FileResponse:
    """Home page."""
    with importlib_resources.as_file(
        importlib_resources.files(__package__) / "templates" / "index.html",
    ) as path:
        return fastapi.responses.FileResponse(path, media_type=_file_mime_type)


@app.get("/index/")
def list_packages(request: fastapi.Request) -> fastapi.Response:
    """List all projects in index(es)."""
    package_names = cache.list_projects()

    if _wants_json(request):
        response = _build_json_response(data={
            "meta": {"api-version": "1.0"},
            "projects": [{"name": n} for n in package_names],
        })  # fmt: skip
    else:
        response = templates.TemplateResponse(
            request=request,
            name="packages.html",
            context=dict(package_names=package_names),
        )

    _server_utils.add_vary("Accept", response)

    return _compress(response, request)


@app.get("/index/{package_name}/")
def list_files(package_name: str, request: fastapi.Request) -> fastapi.Response:
    """List all files for a project."""
    try:
        files = cache.list_files(package_name)
    except _cache.NotFound as e:
        raise fastapi.exceptions.HTTPException(
            status_code=http.HTTPStatus.NOT_FOUND.value,
        ) from e

    if _wants_json(request):
        files_data = []
        for file in files:
            file_data = file.to_json_response()
            file_data["url"] = file.name
            files_data.append(file_data)
        response = _build_json_response(data={
            "meta": {"api-version": "1.0"},
            "name": package_name,
            "files": files_data,
        })  # fmt: skip

    else:
        response = templates.TemplateResponse(
            request=request,
            name="files.html",
            context=dict(package_name=package_name, files=files),
        )

    _server_utils.add_vary("Accept", response)

    return _compress(response, request)


@app.get("/index/{package_name}/{file_name}")
def get_file(package_name: str, file_name: str) -> fastapi.Response:
    """Download a file."""
    try:
        path = cache.get_file(package_name, file_name)
    except _cache.NotFound as e:
        raise fastapi.exceptions.HTTPException(
            status_code=http.HTTPStatus.NOT_FOUND.value,
        ) from e

    scheme = urllib.parse.urlparse(path).scheme
    if scheme and scheme != "file":
        return fastapi.responses.RedirectResponse(
            path, status_code=http.HTTPStatus.FOUND.value
        )

    response = fastapi.responses.FileResponse(path, media_type=_file_mime_type)
    if (
        path.endswith(".tar.gz")
        and response.media_type == "application/x-tar"
    ):
        response.media_type = "application/x-tar+gzip"  # keep consistent
        response.headers["Content-Type"] = "application/x-tar+gzip"
    return response


@app.delete("/cache/list")
def invalidate_list():
    """Invalidate project list cache."""
    cache.invalidate_list()
    return {"status": "success", "data": None}


@app.delete("/cache/{package_name}")
def invalidate_package(package_name):
    """Invalidate project file list cache."""
    cache.invalidate_project(package_name)
    return {"status": "success", "data": None}


@app.get("/health")
def health():
    return {"status": "success", "data": None}
