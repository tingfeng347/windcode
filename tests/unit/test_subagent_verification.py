import asyncio
from pathlib import Path

import pytest

from windcode.runtime.subagents.verification import VerificationRunner


async def test_verification_preserves_order_and_stops_after_failure(tmp_path: Path) -> None:
    runner = VerificationRunner()
    results = await runner.run(
        ("printf first", "printf failed >&2; exit 3", "printf skipped"),
        workspace=tmp_path,
        run_id="run",
    )
    assert [result.command for result in results] == [
        "printf first",
        "printf failed >&2; exit 3",
    ]
    assert results[0].passed
    assert not results[1].passed
    assert results[1].exit_code == 3
    assert "failed" in results[1].output_summary


async def test_verification_reports_timeout(tmp_path: Path) -> None:
    runner = VerificationRunner(timeout_seconds=0.01)
    result = (await runner.run(("sleep 1",), workspace=tmp_path, run_id="run"))[0]
    assert not result.passed
    assert "timed out" in result.output_summary


async def test_verification_honors_cancellation(tmp_path: Path) -> None:
    runner = VerificationRunner()
    with pytest.raises(asyncio.CancelledError):
        await runner.run(
            ("printf never",), workspace=tmp_path, run_id="run", cancelled=lambda: True
        )
