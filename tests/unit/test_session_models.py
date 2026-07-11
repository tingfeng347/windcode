from datetime import UTC, datetime

from windcode.sessions import EventRecord, SessionMetadata


def test_session_models_round_trip() -> None:
    now = datetime.now(UTC)
    metadata = SessionMetadata(session_id="session", created_at=now, updated_at=now)
    record = EventRecord(
        sequence=1,
        record_id="record",
        parent_id=None,
        record_type="event",
        payload={"nested": [1, True]},
        created_at=now,
    )

    assert SessionMetadata.from_dict(metadata.to_dict()) == metadata
    assert EventRecord.from_dict(record.to_dict()) == record
