# proxpi
Local PyPI mirror cache

## Installation
```bash
pip install proxpi
```

## Usage
```bash
FLASK_APP=proxpi flask run
```

```bash
pip install --index-url http://127.0.0.1:5000/index/ simplejson
```

### Environment variables
* `PIP_INDEX_URL`: root index URL, default: https://pypi.org/simple/
* `INDEX_TTL`: root index time-to-live (aka cache time-out) in seconds, default: 30
   minutes
* `PIP_EXTRA_INDEX_URL`: extra index URLs (white-space separated)
* `EXTRA_INDEX_TTL`: corresponding extra index times-to-live in seconds (white-space
   separated), default: 5 minutes
