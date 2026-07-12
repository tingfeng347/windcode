"""发现并显式激活 examples/skills/release_notes Skill。"""
# ruff: noqa: RUF001

import asyncio
from pathlib import Path

from windcode import Windcode
from windcode.config import load_config
from windcode.types import RunRequest


async def main() -> None:
    workspace = Path.cwd().resolve()
    skill_root = Path(__file__).parent / "skills"
    config = load_config(workspace)
    extensions = config.extensions.model_copy(
        update={"enabled": True, "skill_roots": (*config.extensions.skill_roots, str(skill_root))}
    )
    config = config.model_copy(update={"extensions": extensions})

    async with Windcode.open(config, workspace=workspace) as client:
        await client.trust_extension_workspace(workspace)
        await client.reload_extensions()
        skills = client.search_skills("release")
        if not skills:
            raise RuntimeError("未发现 release-notes Skill，请检查 examples/skills 目录")
        for skill in skills:
            print(f"发现 Skill: ${skill.name} - {skill.description}")

        handle = client.start_run(
            RunRequest(
                "$release-notes 根据当前 git diff 生成一份中文发布说明，不要修改文件。", workspace
            )
        )
        async for event in handle:
            if event.kind == "text_delta":
                print(getattr(event, "text", ""), end="", flush=True)
        result = await handle.result()
        print(f"\n状态: {result.status}")


if __name__ == "__main__":
    asyncio.run(main())
