"""Cached package index server."""

import os
import gzip
import http
import zlib
import typing as t
import logging
import urllib.parse

import jinja2
import starlette.requests
import starlette.responses
import starlette.exceptions
import starlette.templating
import starlette.applications

from . import _cache, _server_utils

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

E = t.TypeVar("E", bound=t.Callable[
    [starlette.requests.Request], t.Awaitable[starlette.responses.Response]
])  # fmt: skip

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


app = starlette.applications.Starlette()
app.router.redirect_slashes = False
templates = starlette.templating.Jinja2Templates(
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


def _route(url_path: str, method: str = "GET") -> t.Callable[[E], E]:
    def add_route(endpoint: E) -> E:
        app.add_route(url_path, route=endpoint, methods=[method])
        return endpoint

    return add_route


def _wants_json(request: starlette.requests.Request, version: str = "v1") -> bool:
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
        except starlette.exceptions.HTTPException as e:
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
        raise starlette.exceptions.HTTPException(
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
) -> starlette.responses.JSONResponse:
    return starlette.responses.JSONResponse(
        data, media_type=f"application/vnd.pypi.simple.{version}+json"
    )


BINARY_FILE_MIME_TYPE = (
    os.environ.get("PROXPI_BINARY_FILE_MIME_TYPE", "")
).lower() not in ("", "0", "no", "off", "false")
_file_mime_type = "application/octet-stream" if BINARY_FILE_MIME_TYPE else None


def _compress(
    response: t.Union[str, starlette.responses.Response],
    request: starlette.requests.Request,
) -> starlette.responses.Response:
    if isinstance(response, str):
        response = starlette.responses.Response(response)

    header_value = request.headers.get("Accept-Encoding")
    get_quality = _server_utils.parse_accept_encoding_header(header_value)
    gzip_quality = get_quality("gzip")
    zlib_quality = get_quality("deflate")
    identity_quality = get_quality("identity")

    if not header_value:
        pass  # always treat unspecified header as requesting no compression
    elif gzip_quality and gzip_quality >= max(identity_quality, zlib_quality):
        response.body = gzip.compress(response.body)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(response.body))
    elif zlib_quality and zlib_quality >= identity_quality:
        response.body = zlib.compress(response.body)
        response.headers["Content-Encoding"] = "deflate"
        response.headers["Content-Length"] = str(len(response.body))
    elif not identity_quality:
        raise starlette.exceptions.HTTPException(
            status_code=http.HTTPStatus.NOT_ACCEPTABLE.value,
        )

    _server_utils.add_vary("Accept-Encoding", response)

    return response


@_route("/")
def index(_) -> starlette.responses.FileResponse:
    """Home page."""
    with importlib_resources.as_file(
        importlib_resources.files(__package__) / "templates" / "index.html",
    ) as path:
        return starlette.responses.FileResponse(path, media_type=_file_mime_type)


@_route("/index/")
def list_packages(request: starlette.requests.Request) -> starlette.responses.Response:
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


@_route("/index/{package_name}/")
def list_files(request: starlette.requests.Request) -> starlette.responses.Response:
    """List all files for a project."""
    package_name = request.path_params["package_name"]
    try:
        files, versions = cache.list_files(package_name)
    except _cache.NotFound as e:
        raise starlette.exceptions.HTTPException(
            status_code=http.HTTPStatus.NOT_FOUND.value,
        ) from e

    if _wants_json(request):
        files_data = []
        for file in files:
            file_data = file.to_json_response()
            file_data["url"] = file.name
            files_data.append(file_data)

        response_data = {
            "meta": {"api-version": "1.0"},
            "name": package_name,
            "files": files_data,
        }

        if versions is not None:
            response_data["versions"] = versions
            if all(f.size or f.size == 0 for f in files):
                response_data["meta"]["api-version"] = "1.1"
            # No need to remove versions, size and upload-time for API < v1.1

        response = _build_json_response(data=response_data)

    else:
        response = templates.TemplateResponse(
            request=request,
            name="files.html",
            context=dict(package_name=package_name, files=files),
        )

    _server_utils.add_vary("Accept", response)

    return _compress(response, request)


@_route("/index/{package_name}/{file_name}")
def get_file(request: starlette.requests.Request) -> starlette.responses.Response:
    """Download a file."""
    package_name = request.path_params["package_name"]
    file_name = request.path_params["file_name"]
    try:
        path = cache.get_file(package_name, file_name)
    except _cache.NotFound as e:
        raise starlette.exceptions.HTTPException(
            status_code=http.HTTPStatus.NOT_FOUND.value,
        ) from e

    scheme = urllib.parse.urlparse(path).scheme
    if scheme and scheme != "file":
        return starlette.responses.RedirectResponse(
            path, status_code=http.HTTPStatus.FOUND.value
        )

    response = starlette.responses.FileResponse(path, media_type=_file_mime_type)
    if path.endswith(".tar.gz") and response.media_type == "application/x-tar":
        response.media_type = "application/x-tar+gzip"  # keep consistent
        response.headers["Content-Type"] = "application/x-tar+gzip"
    return response


@_route("/cache/list", method="DELETE")
def invalidate_list(_) -> t.Dict[str, t.Any]:
    """Invalidate project list cache."""
    cache.invalidate_list()
    return starlette.responses.JSONResponse({"status": "success", "data": None})


@_route("/cache/{package_name}", method="DELETE")
def invalidate_package(request: starlette.requests.Request) -> t.Dict[str, t.Any]:
    """Invalidate project file list cache."""
    package_name = request.path_params["package_name"]
    cache.invalidate_project(package_name)
    return starlette.responses.JSONResponse({"status": "success", "data": None})


@_route("/health")
def health(_) -> t.Dict[str, t.Any]:
    return starlette.responses.JSONResponse({"status": "success", "data": None})
