#!/usr/bin/env sh

# Test Docker image
#
# Usage: test-docker-image.sh REPO/IMAGE:TAG

# Make command errors cause script to fail
set -e

cleanUp () {
  docker logs "$container"

  docker stop "$container"
  echo "stopped container" 1>&2

  rm --recursive "$tempDir"
  echo "deleted temporary directory" 1>&2
}

# Start proxpi server
container="$(docker create \
  --publish 5042:5000 \
  "$1"
)"
echo "created container: $container" 1>&2

docker start "$container"
echo "started container" 1>&2

sleep 1

# Create temporary directory
tempDir="$(mktemp -d)"
echo "created temporary directory: $tempDir" 1>&2

# Run test
pip \
  --no-cache-dir \
  download \
  --index-url http://localhost:5042/index \
  --dest "$tempDir/a" \
  jinja2 marshmallow \
  || (cleanUp && false)
echo "step 1 passed" 1>&2

pip \
  --no-cache-dir \
  download \
  --index-url http://localhost:5042/index \
  --dest "$tempDir/b" \
  jinja2 marshmallow \
  || (cleanUp && false)
echo "step 2 passed" 1>&2

cleanUp || true

echo "passed" 1>&2
