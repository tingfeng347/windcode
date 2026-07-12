from __future__ import annotations

PERMISSION_LABELS = {
    "plan": "计划",
    "default": "默认",
    "accept_edits": "自动编辑",
    "full_access": "完全授权",
}

# Keep permission modes visually consistent with MewCode.
PERMISSION_STYLES = {
    "plan": "yellow",
    "default": "dim",
    "accept_edits": "green",
    "full_access": "red",
}


def permission_label(permission: str) -> str:
    return PERMISSION_LABELS.get(permission, permission)


def permission_style(permission: str) -> str:
    return PERMISSION_STYLES.get(permission, "dim")
