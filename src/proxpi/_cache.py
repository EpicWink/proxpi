"""Package index interfacing and caching."""

import os
import re
import abc
import time
import shutil
import logging
import tempfile
import warnings
import functools
import posixpath
import threading
import dataclasses
import typing as t
import urllib.parse

import requests
import lxml.etree

INDEX_URL = os.environ.get("PROXPI_INDEX_URL", "https://pypi.org/simple/")
EXTRA_INDEX_URLS = [
    s for s in os.environ.get("PROXPI_EXTRA_INDEX_URLS", "").strip().split(",") if s
]
DISABLE_INDEX_SSL_VERIFICATION = os.environ.get(
    "PROXPI_DISABLE_INDEX_SSL_VERIFICATION", ""
) not in ("", "0", "no", "off", "false")

INDEX_TTL = int(os.environ.get("PROXPI_INDEX_TTL", 1800))
EXTRA_INDEX_TTLS = [
    int(s) for s in os.environ.get("PROXPI_EXTRA_INDEX_TTL", "").strip().split(",") if s
] or [180] * len(EXTRA_INDEX_URLS)

CACHE_SIZE = int(os.environ.get("PROXPI_CACHE_SIZE", 5368709120))
CACHE_DIR = os.environ.get("PROXPI_CACHE_DIR")
DOWNLOAD_TIMEOUT = float(os.environ.get("PROXPI_DOWNLOAD_TIMEOUT", 0.9))

CONNECT_TIMEOUT = (
    float(os.environ["PROXPI_CONNECT_TIMEOUT"])
    if os.environ.get("PROXPI_CONNECT_TIMEOUT")
    else None
)
READ_TIMEOUT = (
    float(os.environ["PROXPI_READ_TIMEOUT"])
    if os.environ.get("PROXPI_READ_TIMEOUT")
    else None
)

logger = logging.getLogger(__name__)
_name_normalise_re = re.compile("[-_.]+")
_hostname_normalise_pattern = re.compile(r"[^a-z0-9]+")
_time_offset = time.time()


def _now() -> float:
    return time.monotonic() + _time_offset


class File(metaclass=abc.ABCMeta):
    """Package file reference."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Filename."""

    @property
    @abc.abstractmethod
    def url(self) -> str:
        """File URL."""

    @property
    @abc.abstractmethod
    def fragment(self) -> str:
        """File URL fragment."""

    @property
    @abc.abstractmethod
    def attributes(self) -> t.Dict[str, str]:
        """File reference link element (non-href) attributes."""

    @property
    @abc.abstractmethod
    def hashes(self) -> t.Dict[str, str]:
        """File hashes."""

    @property
    @abc.abstractmethod
    def requires_python(self) -> t.Union[str, None]:
        """Distribution Python requirement."""

    @property
    @abc.abstractmethod
    def dist_info_metadata(self) -> t.Union[bool, t.Dict[str, str], None]:
        """Distribution metadata file marker."""

    @property
    @abc.abstractmethod
    def gpg_sig(self) -> t.Union[bool, None]:
        """Distribution GPG signature file marker."""

    @property
    @abc.abstractmethod
    def yanked(self) -> t.Union[bool, str, None]:
        """File yanked status."""

    def to_json_response(self) -> t.Dict[str, t.Any]:
        """Serialise to JSON response data (with 'url' key)."""
        data = {"filename": self.name, "hashes": self.hashes}
        if self.requires_python is not None:
            data["requires-python"] = self.requires_python
        if self.dist_info_metadata is not None:
            # PEP 714: only emit new key in JSON
            data["core-metadata"] = self.dist_info_metadata
        if self.gpg_sig is not None:
            data["gpg-sig"] = self.gpg_sig
        if self.yanked is not None:
            data["yanked"] = self.yanked
        return data


@dataclasses.dataclass
class FileFromHTML(File):
    __slots__ = ("name", "url", "fragment", "attributes")

    name: str
    url: str
    fragment: str
    attributes: t.Dict[str, str]

    @classmethod
    def from_html_element(
        cls, el: "lxml.etree.ElementBase", request_url: str
    ) -> "File":
        """Construct from HTML API response."""
        url = urllib.parse.urljoin(request_url, el.attrib["href"])

        attributes = {k: v for k, v in el.attrib.items() if k != "href"}

        # PEP 714: accept both core-metadata attributes, and emit both in HTML
        if "data-core-metadata" in attributes:
            attributes["data-dist-info-metadata"] = attributes["data-core-metadata"]
        elif "data-dist-info-metadata" in attributes:
            attributes["data-core-metadata"] = attributes["data-dist-info-metadata"]

        return cls(
            name=el.text,
            url=url,
            fragment=urllib.parse.urlsplit(url).fragment,
            attributes=attributes,
        )

    @property
    def hashes(self):
        return self._parse_hash(self.fragment)

    @property
    def requires_python(self):
        return self.attributes.get("data-requires-python") or None

    @property
    def dist_info_metadata(self):
        metadata = self.attributes.get("data-core-metadata")
        if metadata is None:
            return None
        hashes = self._parse_hash(metadata)
        if not hashes:
            if metadata not in ("", "true"):
                logger.warning(
                    f"Invalid metadata attribute value from index: {metadata}"
                )
            return True  # '': value-less -> true
        return hashes

    @property
    def gpg_sig(self):
        has_gpg_sig = self.attributes.get("data-gpg-sig")
        return has_gpg_sig and self.attributes.get("data-gpg-sig") == "true"

    @property
    def yanked(self):
        return self.attributes.get("data-yanked") is not None  # '': value-less -> true

    @staticmethod
    def _parse_hash(hash_string: str) -> t.Dict[str, str]:
        if "=" not in hash_string:
            return {}
        hash_name, hash_value = hash_string.split("=")
        return {hash_name: hash_value}


@dataclasses.dataclass
class FileFromJSON(File):
    __slots__ = (
        "name",
        "url",
        "hashes",
        "requires_python",
        "dist_info_metadata",
        "gpg_sig",
        "yanked",
    )

    name: str
    url: str
    hashes: t.Dict[str, str]
    requires_python: t.Union[str, None]
    dist_info_metadata: t.Union[bool, t.Dict[str, str], None]
    gpg_sig: t.Union[bool, None]
    yanked: t.Union[bool, str, None]

    @classmethod
    def from_json_response(cls, data: t.Dict[str, t.Any], request_url: str) -> "File":
        """Construct from JSON API response."""
        return cls(
            name=data["filename"],
            url=urllib.parse.urljoin(request_url, data["url"]),
            hashes=data["hashes"],
            requires_python=data.get("requires-python"),
            # PEP 714: accept both core-metadata keys
            dist_info_metadata=(
                data.get("core-metadata") or data.get("dist-info-metadata")
            ),
            gpg_sig=data.get("gpg-sig"),
            yanked=data.get("yanked"),
        )

    @property
    def fragment(self) -> str:
        """File URL fragment."""
        return self._stringify_hashes(self.hashes)

    @property
    def attributes(self) -> t.Dict[str, str]:
        """File reference link element (non-href) attributes."""
        attributes = {}
        if self.requires_python:
            attributes["data-requires-python"] = self.requires_python
        if self.dist_info_metadata:
            attributes["data-dist-info-metadata"] = self._stringify_hashes(
                self.dist_info_metadata,
            ) if isinstance(self.dist_info_metadata, dict) else ""  # fmt: skip
            # PEP 714: emit both core-metadata attributes in HTML
            attributes["data-core-metadata"] = attributes["data-dist-info-metadata"]
        if self.gpg_sig is not None:
            attributes["data-gpg-sig"] = "true" if self.gpg_sig else "false"
        if self.yanked:
            attributes["data-yanked"] = (
                self.yanked if isinstance(self.yanked, str) else ""
            )
        return attributes

    @staticmethod
    def _stringify_hashes(hashes: t.Dict[str, str]) -> str:
        if not hashes:
            return ""
        if "sha256" in hashes:
            return f"sha256={hashes['sha256']}"
        for hash_name, hash_value in hashes.items():
            return f"{hash_name}={hash_value}"


@dataclasses.dataclass
class Package:
    """Package files cache."""

    __slots__ = ("name", "files", "refreshed")

    name: str
    """Package name."""

    files: t.Dict[str, File]
    """Package files by filename."""

    refreshed: float
    """Package last refreshed time (seconds)."""


class NotFound(ValueError):
    """Package or file not found."""

    pass


class Thread(threading.Thread):
    """Exception-storing thread runner."""

    exc = None

    def run(self):
        try:
            super().run()
        except Exception as e:
            self.exc = e
            raise

    def join(self, timeout=None):
        super().join(timeout)
        if self.exc:
            raise self.exc


class _Locks:
    _lock: threading.Lock
    _locks: t.Dict[str, threading.Lock]

    def __init__(self):
        self._lock = threading.Lock()
        self._locks = {}

    def __getitem__(self, k: str) -> threading.Lock:
        if k not in self._locks:
            with self._lock:
                if k not in self._locks:
                    self._locks[k] = threading.Lock()
        return self._locks[k]


class Session(requests.Session):
    default_timeout: t.Union[float, t.Tuple[float, float], None] = None

    def send(self, request: requests.PreparedRequest, **kwargs) -> requests.Response:
        if self.default_timeout and not kwargs.get("timeout"):
            kwargs["timeout"] = self.default_timeout
        return super().send(request, **kwargs)


def _mask_password(url: str) -> str:
    """Mask HTTP basic auth password in URL.

    Args:
        url: URL to process

    Returns:
        URL with password masked (or original URL if it has no password)
    """

    parsed = urllib.parse.urlsplit(url)
    if not parsed.password:
        return url
    netloc = f"{parsed.username}:****@" + parsed.hostname
    if parsed.port is not None:
        netloc += f":{parsed.port}"
    parsed = parsed._replace(netloc=netloc)
    return urllib.parse.urlunsplit(parsed)


class _IndexCache:
    """Cache for an index.

    Args:
        index_url: index URL
        ttl: cache time-to-live
        session: index request session
    """

    index_url: str
    ttl: int
    session: requests.Session
    _index_t: t.Union[float, None]
    _index_lock: threading.Lock
    _package_locks: _Locks
    _index: t.Dict[str, str]
    _packages: t.Dict[str, Package]
    _headers = {"Accept": (
        "application/vnd.pypi.simple.v1+json, "
        "application/vnd.pypi.simple.v1+html;q=0.1"
    )}  # fmt: skip

    def __init__(self, index_url: str, ttl: int, session: requests.Session = None):
        self.index_url = index_url
        self.ttl = ttl
        self.session = session or requests.Session()
        self._index_t = None
        self._index_lock = threading.Lock()
        self._package_locks = _Locks()
        self._index = {}
        self._packages = {}
        self._index_url_masked = _mask_password(index_url)

    def __repr__(self):
        return f"{self.__class__.__name__}({self._index_url_masked!r}, {self.ttl!r})"

    def _list_packages(self):
        """List projects using or updating cache."""
        if self._index_t is not None and _now() < self._index_t + self.ttl:
            return

        logger.info(f"Listing packages in index '{self._index_url_masked}'")
        response = self.session.get(self.index_url, headers=self._headers, stream=True)
        response.raise_for_status()
        self._index_t = _now()

        if response.headers["Content-Type"] == "application/vnd.pypi.simple.v1+json":
            response_data = response.json()
            for project in response_data["projects"]:
                name_normalised = _name_normalise_re.sub("-", project["name"]).lower()
                self._index[name_normalised] = f"{name_normalised}/"
            logger.debug(
                f"Finished listing packages in index '{self._index_url_masked}'",
            )
            return

        for _, child in lxml.etree.iterparse(response.raw, tag="a", html=True):
            if True:  # minimise Git diff
                name = _name_normalise_re.sub("-", child.text).lower()
                self._index[name] = child.attrib["href"]
        logger.debug(f"Finished listing packages in index '{self._index_url_masked}'")

    def list_packages(self) -> t.KeysView[str]:
        """List packages.

        Deprecated: use ``list_projects``.

        Returns:
            names of packages in index
        """

        warnings.warn(
            message="`list_packages` is deprecated, use `list_projects`",
            category=DeprecationWarning,
            stacklevel=2,
        )
        return self.list_projects()

    def list_projects(self) -> t.KeysView[str]:
        """List projects.

        Returns:
            names of projects in index
        """

        with self._index_lock:
            self._list_packages()
        return self._index.keys()

    def _list_files(self, package_name: str):
        """List project files using or updating cache."""
        package = self._packages.get(package_name)
        if package and _now() < package.refreshed + self.ttl:
            return

        logger.debug(f"Listing files in package '{package_name}'")
        response = None
        if self._index_t is None or _now() > self._index_t + self.ttl:
            url = urllib.parse.urljoin(self.index_url, package_name)
            logger.debug(f"Refreshing '{package_name}'")
            response = self.session.get(url, headers=self._headers, stream=True)
        if not response or not response.ok:
            logger.debug(f"List-files response: {response}")
            package_name_normalised = _name_normalise_re.sub("-", package_name).lower()
            if package_name_normalised not in self.list_projects():
                raise NotFound(package_name)
            package_url = self._index[package_name]
            url = urllib.parse.urljoin(self.index_url, package_url)
            response = self.session.get(url, headers=self._headers, stream=True)
            response.raise_for_status()

        package = Package(package_name, files={}, refreshed=_now())

        if response.headers["Content-Type"] == "application/vnd.pypi.simple.v1+json":
            response_data = response.json()
            for file_data in response_data["files"]:
                file = FileFromJSON.from_json_response(file_data, response.request.url)
                package.files[file.name] = file
            self._packages[package_name] = package
            logger.debug(f"Finished listing files in package '{package_name}'")
            return

        for _, child in lxml.etree.iterparse(response.raw, tag="a", html=True):
            if True:  # minimise Git diff
                file = FileFromHTML.from_html_element(child, response.request.url)
                package.files[file.name] = file
        self._packages[package_name] = package
        logger.debug(f"Finished listing files in package '{package_name}'")

    def list_files(self, package_name: str) -> t.ValuesView[File]:
        """List project files.

        Args:
            package_name: name of project to list files of

        Returns:
            files of project

        Raises:
            NotFound: if project doesn't exist in index
        """

        with self._package_locks[package_name]:
            self._list_files(package_name)
        return self._packages[package_name].files.values()

    def get_file_url(self, package_name: str, file_name: str) -> str:
        """Get a file.

        Args:
            package_name: project of file to get
            file_name: name of file to get

        Returns:
            local file URL

        Raises:
            NotFound: if project doesn't exist in index or file doesn't
                exist in project
        """

        self.list_files(package_name)  # updates cache
        package = self._packages[package_name]
        is_metadata = file_name[-9:] == ".metadata"
        file = package.files.get(file_name[:-9] if is_metadata else file_name)
        if not file:
            raise NotFound(file_name)
        url = file.url
        if is_metadata:
            # Note: don't validate if file has 'data-dist-info-metadata' attribute, let
            # the source index provide the 404
            scheme, netloc, path, query, fragment = urllib.parse.urlsplit(url)
            url = urllib.parse.urlunsplit(
                (scheme, netloc, path + ".metadata", query, fragment),
            )
        return url

    def invalidate_list(self):
        """Invalidate package list cache."""
        if self._index_lock.locked():
            logger.info("Index already undergoing update")
            return
        self._index_t = None
        self._index = {}

    def invalidate_package(self, package_name: str):
        """Invalidate package file list cache.

        Deprecate: use ``invalidate_project``.

        Args:
            package_name: package name
        """

        warnings.warn(
            message="`invalidate_package` is deprecated, use `invalidate_project`",
            category=DeprecationWarning,
            stacklevel=2,
        )
        return self.invalidate_project(package_name)

    def invalidate_project(self, name: str) -> None:
        """Invalidate project file list cache.

        Args:
            name: project name
        """

        package_name = name
        if self._package_locks[package_name].locked():
            logger.info(f"Project '{name}' files already undergoing update")
            return
        self._packages.pop(package_name, None)


@dataclasses.dataclass
class _CachedFile:
    """Cached file."""

    __slots__ = ("path", "size", "n_hits")

    path: str
    """File path."""

    size: int
    """File size."""

    n_hits: int
    """Number of cache hits."""


def _split_path(
    path: str, split: t.Callable[[str], t.Tuple[str, str]]
) -> t.Generator[str, None, None]:
    """Split path into directory components.

    Args:
        path: path to split
        split: path-split functions

    Returns:
        path parts generator
    """

    parent, filename = split(path)
    if not filename:
        return
    if parent:
        yield from _split_path(parent, split)
    yield filename


class _FileCache:
    """Package files cache."""

    max_size: int
    cache_dir: str
    _cache_dir_provided: t.Union[str, None]
    _files: t.Dict[str, t.Union[_CachedFile, Thread]]
    _evict_lock: threading.Lock

    def __init__(
        self,
        max_size: int,
        cache_dir: str = None,
        download_timeout: float = 0.9,
        session: requests.Session = None,
    ):
        """Initialise file-cache.

        Args:
            max_size: maximum file-cache size
            cache_dir: file-cache directory
            download_timeout: file download timeout (seconds), falling back to
                redirect
            session: index request session
        """

        self.max_size = max_size
        self.cache_dir = os.path.abspath(cache_dir or tempfile.mkdtemp())
        self.download_timeout = download_timeout
        self.session = session or requests.Session()
        self._cache_dir_provided = cache_dir
        self._files = {}
        self._evict_lock = threading.Lock()

        self._populate_files_from_existing_cache_dir()

    def __repr__(self):
        return (
            f"{self.__class__.__name__}({self.max_size!r}, {self.cache_dir!r}, "
            f"{self.session!r})"
        )

    def __del__(self):
        if not self._cache_dir_provided and os.path.isdir(self.cache_dir):
            logger.debug(f"Deleting '{self.cache_dir}'")
            shutil.rmtree(self.cache_dir)

    def _populate_files_from_existing_cache_dir(self):
        """Populate from user-provided cache directory."""
        for dirpath, _, filenames in os.walk(self.cache_dir):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                size = os.path.getsize(filepath)
                name = os.path.relpath(filepath, self.cache_dir)
                if os.path != posixpath:
                    name = posixpath.join(*_split_path(name, os.path.split))
                self._files[name] = _CachedFile(filepath, size, n_hits=0)

    @staticmethod
    @functools.lru_cache(maxsize=8096)
    def _get_key(url: str) -> str:
        """Get file cache reference key from file URL."""
        urlsplit = urllib.parse.urlsplit(url)
        parent = _hostname_normalise_pattern.sub("-", urlsplit.hostname)
        return posixpath.join(parent, *_split_path(urlsplit.path, posixpath.split))

    def _download_file(self, url: str, path: str):
        """Download a file.

        Args:
            url: URL of file to download
            path: local path to download to
        """

        url_masked = _mask_password(url)
        logger.debug(f"Downloading '{url_masked}' to '{path}'")
        response = self.session.get(url, stream=True)
        if response.status_code // 100 >= 4:
            logger.error(
                f"Failed to download '{url_masked}': "
                f"status={response.status_code}, body={response.text}"
            )
            return
        parent, _ = os.path.split(path)
        os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as f:
            for chunk in response.iter_content(None):
                f.write(chunk)
        key = self._get_key(url)
        self._files[key] = _CachedFile(path, os.stat(path).st_size, 0)
        logger.debug(f"Finished downloading '{url_masked}'")

    def _wait_for_existing_download(self, url: str) -> bool:
        """Wait for existing download, if any.

        Returns:
            whether the wait is given up (ie if time-out was reached or
                exception was encountered)
        """

        file = self._files.get(url)
        if isinstance(file, Thread):
            try:
                file.join(self.download_timeout)
            except Exception as e:
                if file.exc and file == self._files[url]:
                    self._files.pop(url, None)
                url_masked = _mask_password(url)
                logger.error(f"Failed to download '{url_masked}'", exc_info=e)
                return True
            if isinstance(self._files[url], Thread):
                return True  # default to original URL (due to timeout or HTTP error)
        return False

    def _get_cached(self, url: str) -> t.Union[str, None]:
        """Get file from cache."""
        if url in self._files:
            file = self._files[url]
            assert isinstance(file, _CachedFile)
            file.n_hits += 1
            return file.path
        return None

    def _start_downloading(self, url: str):
        """Start downloading a file."""
        key = self._get_key(url)
        path = os.path.join(self.cache_dir, *_split_path(key, posixpath.split))

        thread = Thread(target=self._download_file, args=(url, path))
        self._files[key] = thread
        thread.start()

    def _evict_lfu(self, url: str):
        """Evict least-frequently-used files until under max cache size."""
        response = self.session.head(url)
        file_size = int(response.headers.get("Content-Length", 0)) if response.ok else 0
        cache_keys = [u for u, f in self._files.items() if isinstance(f, _CachedFile)]
        cache_keys.sort(key=lambda k: self._files[k].size)
        cache_keys.sort(key=lambda k: self._files[k].n_hits)
        existing_size = sum(self._files[k].size for k in cache_keys)
        while existing_size + file_size > self.max_size and existing_size > 0:
            existing_url = cache_keys.pop(0)
            file = self._files.pop(existing_url)
            os.unlink(file.path)
            existing_size -= file.size

    def get(self, url: str) -> str:
        """Get a file using or updating cache.

        Args:
            url: original file URL

        Returns:
            local file path, or original file URL if not yet available
        """

        if self.max_size == 0:
            return url
        key = self._get_key(url)
        path = url
        given_up = self._wait_for_existing_download(key)
        if not given_up:
            path = self._get_cached(key)
            if not path:
                self._start_downloading(url)
                with self._evict_lock:
                    self._evict_lfu(url)
                path = self.get(url)
        return path


@dataclasses.dataclass
class Cache:
    """Package index cache."""

    root_cache: _IndexCache
    """Root index cache."""

    file_cache: _FileCache
    """Downloaded project file cache."""

    extra_caches: t.List[_IndexCache] = dataclasses.field(default_factory=list)
    """Extra indices' caches."""

    _index_cache_cls = _IndexCache
    _file_cache_cls = _FileCache

    @classmethod
    def from_config(cls):
        """Create cache from configuration."""
        session = Session()
        session.verify = not DISABLE_INDEX_SSL_VERIFICATION
        proxpi_version = get_proxpi_version()
        if proxpi_version:
            session.headers["User-Agent"] = f"proxpi/{proxpi_version}"

        if CONNECT_TIMEOUT and READ_TIMEOUT:
            session.default_timeout = (CONNECT_TIMEOUT, READ_TIMEOUT)
        elif CONNECT_TIMEOUT:
            session.default_timeout = (CONNECT_TIMEOUT, 20.0)
        elif READ_TIMEOUT:
            session.default_timeout = (3.1, READ_TIMEOUT)

        root_cache = cls._index_cache_cls(INDEX_URL, INDEX_TTL, session)
        file_cache = cls._file_cache_cls(
            CACHE_SIZE, CACHE_DIR, DOWNLOAD_TIMEOUT, session
        )
        if len(EXTRA_INDEX_URLS) != len(EXTRA_INDEX_TTLS):
            raise RuntimeError(
                f"Number of extra index URLs doesn't equal number of extra index "
                f"times-to-live: {len(EXTRA_INDEX_URLS)} != {len(EXTRA_INDEX_TTLS)}"
            )
        extra_caches = [
            cls._index_cache_cls(url, ttl, session)
            for url, ttl in zip(EXTRA_INDEX_URLS, EXTRA_INDEX_TTLS)
        ]
        return cls(root_cache, file_cache, extra_caches=extra_caches)

    def list_packages(self) -> t.List[str]:
        """List all packages.

        Deprecated: use ``list_projects``.

        Returns:
            names of all discovered packages
        """

        warnings.warn(
            message="`list_packages` is deprecated, use `list_projects`",
            category=DeprecationWarning,
            stacklevel=2,
        )
        return self.list_projects()

    def list_projects(self) -> t.List[str]:
        """List all projects.

        Returns:
            names of all discovered projects
        """

        packages = set(self.root_cache.list_projects())
        for cache in self.extra_caches:
            packages.update(cache.list_projects())
        return sorted(packages)

    def list_files(self, package_name: str) -> t.List[File]:
        """List project files.

        Args:
            package_name: name of project to list files of

        Returns:
            files of project

        Raises:
            NotFound: if project doesn't exist in any index
        """

        files = []
        exc = None
        try:
            root_files = self.root_cache.list_files(package_name)
        except NotFound as e:
            exc = e
        else:
            files.extend(root_files)
        for cache in self.extra_caches:
            try:
                extra_files = cache.list_files(package_name)
            except NotFound:
                continue
            for file in extra_files:
                if file.name not in {f.name for f in files}:
                    files.append(file)
        if not files and exc:
            raise exc
        return files

    def get_file(self, package_name: str, file_name: str) -> str:
        """Get a file.

        Args:
            package_name: project of file to get
            file_name: name of file to get

        Returns:
            local file path, or original file URL if not yet available

        Raises:
            NotFound: if project doesn't exist in any index or file doesn't
                exist in project
        """

        try:
            url = self.root_cache.get_file_url(package_name, file_name)
        except NotFound as e:
            url = e
        if isinstance(url, Exception):
            for cache in self.extra_caches:
                try:
                    url = cache.get_file_url(package_name, file_name)
                except NotFound:
                    pass
            if isinstance(url, Exception):
                raise url
        return self.file_cache.get(url)

    def invalidate_list(self):
        """Invalidate project list cache."""
        logger.info("Invalidating project list cache.")
        self.root_cache.invalidate_list()
        for cache in self.extra_caches:
            cache.invalidate_list()

    def invalidate_package(self, package_name: str):
        """Invalidate package file list cache.

        Deprecated: use ``invalidate_project``.

        Args:
            package_name: package name
        """

        warnings.warn(
            message="`invalidate_package` is deprecated, use `invalidate_project`",
            category=DeprecationWarning,
            stacklevel=2,
        )
        return self.invalidate_project(package_name)

    def invalidate_project(self, name: str) -> None:
        """Invalidate project file-list cache.

        Args:
            name: project name
        """

        logger.info(f"Invalidating project '{name}' file list cache.")
        self.root_cache.invalidate_project(name)
        for cache in self.extra_caches:
            cache.invalidate_project(name)


@functools.lru_cache(maxsize=None)
def get_proxpi_version() -> t.Union[str, None]:
    try:
        import importlib.metadata
    except ImportError:
        return None
    else:
        try:
            return importlib.metadata.version("proxpi")
        except importlib.metadata.PackageNotFoundError:
            return None
