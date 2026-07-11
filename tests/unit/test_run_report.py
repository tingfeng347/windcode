from windcode.domain.tools import ToolResult
from windcode.runtime.report import ToolExecutionRecord, build_run_result


def test_reports_changes_and_successful_verification() -> None:
    records = (
        ToolExecutionRecord(
            "write_file",
            {"path": "a.py"},
            ToolResult("diff", data={"path": "a.py", "action": "modified"}),
        ),
        ToolExecutionRecord(
            "shell",
            {"command": "pytest -q"},
            ToolResult("passed", data={"command": "pytest -q", "exit_code": 0}),
        ),
    )
    result = build_run_result("done", records)
    assert result.status == "completed"
    assert result.changed_files == ("a.py",)
    assert result.verification == ("pytest -q (exit 0)",)


def test_failed_or_missing_verification_is_not_success() -> None:
    failed = build_run_result(
        "done",
        (
            ToolExecutionRecord(
                "shell",
                {"command": "pytest"},
                ToolResult("failed", is_error=True, data={"exit_code": 1}),
            ),
        ),
    )
    assert failed.status == "failed"
    assert build_run_result("done", ()).status == "unverified"
