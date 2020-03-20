# proxpi
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
* `PIP_INDEX_URL`: root index URL, default: https://pypi.org/simple/
* `INDEX_TTL`: root index time-to-live (aka cache time-out) in seconds, default: 30
   minutes
* `PIP_EXTRA_INDEX_URL`: extra index URLs (white-space separated)
* `EXTRA_INDEX_TTL`: corresponding extra index times-to-live in seconds (white-space
   separated), default: 3 minutes
* `CACHE_SIZE`: size of downloaded package files cache (bytes), default 5GB


### Docker
```bash
docker run -p 5000:5000 epicwink/proxpi
```
