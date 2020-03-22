# proxpi
[![Build status](
https://github.com/EpicWink/proxpi/workflows/test/badge.svg?branch=master)](
https://github.com/EpicWink/proxpi/actions?query=branch%3Amaster+workflow%3Atest)
[![codecov](https://codecov.io/gh/EpicWink/proxpi/branch/master/graph/badge.svg)](
https://codecov.io/gh/EpicWink/proxpi)

PyPI caching mirror

* Host a proxy PyPI mirror server with caching
  * Cache the index (package list and packages' file list)
  * Cache the package files
* Support multiple indices
* Set index cache times-to-live (individually for each index)
* Set files cache max-size on disk
* Manually invalidate index cache

## Installation
```bash
pip install proxpi
```

Install `coloredlogs` as well to get coloured logging

## Usage
```bash
FLASK_APP=proxpi.server flask run
```

```bash
pip install --index-url http://127.0.0.1:5000/index/ simplejson
```

### Cache invalidation
```bash
curl -X DELETE http://127.0.0.1:5000/cache/simplejson
curl -X DELETE http://127.0.0.1:5000/cache/list
```

If you need to invalidate a locally cached file, restart the server: files should never
change in a package index.

### Environment variables
* `PROXPI_INDEX_URL`: index URL, default: https://pypi.org/simple/
* `PROXPI_INDEX_TTL`: index cache time-to-live in seconds,
   default: 30 minutes. Disable index-cache by setting this to 0
* `PROXPI_EXTRA_INDEX_URLS`: extra index URLs (comma-separated)
* `PROXPI_EXTRA_INDEX_TTLS`: corresponding extra index cache times-to-live in seconds
   (comma-separated), default: 3 minutes, cache disabled when 0
* `PROXPI_CACHE_SIZE`: size of downloaded package files cache (bytes), default 5GB.
  Disable files-cache by setting this to 0


### Docker
```bash
docker run -p 5000:5000 epicwink/proxpi
```
