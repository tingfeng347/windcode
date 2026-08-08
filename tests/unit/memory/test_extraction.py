from windcode.memory import (
    MemoryKind,
    classify_memory_intent,
    has_explicit_memory_intent,
    is_project_fact,
    is_stable_user_fact,
    should_assess_experience,
)


def test_stable_preference_is_detected_without_remember_keyword() -> None:
    assert is_stable_user_fact("我喜欢先运行聚焦测试")
    assert is_stable_user_fact("I prefer concise final answers")


def test_stable_name_is_detected_without_remember_keyword() -> None:
    assert is_stable_user_fact("我叫tingfeng347")
    assert is_stable_user_fact("请叫我 tingfeng347")
    assert classify_memory_intent("我的名字是 tingfeng347") is MemoryKind.USER_PROFILE


def test_questions_are_not_saved_as_facts() -> None:
    assert not is_stable_user_fact("我喜欢什么？")  # noqa: RUF001
    assert not is_stable_user_fact("What do I prefer?")


def test_explicit_memory_intent_remains_supported() -> None:
    assert has_explicit_memory_intent("记住这个项目使用 Python 3.12")
    assert is_project_fact("记住这个项目使用 Python 3.12")
    assert not is_project_fact("记住我喜欢 Python")


def test_explicit_experience_is_not_classified_as_user_profile() -> None:
    assert (
        classify_memory_intent("记住一条经验: 检查代码规范时先运行 ruff check")
        is MemoryKind.EXPERIENCE
    )
    assert classify_memory_intent("记住这个项目使用 Python 3.12") is MemoryKind.PROJECT_KNOWLEDGE
    assert classify_memory_intent("记住以下参考资料") is MemoryKind.REFERENCE
    assert classify_memory_intent("我喜欢简洁回答") is MemoryKind.USER_PROFILE


def test_explicit_experience_takes_precedence_over_workflow_markers() -> None:
    assert classify_memory_intent("把 commit 工作流程的经验记下来") is MemoryKind.EXPERIENCE
    assert classify_memory_intent("记住这个工作流程") is MemoryKind.SOP


def test_experience_assessment_requires_change_and_verification() -> None:
    assert not should_assess_experience(
        status="completed", changed_files=(), verification=("ruff check (exit 0)",)
    )
    assert not should_assess_experience(
        status="unverified", changed_files=("a.py",), verification=()
    )
    assert should_assess_experience(
        status="completed",
        changed_files=("a.py",),
        verification=("pytest -q (exit 0)",),
    )
