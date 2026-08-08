from windcode.providers.models import parse_model_ids


def test_parse_model_ids_sorts_deduplicates_and_ignores_invalid_items() -> None:
    payload = {
        "data": [
            {"id": "model-z"},
            {"id": "model-a"},
            {"id": "model-z"},
            {"name": "missing-id"},
            "invalid",
        ]
    }

    assert parse_model_ids(payload) == ("model-a", "model-z")


def test_parse_model_ids_rejects_unexpected_payload_shape() -> None:
    assert parse_model_ids({"models": []}) == ()
    assert parse_model_ids([]) == ()
