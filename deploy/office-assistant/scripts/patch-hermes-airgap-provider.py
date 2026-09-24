#!/usr/bin/env python3
"""Patch Hermes v2026.8.31 so airgap LiteLLM is not forced onto OpenRouter.

OPENAI_API_KEY currently makes resolve_provider() return "openrouter". Named
providers such as office-litellm are not in PROVIDER_REGISTRY, so Chat and
bot-peer messaging try OpenRouter and fail offline.

Does not print secrets. Idempotent.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

AUTH_OPENROUTER_ENV = re.compile(
    r"if has_usable_secret\(_scoped_key_env\(\"OPENAI_API_KEY\"\)\) or has_usable_secret\(\s+"
    r"_scoped_key_env\(\"OPENROUTER_API_KEY\"\)\s+\):\s+"
    r"return \"openrouter\"",
    re.M,
)

AUTH_OPENROUTER_REPL = """if has_usable_secret(_scoped_key_env("OPENAI_API_KEY")) or has_usable_secret(
        _scoped_key_env("OPENROUTER_API_KEY")
    ):
        # office-airgap-provider-patch
        _base = (_scoped_key_env("OPENAI_BASE_URL") or "").strip().lower()
        if _base and "openrouter.ai" not in _base:
            return "custom"
        return "openrouter\""""

NAMED_CUSTOM_IF = "if requested_norm in custom_provider_aliases("
NAMED_CUSTOM_IF_PATCHED = (
    "if requested_norm == str(ep_name).strip().lower() "
    "or requested_norm in custom_provider_aliases("
)

TRUST_TAIL = re.compile(
    r"(if base_url_host_matches\(bu, \"openrouter\.ai\"\):\n"
    r"\s+return False\n)"
    r"(?P<ind>\s+)return _loopback_hostname\(base_url_hostname\(bu\)\)"
)

LITELLM_KEY_MARK = "office-airgap-provider-patch: litellm-key"
POOL_URL_KEY_MARK = "office-airgap-provider-patch: url-must-not-be-key"
SANITIZE_MARK = "office-airgap-provider-patch: sanitize-url-key"
URL_SECRET_MARK = "office-airgap-provider-patch: url-is-not-a-secret"

USABLE_SECRET_RE = re.compile(
    r"(if cleaned\.lower\(\) in _PLACEHOLDER_SECRET_VALUES:\n"
    r"(?P<body>\s+)return False\n)"
    r"(?P<ind>\s+)return True"
)

OPENAI_KEY_GATE_RES = (
    re.compile(
        r'(\(_getenv\("OPENAI_API_KEY", ""\)\.strip\(\))\s+'
        r"if (_da_is_openai_url) else \"\"\),"
    ),
    re.compile(
        r'(\(_getenv\("OPENAI_API_KEY", ""\)\.strip\(\))\s+'
        r"if (_cp_is_openai_url)\s+else \"\"\),"
    ),
    re.compile(
        r'(\(_getenv\("OPENAI_API_KEY"\))\s+'
        r"if \((_is_openai_url or _is_openai_azure)\) else \"\"\),"
    ),
)

POOL_URL_RE = re.compile(
    r"(if not pool_api_key:\n)"
    r"(?P<body>\s+)return None\n"
    r"(?P<ind>\s+)if not has_usable_secret\(pool_api_key\) and "
    r"_loopback_hostname\(base_url_hostname\(base_url\)\):"
)


def _usable_litellm_virtual_key(value: str) -> str:
    """Return value only when it can be a LiteLLM Bearer token, else empty."""
    key = (value or "").strip()
    if not key or key == "no-key-required":
        return ""
    if key.startswith("${") and key.endswith("}"):
        return ""
    if key.lower().startswith(("http://", "https://")):
        return ""
    return key


def sanitize_runtime_api_key(api_key: str, env_api_key: str) -> str:
    """Never send URL / placeholder as a LiteLLM virtual key (Bearer must be sk-…)."""
    return (
        _usable_litellm_virtual_key(api_key)
        or _usable_litellm_virtual_key(env_api_key)
        or "no-key-required"
    )


SANITIZE_TAIL = '''

# office-airgap-provider-patch: sanitize-url-key
def _office_airgap_usable_litellm_key(value):
    key = str(value or "").strip()
    if not key or key == "no-key-required":
        return ""
    if key.startswith("${") and key.endswith("}"):
        return ""
    if key.lower().startswith(("http://", "https://")):
        return ""
    return key


def _office_airgap_litellm_api_key():
    try:
        key = (_getenv("OPENAI_API_KEY") or "").strip()
    except NameError:
        key = (os.getenv("OPENAI_API_KEY") or "").strip()
    return _office_airgap_usable_litellm_key(key)


def _office_airgap_sanitize_runtime(result):
    if not isinstance(result, dict):
        return result
    key = str(result.get("api_key") or "").strip()
    picked = _office_airgap_usable_litellm_key(key) or _office_airgap_litellm_api_key()
    if picked == key:
        return result
    out = dict(result)
    out["api_key"] = picked or "no-key-required"
    return out


_office_airgap_orig_resolve_runtime_provider = resolve_runtime_provider


def resolve_runtime_provider(*args, **kwargs):
    return _office_airgap_sanitize_runtime(
        _office_airgap_orig_resolve_runtime_provider(*args, **kwargs)
    )
'''


def _openai_key_repl(match: re.Match[str]) -> str:
    return (
        f"{match.group(1)} if {match.group(2)} or ("
        '(_getenv("OPENAI_BASE_URL") or "").strip() and '
        '"openrouter.ai" not in (_getenv("OPENAI_BASE_URL") or "").lower()) else ""),'
        f"  # {LITELLM_KEY_MARK}"
    )


def _pool_url_repl(match: re.Match[str]) -> str:
    body = match.group("body")
    ind = match.group("ind")
    return (
        f"{match.group(1)}{body}return None\n"
        f"{ind}# {POOL_URL_KEY_MARK}\n"
        f'{ind}if str(pool_api_key).strip().lower().startswith(("http://", "https://")):\n'
        f"{body}return None\n"
        f"{ind}if not has_usable_secret(pool_api_key) and "
        f"_loopback_hostname(base_url_hostname(base_url)):"
    )


def _usable_secret_repl(match: re.Match[str]) -> str:
    body = match.group("body")
    ind = match.group("ind")
    return (
        f"{match.group(1)}"
        f"{ind}# {URL_SECRET_MARK}\n"
        f'{ind}if cleaned.lower().startswith(("http://", "https://")):\n'
        f"{body}return False\n"
        f"{ind}return True"
    )


def patch_has_usable_secret(text: str, *, required: bool = False) -> str:
    if URL_SECRET_MARK in text:
        return text
    patched, n = USABLE_SECRET_RE.subn(_usable_secret_repl, text, count=1)
    if n != 1:
        if required:
            raise SystemExit(
                "patch-hermes-airgap-provider: has_usable_secret needle not found in auth.py"
            )
        return text
    return patched


def patch_auth_py(text: str) -> str:
    out = text
    if not (
        "office-airgap-provider-patch" in out
        and 'return "custom"' in out
        and "OPENAI_BASE_URL" in out
    ):
        patched, n = AUTH_OPENROUTER_ENV.subn(AUTH_OPENROUTER_REPL, out, count=1)
        if n != 1:
            raise SystemExit(
                "patch-hermes-airgap-provider: OPENAI_API_KEY openrouter fallback not found in auth.py"
            )
        out = patched
    return patch_has_usable_secret(out)


def _trust_repl(match: re.Match[str]) -> str:
    ind = match.group("ind")
    return (
        f"{match.group(1)}"
        f"{ind}# office-airgap-provider-patch: trust OPENAI_BASE_URL\n"
        f'{ind}env_base = (os.getenv("OPENAI_BASE_URL") or "").strip().lower()\n'
        f'{ind}if env_base and "openrouter.ai" not in env_base:\n'
        f"{ind}    return True\n"
        f"{ind}return _loopback_hostname(base_url_hostname(bu))"
    )


def patch_runtime_provider_py(text: str) -> str:
    out = text
    applied = False
    if NAMED_CUSTOM_IF_PATCHED in out:
        applied = True
    elif NAMED_CUSTOM_IF in out:
        out = out.replace(NAMED_CUSTOM_IF, NAMED_CUSTOM_IF_PATCHED, 1)
        applied = True
    if "office-airgap-provider-patch: trust OPENAI_BASE_URL" in out:
        applied = True
    else:
        patched, n = TRUST_TAIL.subn(_trust_repl, out, count=1)
        if n == 1:
            out = patched
            applied = True
    if LITELLM_KEY_MARK in out:
        applied = True
    else:
        for cre in OPENAI_KEY_GATE_RES:
            out, n = cre.subn(_openai_key_repl, out, count=1)
            if n:
                applied = True
    if POOL_URL_KEY_MARK in out:
        applied = True
    else:
        patched, n = POOL_URL_RE.subn(_pool_url_repl, out, count=1)
        if n == 1:
            out = patched
            applied = True
    if SANITIZE_MARK in out:
        applied = True
    elif "def resolve_runtime_provider" in out:
        out = out.rstrip() + SANITIZE_TAIL
        applied = True
    if not applied:
        raise SystemExit(
            "patch-hermes-airgap-provider: runtime_provider.py needles not found"
        )
    return out


def _write_if_changed(path: Path, original: str, patched: str, label: str) -> None:
    if original == patched:
        print(f"patch-hermes-airgap-provider: {label} already patched")
        return
    # Published image copies hermes_cli as mode 444 (root cannot write until u+w).
    try:
        path.chmod(path.stat().st_mode | 0o200)
    except OSError as exc:
        raise SystemExit(
            f"patch-hermes-airgap-provider: cannot chmod u+w {path}: {exc}"
        ) from exc
    path.write_text(patched)
    print(f"patch-hermes-airgap-provider: patched {label}")


def main() -> None:
    root = Path(os.environ.get("OFFICE_HERMES_SRC", "/opt/hermes"))
    auth = root / "hermes_cli" / "auth.py"
    runtime = root / "hermes_cli" / "runtime_provider.py"
    if not auth.is_file() or not runtime.is_file():
        raise SystemExit(f"patch-hermes-airgap-provider: missing {auth} or {runtime}")
    auth_patched = patch_has_usable_secret(patch_auth_py(auth.read_text()), required=True)
    runtime_patched = patch_runtime_provider_py(runtime.read_text())
    if NAMED_CUSTOM_IF_PATCHED not in runtime_patched:
        raise SystemExit(
            "patch-hermes-airgap-provider: named custom key match missing after patch"
        )
    if "office-airgap-provider-patch: trust OPENAI_BASE_URL" not in runtime_patched:
        raise SystemExit(
            "patch-hermes-airgap-provider: bare-custom trust patch missing after patch"
        )
    if LITELLM_KEY_MARK not in runtime_patched:
        raise SystemExit(
            "patch-hermes-airgap-provider: LiteLLM OPENAI_API_KEY ungating missing after patch"
        )
    if POOL_URL_KEY_MARK not in runtime_patched:
        raise SystemExit(
            "patch-hermes-airgap-provider: URL-as-pool-key guard missing after patch"
        )
    if SANITIZE_MARK not in runtime_patched:
        raise SystemExit(
            "patch-hermes-airgap-provider: URL-as-api-key sanitizer missing after patch"
        )
    if URL_SECRET_MARK not in auth_patched:
        raise SystemExit(
            "patch-hermes-airgap-provider: URL-as-secret guard missing after patch"
        )
    _write_if_changed(auth, auth.read_text(), auth_patched, str(auth))
    _write_if_changed(runtime, runtime.read_text(), runtime_patched, str(runtime))


if __name__ == "__main__":
    try:
        main()
    except OSError as exc:
        print(f"patch-hermes-airgap-provider: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
