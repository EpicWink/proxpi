"""Benchmark JSON API response size again HTML API.

Note: ``requests`` by default asks for gzip/zlib compression.
"""

import re
import sys
import json

import requests

normalise_pattern = re.compile(r"[^a-z\d-]+")

packages_json = sys.stdin.read()
package_names = sorted(set(p["name"] for p in json.loads(packages_json)))

print("| Project | HTML size (kB) | JSON size (kB) | JSON size ratio |")
print("| ------- | -------------- | -------------- | --------------- |")
ratios = []
for package_name in package_names:
    package_name = normalise_pattern.sub("-", package_name.lower())

    response = requests.get(
        f"http://localhost:5042/index/{package_name}/", headers={"Accept": "text/html"}
    )
    if not response.ok:
        print(
            f"Failed '{package_name}: [{response.status_code}] {response.reason}",
            file=sys.stderr,
        )
        continue
    assert response.headers["Content-Encoding"] in ("gzip", "deflate")
    html_length = response.headers["Content-Length"]

    response = requests.get(
        f"http://localhost:5042/index/{package_name}/", headers={
            "Accept": "application/vnd.pypi.simple.latest+json",
        }
    )
    assert response.headers["Content-Encoding"] in ("gzip", "deflate")
    response.raise_for_status()
    json_length = response.headers["Content-Length"]

    ratio = json_length / html_length
    ratios.append(ratio)

    html_length = round(int(html_length) / 1024, 1)
    json_length = round(int(json_length) / 1024, 1)
    ratio = round(ratio, 2)
    print(f"| {package_name} | {html_length} | {json_length} | {ratio} |")

mean_ratio = sum(ratios) / len(ratios)
ratio_stddev = (sum((r - mean_ratio) ** 2.0 for r in ratios) / len(ratios)) ** 0.5
print(f"\nAverage ratio: {mean_ratio} ± {ratio_stddev}")
