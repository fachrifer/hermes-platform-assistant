from __future__ import annotations


def compare_mig(expected: dict[str, int], actual: dict[str, int]) -> dict:
    missing = {}
    extra = {}
    for profile, count in expected.items():
        have = actual.get(profile, 0)
        if have < count:
            missing[profile] = count - have
        elif have > count:
            extra[profile] = have - count
    for profile, have in actual.items():
        if profile not in expected and have:
            extra[profile] = have
    return {
        "expected": dict(expected),
        "actual": dict(actual),
        "missing": missing,
        "extra": extra,
        "ok": not missing and not extra,
    }
