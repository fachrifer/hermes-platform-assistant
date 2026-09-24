from office_gateway.redact import redact_text


def test_redacts_common_secret_shapes():
    cases = {
        "Authorization: Bearer abcdefghijklmnop": "Authorization: Bearer [redacted]",
        "key sk-live_1234567890abcdef used": "key [redacted] used",
        "password=hunter22 ok": "password=[redacted] ok",
        'api_key: "zzzzzzzz"': 'api_key: "[redacted]"',
        "postgres://app:s3cret@db:5432/x": "postgres://app:[redacted]@db:5432/x",
        "AKIAABCDEFGHIJKLMNOP": "[redacted]",
    }
    for raw, expected in cases.items():
        assert redact_text(raw) == expected


def test_leaves_normal_lines():
    line = "GET /grafana/api/health 200 12ms"
    assert redact_text(line) == line
