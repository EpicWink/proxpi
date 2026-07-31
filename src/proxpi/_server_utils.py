"""Cached package index server utilities."""

import typing as t

if t.TYPE_CHECKING:
    import fastapi.responses
    import fastapi.templating


def _parse_header_value_parameters(texts: t.Iterable[str]) -> t.List[t.Tuple[str, str]]:
    parameters = []  # type: t.List[t.Tuple[str, str]]
    for text in texts:
        name, value = text.strip().split("=", maxsplit=1)
        parameters.append((name.lower(), value))
    return sorted(parameters, key=lambda x: x[0])


def _pop_quality_parameter(parameters: t.List[t.Tuple[str, str]]) -> float:
    for i, (name, value) in enumerate(parameters):
        if name == "q":
            parameters.pop(i)
            return float(value)
    return 1.0


def parse_accept_encoding_header(
    header_value: t.Union[str, None],
) -> t.Callable[[str], float]:
    def get_quality(t_value: str, default: float = 0.0) -> float:
        # Parse requested media type
        t_value, *parameter_texts = t_value.split(";")
        t_value = t_value.lower()
        parameters = _parse_header_value_parameters(parameter_texts)

        # Find quality
        for hv_value, hv_parameters, quality in qualities:
            if hv_value == "*" or (t_value == hv_value and parameters == hv_parameters):
                return quality
        return default

    qualities = []  # type: t.List[t.Tuple[str, t.List[t.Tuple[str, str]], float]]
    for part in header_value.split(","):
        v_value = part.strip()
        v_value, *v_parameter_texts = v_value.split(";")

        v_parameters = _parse_header_value_parameters(v_parameter_texts)
        v_quality = _pop_quality_parameter(v_parameters)

        qualities.append((v_value.lower(), v_parameters, v_quality))

    return get_quality


def parse_accept_header(
    header_value: t.Union[str, None],
) -> t.Callable[[str], float]:
    def get_quality(media_type: str, default: float = 0.0) -> float:
        # Parse requested media type
        media_type, *parameter_texts = media_type.split(";")

        parameters = _parse_header_value_parameters(parameter_texts)

        maintype, subtype = media_type.split("/", maxsplit=1)
        maintype = maintype.lower()
        subtype = subtype.lower()

        # Find quality
        for ah_type, ah_subtype, ah_parameters, quality in qualities:
            if (ah_type == "*" and ah_subtype == "*") or (
                maintype == ah_type
                and (
                    ah_subtype == "*"
                    or (subtype == ah_subtype and parameters == ah_parameters)
                )
            ):
                return quality
        return default

    qualities = []  # type: t.List[t.Tuple[str, str, t.List[t.Tuple[str, str]], float]]
    for part in header_value.split(","):
        v_media_type = part.strip()
        v_media_type, *v_parameter_texts = v_media_type.split(";")

        v_parameters = _parse_header_value_parameters(v_parameter_texts)
        v_quality = _pop_quality_parameter(v_parameters)

        v_maintype, v_subtype = v_media_type.split("/", maxsplit=1)
        v_maintype = v_maintype.lower()
        v_subtype = v_subtype.lower()

        qualities.append((v_maintype, v_subtype, v_parameters, v_quality))

    return get_quality


def add_vary(header_name: str, response: "fastapi.Response") -> None:
    if response.headers.get("Vary"):
        if header_name.lower() not in set(
            n.strip().lower() for n in response.headers["Vary"].split(",")
        ):
            response.headers["Vary"] += ", " + header_name
    else:
        response.headers["Vary"] = header_name
