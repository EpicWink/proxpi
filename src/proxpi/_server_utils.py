"""Cached package index server utilities."""

import typing as t
import functools

if t.TYPE_CHECKING:
    import email.headerregistry

    import fastapi.responses
    import fastapi.templating

    class _ContentTypeHeader(
        email.headerregistry.ContentTypeHeader,
        email.headerregistry.BaseHeader,
    ):
        pass


T = t.TypeVar("T")


@functools.lru_cache(maxsize=None)
def _get_content_type_class() -> t.Type["_ContentTypeHeader"]:
    import email.headerregistry

    return t.cast(
        t.Type["_ContentTypeHeader"],
        email.headerregistry.HeaderRegistry()["Content-Type"],
    )


@functools.lru_cache()
def _parse_content_type(media_type: str) -> "_ContentTypeHeader":
    content_type_class = _get_content_type_class()
    return content_type_class("Content-Type", media_type)


@functools.lru_cache()
def parse_accept_encoding_header(
    header_value: t.Union[str, None],
) -> t.Callable[[str], float]:
    def get_quality(t_value: str, default: float = 0.0) -> float:
        # Parse requested media type
        c = _parse_content_type("dummy/" + t_value.strip())

        # Find quality
        for hv_value, hv_parameters, quality in qualities:
            if hv_value == "*" or (c.subtype == hv_value and c.params == hv_parameters):
                return quality
        return default

    if header_value is None:
        header_value = "*"

    qualities = []  # type: t.List[t.Tuple[str, t.Mapping[str, str], float]]
    for part in header_value.split(","):
        cth = _parse_content_type("dummy/" + part.strip())
        qualities.append((cth.subtype, cth.params, float(cth.params.get("q", 1.0))))

    return get_quality


@functools.lru_cache()
def parse_accept_header(
    header_value: t.Union[str, None],
) -> t.Callable[[str], float]:
    def get_quality(media_type: str, default: float = 0.0) -> float:
        # Parse requested media type
        c = _parse_content_type(media_type.strip())
        c_params = {k: v for k, v in c.params.items() if k != "q"}

        # Find quality
        for ah_type, ah_subtype, ah_parameters, quality in qualities:
            if (ah_type == "*" and ah_subtype == "*") or (
                c.maintype == ah_type
                and (
                    ah_subtype == "*"
                    or (c.subtype == ah_subtype and c_params == ah_parameters)
                )
            ):
                return quality
        return default

    if header_value is None:
        header_value = "*/*"

    qualities = []  # type: t.List[t.Tuple[str, str, t.Mapping[str, str], float]]
    for part in header_value.split(","):
        cth = _parse_content_type(part.strip())
        qualities.append((
            cth.maintype,  # ie 'text' part of 'text/plain'
            cth.subtype,  # ie 'plain' part of 'text/plain'
            {k: v for k, v in cth.params.items() if k != "q"},  # non-quality params
            float(cth.params.get("q", 1.0)),  # quality
        ))  # fmt: skip

    return get_quality


def add_vary(header_name: str, response: "fastapi.Response") -> None:
    response.headers.add_vary_header(header_name)
