# proxpi
[![Build status](
https://github.com/EpicWink/proxpi/workflows/test/badge.svg?branch=master)](
https://github.com/EpicWink/proxpi/actions?query=branch%3Amaster+workflow%3Atest)
[![codecov](https://codecov.io/gh/EpicWink/proxpi/branch/master/graph/badge.svg)](
https://codecov.io/gh/EpicWink/proxpi)

PyPI caching mirror

* Host a proxy PyPI mirror server with caching
* Use extra index URLs
* Set index cache times-to-live (individually for each index)

## Installation
```bash
pip install proxpi
```

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

### Environment variables
* `PROXPI_ROOT_INDEX_URL`: root index URL, default: https://pypi.org/simple/
* `PROXPI_ROOT_INDEX_TTL`: root index time-to-live (aka cache time-out) in seconds,
   default: 30 minutes
* `PROXPI_EXTRA_INDEX_URLS`: extra index URLs (white-space separated)
* `PROXPI_EXTRA_INDEX_TTLS`: corresponding extra index times-to-live in seconds
   (white-space separated), default: 3 minutes
* `PROXPI_CACHE_SIZE`: size of downloaded package files cache (bytes), default 5GB


### Docker
```bash
docker run -p 5000:5000 epicwink/proxpi
```
