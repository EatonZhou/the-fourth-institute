from collections.abc import Mapping


def validate_metaness(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 10:
        raise ValueError("metaness 必须是 1 到 10 的整数")
    return value


def document_metaness(metadata: Mapping[str, object]) -> int | None:
    if "metaness" not in metadata:
        return None
    return validate_metaness(metadata["metaness"])
