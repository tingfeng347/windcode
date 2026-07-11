from windcode.observability import REDACTED, redact


def test_recursively_redacts_sensitive_fields_and_known_values() -> None:
    original = {
        "Authorization": "Bearer top-secret",
        "nested": [{"api-key": "top-secret"}, "prefix top-secret suffix"],
        "input_tokens": 12,
    }

    result = redact(original, secrets=["top-secret"])

    assert result == {
        "Authorization": REDACTED,
        "nested": [{"api-key": REDACTED}, f"prefix {REDACTED} suffix"],
        "input_tokens": 12,
    }
    assert original["Authorization"] == "Bearer top-secret"


def test_preserves_tuple_shape() -> None:
    assert redact(("visible", {"password": "hidden"})) == (
        "visible",
        {"password": REDACTED},
    )
