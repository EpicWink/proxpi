"""Package index interfacing and caching."""

import io
import os
import re
import time
import uuid
import shutil
import logging
import tempfile
import threading
import collections
import typing as t
from urllib import parse as urllib_parse

import requests
from lxml import etree as lxml_etree

ROOT_INDEX_URL = os.environ.get("PROXPI_ROOT_INDEX_URL", "https://pypi.org/simple/")
EXTRA_INDEX_URLS = os.environ.get("PROXPI_EXTRA_INDEX_URLS", "").split()
ROOT_INDEX_TTL = int(os.environ.get("PROXPI_ROOT_INDEX_TTL", 1800))
EXTRA_INDEX_TTLS = os.environ.get("PROXPI_EXTRA_INDEX_TTL", "").split()
EXTRA_INDEX_TTLS = [int(s) for s in EXTRA_INDEX_TTLS] or [180] * len(EXTRA_INDEX_URLS)
CACHE_SIZE = int(os.environ.get("PROXPI_CACHE_SIZE", 5368709120))

logger = logging.getLogger(__name__)
_name_normalise_re = re.compile("[-_.]+")
_html_parser = lxml_etree.HTMLParser()
File = collections.namedtuple("File", ("name", "url", "fragment", "attributes"))


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
    def __init__(self):
        self._lock = threading.Lock()
        self._locks = {}

    def __getitem__(self, k: str) -> threading.Lock:
        if k not in self._locks:
            with self._lock:
                if k not in self._locks:
                    self._locks[k] = threading.Lock()
        return self._locks[k]


class _IndexCache:
    """Cache for an index.

    Args:
        index_url: index URL
        ttl: cache time-to-live
    """

    def __init__(self, index_url: str, ttl: int):
        self.index_url = index_url
        self.ttl = ttl
        self._index_t = None
        self._packages_t = {}
        self._index_lock = threading.Lock()
        self._package_locks = _Locks()
        self._index = {}
        self._packages = {}

    def _list_packages(self):
        """List packages using or updating cache."""
        if self._index_t is not None and (time.monotonic() - self._index_t) < self.ttl:
            return

        logger.info(f"Listing packages in index '{self.index_url}'")
        response = requests.get(self.index_url)
        tree = lxml_etree.parse(io.BytesIO(response.content), _html_parser)
        self._index_t = time.monotonic()

        root = tree.getroot()
        body = next(b for b in root if b.tag == "body")
        for child in body:
            if child.tag == "a":
                name = _name_normalise_re.sub("-", child.text).lower()
                self._index[name] = child.attrib["href"]

    def list_packages(self) -> t.Iterable[str]:
        """List packages.

        Returns:
            names of packages in index
        """

        with self._index_lock:
            self._list_packages()
        return tuple(self._index)

    def _list_files(self, package_name: str):
        """List package files using or updating cache."""
        packages_t = self._packages_t.get(package_name)
        if packages_t is not None and (time.monotonic() - packages_t) < self.ttl:
            return

        with self._index_lock:
            self._list_packages()
        if package_name not in self._index:
            raise NotFound(package_name)

        logger.debug(f"Listing files in package '{package_name}'")
        package_url = self._index[package_name]
        url = urllib_parse.urljoin(self.index_url, package_url)
        response = requests.get(url)
        self._packages_t[package_name] = time.monotonic()
        tree = lxml_etree.parse(io.BytesIO(response.content), _html_parser)

        root = tree.getroot()
        body = next(b for b in root if b.tag == "body")
        self._packages.setdefault(package_name, {})
        for child in body:
            if child.tag == "a":
                name = child.text
                url = child.attrib["href"]
                attributes = {k: v for k, v in child.attrib.items() if k != "href"}
                fragment = urllib_parse.urlsplit(url).fragment
                self._packages[package_name][name] = File(
                    name, url, fragment, attributes
                )

    def list_files(self, package_name: str) -> t.Iterable[File]:
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
        return tuple(self._packages[package_name].values())

    def get_file_url(self, package_name: str, file_name: str) -> str:
        """Get a file.

        Args:
            package_name: package of file to get
            file_name: name of file to get

        Returns:
            local file path, or original file URL if not yet available

        Raises:
            NotFound: if package doesn't exist in index or file doesn't
                exist in package
        """

        with self._package_locks[package_name]:
            self._list_files(package_name)
        if file_name not in self._packages[package_name]:
            raise NotFound(file_name)
        return self._packages[package_name][file_name].url

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
        self._packages_t.pop(package_name, None)
        self._packages.pop(package_name, None)


class _CachedFile:
    __slots__ = ("path", "size", "n_hits")

    def __init__(self, path, size, n_hits):
        self.path = path
        self.size = size
        self.n_hits = n_hits


class _FileCache:
    def __init__(self, max_size):
        self.max_size = max_size
        self._package_dir = tempfile.mkdtemp()
        self._files = {}

    def __del__(self):
        if os.path.isdir(self._package_dir):
            logger.debug(f"Deleting '{self._package_dir}'")
            shutil.rmtree(self._package_dir)

    def _download_file(self, url: str, path: str):
        """Download a file.

        Args:
            url: URL of file to download
            path: local path to download to
        """

        logger.debug(f"Downloading '{url}' to '{path}'")
        response = requests.get(url, stream=True)
        with open(path, "wb") as f:
            for chunk in response.iter_content(None):
                f.write(chunk)
        self._files[url] = _CachedFile(path, os.stat(path).st_size, 0)

    def _wait_for_existing_download(self, url: str) -> t.Union[str, None]:
        """Wait 0.9s for existing download."""
        file = self._files.get(url)
        if isinstance(file, Thread):
            try:
                file.join(0.9)
            except Exception:
                if file.exc and file == self._files[url]:
                    self._files.pop(url, None)
                raise
            if isinstance(self._files[url], Thread):
                return url  # default to original URL
        return None

    def _get_cached(self, url: str) -> t.Union[str, None]:
        """Get file from cache."""
        if url in self._files:
            file = self._files[url]
            file.n_hits += 1
            return file.path
        return None

    def _start_downloading(self, url: str):
        """Start downloading a file."""
        suffix = os.path.splitext(urllib_parse.urlparse(url).path)[1]
        path = os.path.join(self._package_dir, str(uuid.uuid4()) + suffix)

        thread = Thread(target=self._download_file, args=(url, path))
        self._files[url] = thread
        thread.start()

    def _evict_lfu(self, url: str):
        """Evict least-frequently-used files until under max cache size."""
        response = requests.head(url)
        file_size = int(response.headers.get("Content-Length", 0))
        existing_urls = sorted(
            (f for f in self._files if isinstance(f, _CachedFile)),
            key=lambda k: self._files[k].n_hist,
        )
        existing_size = sum(self._files[k].size for k in existing_urls)
        while existing_size + file_size > self.max_size and existing_size > 0:
            existing_url = existing_urls.pop(0)
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

        path = self._wait_for_existing_download(url)
        if not path:
            path = self._get_cached(url)
            if not path:
                self._start_downloading(url)
                self._evict_lfu(url)
                path = self.get(url)
        return path


class Cache:
    """Package index cache.

    Args:
        root_cache: root index cache
        file_cache: downloaded package file cache
        extra_caches: extra indices' caches
    """

    _index_cache_cls = _IndexCache
    _file_cache_cls = _FileCache

    def __init__(
        self,
        root_cache: _IndexCache,
        file_cache: _FileCache,
        extra_caches: t.List[_IndexCache] = None,
    ):
        self.root_cache = root_cache
        self.file_cache = file_cache
        self.extra_caches = extra_caches or []
        self._packages = {}
        self._list_dt = None
        self._package_list_dt = {}

    @classmethod
    def from_config(cls):
        """Create cache from configuration."""
        root_cache = cls._index_cache_cls(ROOT_INDEX_URL, ROOT_INDEX_TTL)
        file_cache = cls._file_cache_cls(CACHE_SIZE)
        assert len(EXTRA_INDEX_URLS) == len(EXTRA_INDEX_TTLS)
        extra_caches = [
            cls._index_cache_cls(url, ttl)
            for url, ttl in zip(EXTRA_INDEX_URLS, EXTRA_INDEX_TTLS)
        ]
        return cls(root_cache, file_cache, extra_caches=extra_caches)

    def list_packages(self) -> t.Iterable[str]:
        """List all packages.

        Returns:
            names of all discovered packages
        """

        packages = set(self.root_cache.list_packages())
        for cache in self.extra_caches:
            packages.update(cache.list_packages())
        return sorted(packages)

    def list_files(self, package_name: str) -> t.Iterable[File]:
        """List package files.

        Args:
            package_name: name of package to list files of

        Returns:
            files of package

        Raises:
            NotFound: if package doesn't exist in any index
        """

        files = []
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
        if not files:
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
