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
Either run `flask` locally
```bash
FLASK_APP=proxpi.server flask run
```

Or use Docker
```bash
docker run -p 5000:5000 epicwink/proxpi
```

See `flask run --help` for more information on address and port binding, and certificate
specification to use HTTPS. Alternatively, bring your own WSGI server.

Use PIP's index-URL flag to install packages via the proxy

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
* `PROXPI_CACHE_DIR`: downloaded package files cache directory path, default: a new
  temporary directory

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

`proxpi` works by caching index requests (ie which versions, wheel-types, etc) are
available for a given package (the index cache) and the package files themselves (to a
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
   docker run --detach --network gitlab-runner-network --name proxpi epicwink/proxpi:latest
   ```
   You don't need to expose a port (the `-p` flag) as we'll be using the internal
   Docker (bridge) network.

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
