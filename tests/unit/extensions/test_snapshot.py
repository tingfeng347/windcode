from windcode.extensions.discovery import DiscoveryResult
from windcode.extensions.snapshot import SnapshotPublisher, build_candidate


def test_publisher_increments_and_keeps_old_snapshot_on_rejected_candidate() -> None:
    publisher = SnapshotPublisher()
    accepted = build_candidate(DiscoveryResult((), {}, ()), generation=1, config={"a": 1})
    assert publisher.publish(accepted)
    old = publisher.current

    rejected = type(accepted)(
        build_candidate(DiscoveryResult((), {}, ()), generation=2, config={"a": 2}).snapshot,
        False,
    )
    assert not publisher.publish(rejected)
    assert publisher.current is old


def test_fingerprint_is_stable_for_mapping_order() -> None:
    one = build_candidate(DiscoveryResult((), {}, ()), generation=1, config={"a": 1, "b": 2})
    two = build_candidate(DiscoveryResult((), {}, ()), generation=1, config={"b": 2, "a": 1})
    assert one.snapshot.config_fingerprint == two.snapshot.config_fingerprint
