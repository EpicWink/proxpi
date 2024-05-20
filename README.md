# proxpi
[![Build status](
https://github.com/EpicWink/proxpi/workflows/test/badge.svg?branch=master)](
https://github.com/EpicWink/proxpi/actions?query=branch%3Amaster+workflow%3Atest)
[![codecov](https://codecov.io/gh/EpicWink/proxpi/branch/master/graph/badge.svg)](
https://codecov.io/gh/EpicWink/proxpi)

PyPI caching mirror

* Host a proxy PyPI mirror server with caching
  * Cache the index (project list and projects' file list)
  * Cache the project files
* Support multiple indices
* Set index cache times-to-live (individually for each index)
* Set files cache max-size on disk
* Manually invalidate index cache

See [Alternatives](#alternatives).

## Usage
### Start server
Choose between running inside [Docker](https://www.docker.com/) container if you want to
run in a known-working environment, or outside via a Python app (instructions here are
for the [Flask](https://flask.palletsprojects.com/en/latest/) development server) if you
want more control over the environment.

#### Docker
Uses a [Gunicorn](https://gunicorn.org/) WSGI server
```bash
docker run -p 5000:5000 epicwink/proxpi
```

Without arguments, runs with 2 threads. If passing arguments, make sure to bind to an
exported address (or all with `0.0.0.0`) on port 5000 (ie `--bind 0.0.0.0:5000`).

##### Compose
Alternatively, use [Docker Compose](https://docs.docker.com/compose/)
```bash
docker compose up
```

#### Local
##### Install
```bash
pip install proxpi
```

Install `coloredlogs` as well to get coloured logging

##### Run server
```bash
FLASK_APP=proxpi.server flask run
```

See `flask run --help` for more information on address and port binding, and certificate
specification to use HTTPS. Alternatively, bring your own WSGI server.

### Use proxy
Use PIP's index-URL flag to install packages via the proxy

```bash
pip install --index-url http://127.0.0.1:5000/index/ simplejson
```

### Cache invalidation
Either head to http://127.0.0.1:5000/ in the browser, or run:
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
* `PROXPI_CACHE_SIZE`: size of downloaded project files cache (bytes), default 5GB.
  Disable files-cache by setting this to 0
* `PROXPI_CACHE_DIR`: downloaded project files cache directory path, default: a new
  temporary directory
* `PROXPI_BINARY_FILE_MIME_TYPE=1`: force file-response content-type to
  `"application/octet-stream"` instead of letting Flask guess it. This may be needed
  if your package installer (eg Poetry) mishandles responses with declared encoding.
* `PROXPI_DISABLE_INDEX_SSL_VERIFICATION=1`: don't verify any index SSL certificates
* `PROXPI_DOWNLOAD_TIMEOUT`: time (in seconds) before `proxpi` will redirect to the
  proxied index server for file downloads instead of waiting for the download,
  default: 0.9
* `PROXPI_CONNECT_TIMEOUT`: time (in seconds) `proxpi` will wait for a socket to
  connect to the index server before `requests` raises a `ConnectTimeout` error
  to prevent indefinite blocking, default: none, or 3.1 if read-timeout provided
* `PROXPI_READ_TIMEOUT`: time (in seconds) `proxpi` will wait for chunks of data 
  from the index server before `requests` raises a `ReadTimeout` error to prevent
  indefinite blocking, default: none, or 20 if connect-timeout provided

### Considerations with CI
`proxpi` was designed with three goals (particularly for continuous integration (CI)):
* to reduce load on PyPI package serving
* to reduce `pip install` times
* not require modification to the current workflow

Specifically, `proxpi` was designed to run for CI services such as
[Travis](https://travis-ci.org/),
[Jenkins](https://jenkins.io/),
[GitLab CI](https://docs.gitlab.com/ee/ci/),
[Azure Pipelines](https://azure.microsoft.com/en-us/services/devops/pipelines/)
and [GitHub Actions](https://github.com/features/actions).

`proxpi` works by caching index requests (ie which versions, wheel-types, etc are
available for a given project, the index cache) and the project files themselves (to a
local directory, the package cache). This means they will cache identical requests after
the first request, and will be useless for just one `pip install`.

#### Cache persistence
As a basic end-user of these services, for at least most of these services you won't be
able to keep a `proxpi` server running between multiple invocations of your project(s)
CI pipeline: CI invocations are designed to be independent. This means the best that you
can do is start the cache for just the current job.

A more advanced user of these CI services can bring their own runner (personally, my
needs are for running GitLab CI). This means you can run `proxpi` on a fully-controlled
server (eg [EC2](https://aws.amazon.com/ec2/) instance), and proxy PyPI requests (during
a `pip` command) through the local cache. See the instructions
[below](#gitlab-ci-instructions).

Hopefully, in the future these CI services will all implement their own transparent
caching for PyPI. For example, Azure already has
[Azure Artifacts](https://azure.microsoft.com/en-au/services/devops/artifacts/) which
provides much more functionality than `proxpi`, but won't reduce `pip install` times for
CI services not using Azure.

#### GitLab CI instructions
This implementation leverages the index URL configurable of `pip` and Docker networks.
This is to be run on a server you have console access to.

1. Create a Docker bridge network
   ```shell
   docker network create gitlab-runner-network
   ```

1. Start a GitLab CI Docker runner using
   [their documentation](https://docs.gitlab.com/runner/install/docker.html)

2. Run the `proxpi` Docker container
   ```bash
   docker run \
     --detach \
     --network gitlab-runner-network \
     --volume proxpi-cache:/var/cache/proxpi \
     --env PROXPI_CACHE_DIR=/var/cache/proxpi \
     --name proxpi epicwink/proxpi:latest
   ```
   You don't need to expose a port (the `-p` flag) as we'll be using an internal
   Docker network.

4. Set `pip`'s index URL to the `proxpi` server by setting it in the runner environment.
   Set `runners[0].docker.network_mode` to `gitlab-runner-network`.
   Add `PIP_INDEX_URL=http://proxpi:5000/index/` and `PIP_TRUSTED_HOST=proxpi`
   to `runners.environment` in the GitLab CI runner configuration TOML. For example, you
   may end up with the following configuration:
   ```toml
   [[runners]]
     name = "awesome-ci-01"
     url = "https://gitlab.com/"
     token = "SECRET"
     executor = "docker"
     environment = [
       "DOCKER_TLS_CERTDIR=/certs",
       "PIP_INDEX_URL=http://proxpi:5000/index/",
       "PIP_TRUSTED_HOST=proxpi",
     ]
   
   [[runners.docker]]
     network_mode = "gitlab-runner-network"
     ...
   ```

This is designed to not require any changes to the GitLab CI project configuration (ie
`gitlab-ci.yml`), unless it already sets the index URL for some reason (if that's the
case, you're probably already using a cache).

Another option is to set up a proxy, but that's more effort than the above method.

## Alternatives
* [simpleindex](https://pypi.org/project/simpleindex/): routes URLs to multiple
  indices (including PyPI), supports local (or S3 with a plygin) directory of packages,
  no caching without custom plugins

* [bandersnatch](https://pypi.org/project/bandersnatch/): mirrors one index (eg PyPI),
  storing packages locally, or on S3 with a plugin. Manual update, no proxy

* [devpi](https://pypi.org/project/devpi/): heavyweight, runs a full index (or multiple)
  in addition to mirroring (in place of proxying), supports proxying (with inheritance),
  supports package upload, server replication and fail-over

* [pypiserver](https://pypi.org/project/pypiserver/): serves local directory of
  packages, proxy to PyPI when not-found, supports package upload, no caching

* [PyPI Cloud](https://pypi.org/project/pypicloud/): serves local or cloud-storage
  directory of packages, with redirecting/cached proxying to indexes, authentication and
  authorisation.

* [`pypiprivate`](https://pypi.org/project/pypiprivate/): serves local (or S3-hosted)
  directory of packages, no proxy to package indices (including PyPI)

* [Pulp](https://pypi.org/project/pulpcore/): generic content repository, can host
  multiple ecosystems' packages.
  [Python package index plugin](https://pypi.org/project/pulp-python/) supports local/S3
  mirrors, package upload, proxying to multiple indices, no caching

* [`pip2pi`](https://pypi.org/project/pip2pi/): manual syncing of specific packages,
  no proxy

* [`nginx_pypi_cache`](https://github.com/hauntsaninja/nginx_pypi_cache): caching proxy
  using [nginx](https://nginx.org/en/), single index

* [Flask-Pypi-Proxy](https://pypi.org/project/Flask-Pypi-Proxy/): unmaintained, no cache
  size limit, no caching index pages

* [`http.server`](https://docs.python.org/3/library/http.server.html): standard-library,
  hosts directory exactly as laid out, no proxy to package indices (eg PyPI)

* [Apache with `mod_rewrite`](
  https://httpd.apache.org/docs/current/mod/mod_rewrite.html): I'm not familiar with
  Apache, but it likely has the capability to proxy and cache (with eg `mod_cache_disk`)

* [Gemfury](https://fury.co/l/pypi-server): hosted, managed. Private index is not free,
  documentation doesn't say anything about proxying
