"""Dependency-free helpers for the cheap input calibration gate."""


def normalize_topic_for_gate(value: str) -> str:
    normalized = value.casefold().strip()
    normalized = normalized.replace("(", " ").replace(")", " ").replace("-", " ")
    return " ".join(normalized.split())


def contains_hangul(value: str) -> bool:
    return any("\uac00" <= char <= "\ud7a3" for char in value)


def looks_too_broad_for_demo(topic: str) -> bool:
    normalized = normalize_topic_for_gate(topic)
    tokens = normalized.split()
    # Hard-gate only obvious short single-token inputs. Multi-token terms often
    # encode technique + target/use case, so they should be judged by the
    # preflight model instead of an alias list.
    if len(tokens) != 1:
        return False
    if contains_hangul(normalized):
        compact_len = len(normalized.replace(" ", ""))
        return compact_len <= 6
    return len(tokens[0]) <= 8
