"""Server data.

Includes package index interfacing and caching.
"""

import os
import re
import time
import uuid
import shutil
import tempfile
import threading
import collections
import typing as t
import logging as lg
from urllib import parse as urllib_parse

import bs4
import requests

from . import config

logger = lg.getLogger(__name__)
_sha_fragment_re = re.compile("[#&]sha256=([^&]*)")
_name_normalise_re = re.compile("[-_.]+")
File = collections.namedtuple("File", ("name", "url", "sha"))


class NotFound(ValueError):
    """Package or file not found."""

    pass


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
        self._index = {}
        self._packages = {}

    def _list_packages(self):
        """List packages using or updating cache."""
        if self._index_t is not None and (time.monotonic() - self._index_t) < self.ttl:
            return

        logger.info(f"Listing packages in index '{self.index_url}'")
        response = requests.get(self.index_url)
        self._index_t = time.monotonic()

        soup = bs4.BeautifulSoup(response.text)
        for link in soup.find_all("a"):
            name = _name_normalise_re.sub("-", link.string).lower()
            self._index[name] = link["href"]

    def list_packages(self) -> t.Iterable[str]:
        """List packages.

        Returns:
            names of packages in index
        """

        self._list_packages()
        return tuple(self._index)

    def _list_files(self, package_name: str):
        """List package files using or updating cache."""
        packages_t = self._packages_t.get(package_name)
        if packages_t is not None and (time.monotonic() - packages_t) < self.ttl:
            return

        self._list_packages()
        if package_name not in self._index:
            raise NotFound(package_name)

        logger.debug(f"Listing files in package '{package_name}'")
        package_url = self._index[package_name]
        url = urllib_parse.urljoin(self.index_url, package_url)
        response = requests.get(url)
        self._packages_t[package_name] = time.monotonic()

        soup = bs4.BeautifulSoup(response.text)
        self._packages.setdefault(package_name, {})
        for link in soup.find_all("a"):
            name = link.string
            url = link["href"]
            match = _sha_fragment_re.search(url)
            sha = match.group(1) if match else None
            self._packages[package_name][name] = File(name, url, sha)

    def list_files(self, package_name: str) -> t.Iterable[File]:
        """List package files.

        Args:
            package_name: name of package to list files of

        Returns:
            files of package

        Raises:
            NotFound: if package doesn't exist in index
        """

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

        self._list_files(package_name)
        if file_name not in self._packages[package_name]:
            raise NotFound(file_name)
        return self._packages[package_name][file_name].url

    def invalidate_list(self):
        """Invalidate package list cache."""
        self._index_t = None
        self._index = {}

    def invalidate_package(self, package_name: str):
        """Invalidate package file list cache.

        Args:
            package_name: package name
        """

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
        if isinstance(file, threading.Thread):
            file.join(0.9)
            if isinstance(self._files[url], threading.Thread):
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

        thread = threading.Thread(target=self._download_file, args=(url, path))
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
        root_cache = cls._index_cache_cls(config.INDEX_URL, int(config.INDEX_TTL))
        file_cache = cls._file_cache_cls(int(config.CACHE_SIZE))
        extra_index_urls = [s for s in config.EXTRA_INDEX_URL.split() if s]
        extra_ttls = [int(s) for s in config.EXTRA_INDEX_TTL.split() if s]
        assert len(extra_index_urls) == len(extra_ttls)
        extra_caches = [
            cls._index_cache_cls(url, ttl)
            for url, ttl in zip(extra_index_urls, extra_ttls)
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

        try:
            return self.root_cache.list_files(package_name)
        except NotFound as e:
            exc = e
        for cache in self.extra_caches:
            try:
                return cache.list_files(package_name)
            except NotFound:
                pass
        raise exc

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
