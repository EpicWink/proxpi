# Changelog

`proxpi` release notes. `proxpi` follows [semantic versioning](https://semver.org/).

## Unreleased

### Changes

* Don't include `Content-Encoding` header in cached sdist responses
  * `Content-Type` is now `application/x-tar+gzip` instead of `application/x-tar`
  * Doesn't apply when `PROXPI_BINARY_FILE_MIME_TYPE=1` is set

### Features

* Configure logging level with `PROXPI_LOGGING_LEVEL`

### Fixes

* Support package index HTML responses with no `content-type` header

### Improvements

* Colour tracebacks if `colored-traceback` is installed (disable with `NO_COLOR=1`)
* Atomically download files from sources

### Miscellaneous

* Move project metadata and configuration to [pyproject.toml](./pyproject.toml)
  * Drop `dataclassess` requirement
* Add `pretty` extra, installing `coloredlogs` and `colored-traceback`
* Test with Python 3.14 in CI, and use in app Docker image
* Pin `setuptools` build requirement to <81
* Allow `lxml` v6

## 1.2.2 - 2025-11-19

### Fixes

* Read source index response bodies using iter_content, which automatically decompresses
  the response

### Improvements

* Set chunk size (to 16 KiB), so cached file is gradually written to disk as it is
  downloaded from source index

## 1.2.1 - 2025-11-19

### Changes

* Docker image tag format is simply the version (no leading `v`)

### Fixes

* Respect correct environment variable for extra index TTLs: `PROXPI_EXTRA_INDEX_TTLS`
  * Still support `PROXPI_EXTRA_INDEX_TTL` for backwards compatibility

## 1.2 - 2024-07-08

### Features

* Implement [PEP 714](https://peps.python.org/pep-0714/) - rename `"dist-info-metadata"`
  attribute to `"core-metadata"` (https://github.com/EpicWink/proxpi/pull/28)
* Server health endpoint: `GET /health` (https://github.com/EpicWink/proxpi/pull/38)
* Added request timeout configuration (https://github.com/EpicWink/proxpi/pull/35)
* Added environment variable `PROXPI_DISABLE_INDEX_SSL_VERIFICATION` (`=1`) to disable
  index SSL certificate verification
* Added environment variable `PROXPI_DOWNLOAD_TIMEOUT` for fallback redirect timeout
  (https://github.com/EpicWink/proxpi/pull/18)

### Improvements

* Warn on invalid core-metadata attribute value
* Don't rely on exceptions being raised during HTML hash attribute value parsing
* Stream proxied index responses to HTML parser
  (https://github.com/EpicWink/proxpi/pull/20)

### Miscellaneous

* Use Python 3.12, Flask v3, Gunicorn v22 (https://github.com/EpicWink/proxpi/pull/49),
  lxml v5, Werkzeug v3 in Docker app
* Added ARM Docker image (https://github.com/EpicWink/proxpi/pull/45)
* Package build uses [trusted publishing](https://docs.pypi.org/trusted-publishers/)

## 1.1 - 2023-05-26

### Features

* Optionally provide JSON simple repository index responses
  ([PEP 691](https://peps.python.org/pep-0691/))
* Serve distribution metadata if provided from source package indexes
* Add option to force binary content-type (ie `application/octet-stream`) in file
  responses via environment variable `PROXPI_BINARY_FILE_MIME_TYPE`

### Fixes

* `Vary` response header now correctly contains `Accept` and `Accept-Encoding`
* Support package index HTML responses with no `body` element
* Provide package only found in extra indexes instead of 404
* Fix cache refreshed-time after recently starting the OS. This affects when the project
  sub-route is directly called instead of calling the project-list route
* Fix file attributes in HTML responses, importantly the data-yanked attribute, and all
  hashes when the source index provides multiple hashes

### Improvements

* HTML simple repository index API version declared in response body
  ([PEP 629](https://peps.python.org/pep-0629/))
* Declare HTML generator (as `proxpi`) in response body
* Add user-agent (as `proxpi/vX.Y.Z`) to package index requests
* Support and prefer JSON simple repository index responses from source package indexes
* Include `coloredlogs` in Docker image

### Miscellaneous

* Test with Python 3.11 in CI (and drop 3.6)
* Docker app has dependencies pinned

## 1.0.1 - 2022-08-08

### Fixes

* Relative files for URLs from source index servers are joined with their package's
  request base URL to make them absolute for downloading
* Support package index responses with no HTML body element
* Provide package only found in extra indexes instead of 404

### Improvements

* Add `proxpi` user-agent to requests to package indexes
* Request HTML from package indexes

### Miscellaneous

* Constrain dependency versions

## 1.0 - 2022-04-07

### Features

* Add home page with index invalidation and link to index root
* Add environment variable `PROXPI_CACHE_DIR` to set file-cache directory

### Changes

* Use [Gunicorn](https://gunicorn.org/) for serving in Docker container

### Improvements

* Reduced Docker image size
* Avoid requesting index by assuming package URL
* Use single request session for cache
* Protect file-cache eviction with a lock
* Remove smallest files first when evict from file-cache
* Convert user-assert into RuntimeError
* (Attempt to) log `proxpi` version on server start-up

### Fixes

* Download-fail response not cached

## 0.1 - 2020-04-16

### Features

* Host a proxy PyPI mirror server with caching
  * Cache the index (package list and packages' file list)
  * Cache the package files
* Support multiple indices
* Set index cache times-to-live (individually for each index)
* Set files cache max-size on disk
* Manually invalidate index cache
