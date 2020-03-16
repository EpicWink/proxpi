"""Server data.

Includes package index interfacing and caching.
"""

import os
import re
import time
import shutil
import tempfile
import threading
import collections
import typing as t
from urllib import parse as urllib_parse

import bs4
import requests

from . import config

_sha_fragment_re = re.compile("[#&]sha256=([^&]*)")
_name_normalise_re = re.compile("[-_.]+")
File = collections.namedtuple("File", ("name", "url", "sha"))


class NotFound(ValueError):
    pass


class _IndexCache:
    def __init__(self, index_url: str, ttl: int):
        self.index_url = index_url
        self.ttl = ttl
        self._package_dir = tempfile.mkdtemp()
        self._index_t = None
        self._packages_t = {}
        self._files_t = {}
        self._index = {}
        self._packages = {}
        self._files = {}

    def __del__(self):
        if os.path.isdir(self._package_dir):
            shutil.rmtree(self._package_dir)

    def _list_packages(self):
        if self._index_t is not None and (time.monotonic() - self._index_t) < self.ttl:
            return

        response = requests.get(self.index_url)
        self._index_t = time.monotonic()

        soup = bs4.BeautifulSoup(response.text)
        for link in soup.find_all("a"):
            name = _name_normalise_re.sub("-", link.string).lower()
            self._index[name] = link["href"]

    def list_packages(self) -> t.Iterable[str]:
        self._list_packages()
        return tuple(self._index)

    def _list_files(self, package_name: str):
        packages_t = self._packages_t.get(package_name)
        if packages_t is not None and (time.monotonic() - packages_t) < self.ttl:
            return

        self._list_packages()
        if package_name not in self._index:
            raise NotFound(package_name)

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
        self._list_files(package_name)
        return tuple(self._packages[package_name].values())

    @staticmethod
    def _download_file(
        url: str,
        path: str,
        get_callback: t.Callable[[], t.Any],
        done_callback: t.Callable[[], t.Any],
    ):
        response = requests.get(url, stream=True)
        get_callback()
        with open(path, "wb") as f:
            for chunk in response.iter_content(None):
                f.write(chunk)
        done_callback()

    def _get_file(self, package_name: str, file_name: str):
        files_t = self._files_t.get(package_name, {}).get(file_name)
        if files_t is not None and (time.monotonic() - files_t) < self.ttl:
            return

        self._list_files(package_name)
        if file_name not in self._packages[package_name]:
            raise NotFound(file_name)

        path = os.path.join(self._package_dir, package_name + "_" + file_name)
        url = self._packages[package_name][file_name].url

        def get_callback():
            self._files_t.setdefault(package_name, {})[file_name] = time.monotonic()

        def done_callback():
            package_files[file_name] = path

        package_files = self._files.setdefault(package_name, {})
        if isinstance(package_files.get(file_name), threading.Thread):
            package_files[file_name].join(0.9)
            time.sleep(0.01)  # give control to original master
            return

        thread = threading.Thread(
            target=self._download_file, args=(url, path, get_callback, done_callback)
        )
        package_files[file_name] = thread
        thread.start()
        thread.join(0.9)
        if thread.is_alive():
            self._files[package_name][file_name] = url

    def get_file(self, package_name: str, file_name: str) -> str:
        self._get_file(package_name, file_name)
        return self._files[package_name][file_name]


class Cache:
    _index_cache_cls = _IndexCache

    def __init__(
        self, root_cache: _IndexCache, extra_caches: t.List[_IndexCache] = None
    ):
        self.root_cache = root_cache
        self.extra_caches = extra_caches or []
        self._packages = {}
        self._list_dt = None
        self._package_list_dt = {}

    @classmethod
    def from_config(cls):
        root_cache = cls._index_cache_cls(config.INDEX_URL, int(config.INDEX_TTL))
        extra_index_urls = [s for s in config.EXTRA_INDEX_URL.split() if s]
        extra_ttls = [int(s) for s in config.EXTRA_INDEX_TTL.split() if s]
        assert len(extra_index_urls) == len(extra_ttls)
        extra_caches = [
            cls._index_cache_cls(url, ttl)
            for url, ttl in zip(extra_index_urls, extra_ttls)
        ]
        return cls(root_cache, extra_caches=extra_caches)

    def list_packages(self) -> t.Iterable[str]:
        packages = set(self.root_cache.list_packages())
        for cache in self.extra_caches:
            packages.update(cache.list_packages())
        return sorted(packages)

    def list_files(self, package_name: str) -> t.Iterable[File]:
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
        try:
            return self.root_cache.get_file(package_name, file_name)
        except NotFound as e:
            exc = e
        for cache in self.extra_caches:
            try:
                return cache.get_file(package_name, file_name)
            except NotFound:
                pass
        raise exc
