"""Package index interfacing and caching."""

import io
import os
import re
import time
import shutil
import logging
import tempfile
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

INDEX_TTL = int(os.environ.get("PROXPI_INDEX_TTL", 1800))
EXTRA_INDEX_TTLS = [
    int(s) for s in os.environ.get("PROXPI_EXTRA_INDEX_TTL", "").strip().split(",") if s
] or [180] * len(EXTRA_INDEX_URLS)

CACHE_SIZE = int(os.environ.get("PROXPI_CACHE_SIZE", 5368709120))
CACHE_DIR = os.environ.get("PROXPI_CACHE_DIR")

logger = logging.getLogger(__name__)
_name_normalise_re = re.compile("[-_.]+")
_hostname_normalise_pattern = re.compile(r"[^a-z0-9]+")
_html_parser = lxml.etree.HTMLParser()


@dataclasses.dataclass
class File:
    """Package file reference."""

    __slots__ = ("name", "url", "fragment", "attributes")

    name: str
    """Filename."""

    url: str
    """File URL."""

    fragment: str
    """File URL fragment."""

    attributes: t.Dict[str, str]
    """File reference link element (non-href) attributes."""


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
        """List packages using or updating cache."""
        if self._index_t is not None and (time.monotonic() - self._index_t) < self.ttl:
            return

        logger.info(f"Listing packages in index '{self._index_url_masked}'")
        response = self.session.get(self.index_url)
        response.raise_for_status()
        tree = lxml.etree.parse(io.BytesIO(response.content), _html_parser)
        self._index_t = time.monotonic()

        root = tree.getroot()
        body = next(b for b in root if b.tag == "body")
        for child in body:
            if child.tag == "a":
                name = _name_normalise_re.sub("-", child.text).lower()
                self._index[name] = child.attrib["href"]
        logger.debug(f"Finished listing packages in index '{self._index_url_masked}'")

    def list_packages(self) -> t.KeysView[str]:
        """List packages.

        Returns:
            names of packages in index
        """

        with self._index_lock:
            self._list_packages()
        return self._index.keys()

    def _list_files(self, package_name: str):
        """List package files using or updating cache."""
        package = self._packages.get(package_name)
        if package and time.monotonic() < package.refreshed + self.ttl:
            return

        logger.debug(f"Listing files in package '{package_name}'")
        response = None
        if time.monotonic() > (self._index_t or 0.0) + self.ttl:
            url = urllib.parse.urljoin(self.index_url, package_name)
            response = self.session.get(url)
        if not response or not response.ok:
            if package_name not in self.list_packages():
                raise NotFound(package_name)
            package_url = self._index[package_name]
            url = urllib.parse.urljoin(self.index_url, package_url)
            response = self.session.get(url)
            response.raise_for_status()

        package = Package(package_name, files={}, refreshed=time.monotonic())
        tree = lxml.etree.parse(io.BytesIO(response.content), _html_parser)

        root = tree.getroot()
        body = next(b for b in root if b.tag == "body")
        for child in body:
            if child.tag == "a":
                name = child.text
                url = child.attrib["href"]
                attributes = {k: v for k, v in child.attrib.items() if k != "href"}
                fragment = urllib.parse.urlsplit(url).fragment
                package.files[name] = File(name, url, fragment, attributes)
        self._packages[package_name] = package
        logger.debug(f"Finished listing files in package '{package_name}'")

    def list_files(self, package_name: str) -> t.ValuesView[File]:
        """List package files.

        Args:
            package_name: name of package to list files of

        Returns:
            files of package

        Raises:
            NotFound: if package doesn't exist in index
        """

        with self._package_locks[package_name]:
            self._list_files(package_name)
        return self._packages[package_name].files.values()

    def get_file_url(self, package_name: str, file_name: str) -> str:
        """Get a file.

        Args:
            package_name: package of file to get
            file_name: name of file to get

        Returns:
            local file URL

        Raises:
            NotFound: if package doesn't exist in index or file doesn't
                exist in package
        """

        self.list_files(package_name)  # updates cache
        package = self._packages[package_name]
        if file_name not in package.files:
            raise NotFound(file_name)
        return package.files[file_name].url

    def invalidate_list(self):
        """Invalidate package list cache."""
        if self._index_lock.locked():
            logger.info("Index already undergoing update")
            return
        self._index_t = None
        self._index = {}

    def invalidate_package(self, package_name: str):
        """Invalidate package file list cache.

        Args:
            package_name: package name
        """

        if self._package_locks[package_name].locked():
            logger.info(f"Package '{package_name}' files already undergoing update")
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
        self, max_size: int, cache_dir: str = None, session: requests.Session = None
    ):
        """Initialise file-cache.

        Args:
            max_size: maximum file-cache size
            cache_dir: file-cache directory
            session: index request session
        """

        self.max_size = max_size
        self.cache_dir = os.path.abspath(cache_dir or tempfile.mkdtemp())
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
        """Wait 0.9s for existing download."""
        file = self._files.get(url)
        if isinstance(file, Thread):
            try:
                file.join(0.9)
            except Exception as e:
                if file.exc and file == self._files[url]:
                    self._files.pop(url, None)
                url_masked = _mask_password(url)
                logger.error(f"Failed to download '{url_masked}'", exc_info=e)
                return True
            if isinstance(self._files[url], Thread):
                return True  # default to original URL (due to timeout)
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
    """Downloaded package file cache."""

    extra_caches: t.List[_IndexCache] = dataclasses.field(default_factory=list)
    """Extra indices' caches."""

    _index_cache_cls = _IndexCache
    _file_cache_cls = _FileCache

    @classmethod
    def from_config(cls):
        """Create cache from configuration."""
        session = requests.Session()
        root_cache = cls._index_cache_cls(INDEX_URL, INDEX_TTL, session)
        file_cache = cls._file_cache_cls(CACHE_SIZE, CACHE_DIR, session)
        if len(EXTRA_INDEX_URLS) != len(EXTRA_INDEX_TTLS):
            raise RuntimeError(
                f"Number of extra index URLs doesn't equal number of extra index "
                f"times-to-live: {len(EXTRA_INDEX_URLS)} != {len(EXTRA_INDEX_TTLS)}"
            )
        extra_caches = [
            cls._index_cache_cls(url, ttl)
            for url, ttl in zip(EXTRA_INDEX_URLS, EXTRA_INDEX_TTLS)
        ]
        return cls(root_cache, file_cache, extra_caches=extra_caches)

    def list_packages(self) -> t.List[str]:
        """List all packages.

        Returns:
            names of all discovered packages
        """

        packages = set(self.root_cache.list_packages())
        for cache in self.extra_caches:
            packages.update(cache.list_packages())
        return sorted(packages)

    def list_files(self, package_name: str) -> t.List[File]:
        """List package files.

        Args:
            package_name: name of package to list files of

        Returns:
            files of package

        Raises:
            NotFound: if package doesn't exist in any index
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
        if exc:
            raise exc
        return files

    def get_file(self, package_name: str, file_name: str) -> str:
        """Get a file.

        Args:
            package_name: package of file to get
            file_name: name of file to get

        Returns:
            local file path, or original file URL if not yet available

        Raises:
            NotFound: if package doesn't exist in any index or file doesn't
                exist in package
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
        """Invalidate package list cache."""
        logger.info("Invalidating package list cache.")
        self.root_cache.invalidate_list()
        for cache in self.extra_caches:
            cache.invalidate_list()

    def invalidate_package(self, package_name: str):
        """Invalidate package file list cache.

        Args:
            package_name: package name
        """

        logger.info(f"Invalidating package '{package_name}' file list cache.")
        self.root_cache.invalidate_package(package_name)
        for cache in self.extra_caches:
            cache.invalidate_package(package_name)
