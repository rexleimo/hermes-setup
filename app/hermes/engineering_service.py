"""工程规范层（Engineering Guardrails）：给 Hermes 注入「Agent OS」式工程概念。

参考 AIOS（AI Agent Operating System）的文件系统管理思想，通过 Hermes 的三个
官方机制落地，全部幂等、可卸载、不破坏用户手写内容：

1. SOUL.md 托管块 —— 系统提示第一槽位，写入带标记的「工程纪律」段，
   标记之间内容由本平台管理，标记之外的用户内容永不触碰；
2. skills/ 工程技能 —— project-init / requirement-digest / file-placement 三个
   SKILL.md（agentskills.io 兼容 frontmatter），按需渐进加载；
3. agent.coding_instructions —— 官方支持的常驻编码指令键（可选开关）。

另提供工作区物理初始化：按规范 mkdir 目录树并落模板文件。
"""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core import appsettings
from app.hermes.config_store import ensure_path, get_path, load_config, save_config
from app.hermes.paths import HermesPaths, detect

SOUL_BEGIN = "<!-- hermes-console:engineering begin（本段由 Hermes Console 管理，勿手改） -->"
SOUL_END = "<!-- hermes-console:engineering end -->"
SETTING_KEY = "engineering_settings"
SKILL_MARKER = "managed-by: hermes-console"

DEFAULT_WORKSPACE = "~/hermes-workspace"

DEFAULT_RULES = """\
任何非平凡任务先确认/创建项目目录，禁止直接在 HOME 或工作区根部落地文件
下载与外部材料一律放入 downloads/，产出物一律放入所属项目的子目录
动手实现前先写 docs/requirements.md（需求消化）与 docs/plan.md（任务拆解），执行偏离时回写计划
命名使用小写中划线（kebab-case），语义清晰，不用 tmp/新建文件夹/aaa 这类名字
任务结束输出交付清单：产出了什么、在哪个路径、如何验证
完结的项目连同其目录移入 archive/ 并追加日期后缀"""

DEFAULT_REQUIREMENT_TEMPLATE = """\
# 需求：<一句话标题>

## 目标
<这个任务做成什么样算完成>

## 范围与非目标
- 做：
- 不做：

## 约束
<技术栈、依赖、时间、兼容性>

## 验收标准
- [ ] <可勾选的可验证条件>

## 任务拆解
1. <步骤>"""


# ---------------------------------------------------------------------------
# 技能定义（SKILL.md，agentskills.io 兼容 frontmatter）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkillDef:
    slug: str
    name: str
    description: str
    body: str
    tags: tuple[str, ...] = ("engineering", "workflow")


def _skill_doc(name: str, description: str, body: str) -> str:
    return f"""---
name: {name}
description: {description}
version: 1.0.0
author: Hermes Console
metadata:
  hermes:
    tags: [engineering, workflow]
    managed-by: hermes-console
---

{body}
"""


SKILLS: tuple[SkillDef, ...] = (
    SkillDef(
        slug="project-init",
        name="project-init",
        description="新建标准项目：目录脚手架 + 需求/计划文档模板",
        body="""# 项目初始化（Project Init）

## When to Use
用户提出一个需要产出文件/代码/文档的任务，且尚无对应项目目录时。

## Procedure
1. 从需求提炼 2-6 个词的项目名（小写中划线，如 `user-dashboard-refactor`）。
2. 在工作区 `projects/` 下创建：
   ```
   projects/<name>/
   ├── docs/requirements.md   # 需求消化（见 requirement-digest 技能）
   ├── docs/plan.md           # 任务拆解与执行顺序
   ├── src/                   # 代码与产出
   ├── data/                  # 该项目的数据与素材
   └── notes/                 # 过程记录
   ```
3. `docs/requirements.md` 与 `docs/plan.md` 先填骨架再开工。
4. 向用户复述：项目名、目标、验收标准，确认后再动手。

## Pitfalls
- 不要在 HOME、工作区根部或 downloads/ 里直接落地产出物。
- 项目名不要用日期开头，归档时才追加日期。

## Verification
`ls` 项目目录，五个子目录与两份文档齐全。
""",
    ),
    SkillDef(
        slug="requirement-digest",
        name="requirement-digest",
        description="把用户口头需求消化为结构化需求清单与任务拆解",
        body="""# 需求消化（Requirement Digest）

## When to Use
接到任何非平凡指令、需求描述含糊、或任务涉及多步骤时，在动手前执行。

## Procedure
1. 用 `docs/requirements.md` 模板结构化用户需求：
   - **目标**：做成什么样算完成（一句话）
   - **范围与非目标**：明确不做什么
   - **约束**：技术栈、依赖、时间、兼容性
   - **验收标准**：可勾选、可验证的条件
   - **任务拆解**：有序步骤，每步有产出
2. 信息不足时先列「待确认问题」，一次问全，不要挤牙膏。
3. 与用户确认后再进入实现；执行中若偏离计划，先回写 `docs/plan.md`。

## Pitfalls
- 不要拿着半句话就开工；不要把「用户没说」当成「不需要」。
- 验收标准必须是可验证的行为，而不是「做好某功能」。

## Verification
requirements.md 中每条验收标准都能对应到 plan.md 里的至少一个任务。
""",
    ),
    SkillDef(
        slug="file-placement",
        name="file-placement",
        description="文件归位纪律：下载、产出、临时、归档各归其位",
        body="""# 文件归位（File Placement）

## When to Use
任何要创建、下载、移动、清理文件的场景。

## Procedure
| 文件类型 | 去处 |
|---|---|
| 浏览器/命令下载、外部材料 | `downloads/` |
| 项目产出（代码/文档/配置） | `projects/<name>/` 对应子目录 |
| 一次性实验、试错 | `scratch/`（随时可整目录删除） |
| 完结的项目 | `archive/<name>-<YYYYMMDD>/` |

1. 写文件前先想：它属于哪个项目？属于该项目的哪一层？
2. 临时文件用完即删，不进项目目录。
3. 移动文件用 mv 保持历史，不要复制出多份副本。

## Pitfalls
- 禁止 `~/Desktop`、`~/Downloads`、HOME 根部当作工作区。
- 不要为省事把所有东西都堆在项目根。

## Verification
`ls` 工作区根部只看到规范目录，没有散落文件。
""",
    ),
)


# ---------------------------------------------------------------------------
# 设置（持久化在平台 DB，表单可编辑）
# ---------------------------------------------------------------------------

@dataclass
class EngSettings:
    workspace: str = DEFAULT_WORKSPACE
    rules: str = DEFAULT_RULES
    inject_coding_instructions: bool = False
    skills_enabled: tuple[str, ...] = tuple(s.slug for s in SKILLS)

    def to_json(self) -> str:
        return json.dumps({
            "workspace": self.workspace,
            "rules": self.rules,
            "inject_coding_instructions": self.inject_coding_instructions,
            "skills_enabled": list(self.skills_enabled),
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "EngSettings":
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return cls()
        return cls(
            workspace=data.get("workspace", DEFAULT_WORKSPACE),
            rules=data.get("rules", DEFAULT_RULES),
            inject_coding_instructions=bool(data.get("inject_coding_instructions")),
            skills_enabled=tuple(data.get("skills_enabled") or
                                 [s.slug for s in SKILLS]),
        )


def load_settings() -> EngSettings:
    return EngSettings.from_json(appsettings.get_setting(SETTING_KEY))


def save_settings(s: EngSettings) -> None:
    """写入前校验 workspace 根（S2 防劫持）：拒绝过浅/危险路径。

    延迟导入避免与 workspace_service 形成模块级循环。
    """
    from app.hermes.workspace_service import validate_root

    validate_root(s.workspace or DEFAULT_WORKSPACE)  # 不合法抛 WorkspaceError
    appsettings.set_setting(SETTING_KEY, s.to_json())


# ---------------------------------------------------------------------------
# SOUL.md 托管块
# ---------------------------------------------------------------------------

def compose_block(s: EngSettings) -> str:
    workspace = (s.workspace or DEFAULT_WORKSPACE).strip()
    rules = "\n".join(f"- {r.strip()}" for r in s.rules.splitlines() if r.strip())
    return f"""{SOUL_BEGIN}
## 工程纪律（Engineering Guardrails）

你的一切工作遵循工程化规范（类似操作系统的文件管理），把每次任务当作一个项目来管理：

### 工作区布局（根目录：`{workspace}`）
```
{workspace}/
├── projects/<项目名>/        # 所有产出必须归属某个项目
│   ├── docs/requirements.md  # 需求消化：目标/范围/约束/验收标准
│   ├── docs/plan.md          # 任务拆解与执行顺序
│   ├── src/ data/ notes/
├── downloads/                # 下载与外部材料
├── scratch/                  # 一次性实验，随时可清理
└── archive/                  # 完结项目归档（追加日期后缀）
```

### 铁律
{rules}

### 需求消化流程
接到非平凡指令时：先建/认领项目 → 填 `docs/requirements.md`（目标、范围与非目标、
约束、可验证的验收标准、任务拆解）→ 与用户确认 → 按 `docs/plan.md` 执行 →
结束输出交付清单（产出了什么、在哪个路径、如何验证）。信息不足就先列待确认问题一次问全。
{SOUL_END}"""


def _soul_path(paths: HermesPaths) -> Path:
    return paths.home / "SOUL.md"


def read_soul_sections(paths: HermesPaths) -> tuple[str, str | None, str]:
    """返回 (块前内容, 托管块或 None, 块后内容)。"""
    path = _soul_path(paths)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        re.escape(SOUL_BEGIN) + r"\n.*?\n" + re.escape(SOUL_END) + r"\n?",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return text, None, ""
    return text[: m.start()], m.group(0), text[m.end():]


def write_soul_block(paths: HermesPaths, block: str) -> bool:
    """幂等写入托管块；返回是否落盘。用户自有内容永不改动。"""
    before, _, after = read_soul_sections(paths)
    new_text = before + block + "\n" + after
    path = _soul_path(paths)
    if path.exists() and path.read_text(encoding="utf-8") == new_text:
        return False
    _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".SOUL.md.tmp-{time.time_ns()}")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return True


def strip_soul_block(paths: HermesPaths) -> bool:
    before, managed, after = read_soul_sections(paths)
    if managed is None:
        return False
    path = _soul_path(paths)
    _backup(path)
    new_text = (before + after).strip() + ("\n" if (before + after).strip() else "")
    path.write_text(new_text, encoding="utf-8")
    return True


def _backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
    backups = sorted(path.parent.glob(f"{path.name}.bak-*"))
    for stale in backups[:-10]:
        stale.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 技能安装
# ---------------------------------------------------------------------------

def skills_dir(paths: HermesPaths) -> Path:
    return paths.home / "skills"


def skills_status(paths: HermesPaths) -> dict[str, bool]:
    base = skills_dir(paths)
    out: dict[str, bool] = {}
    for s in SKILLS:
        doc = base / s.slug / "SKILL.md"
        out[s.slug] = doc.exists() and SKILL_MARKER in doc.read_text(encoding="utf-8")
    return out


def install_skills(paths: HermesPaths, enabled: tuple[str, ...]) -> list[str]:
    installed: list[str] = []
    for s in SKILLS:
        if s.slug not in enabled:
            continue
        target = skills_dir(paths) / s.slug / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        _backup(target)
        target.write_text(_skill_doc(s.name, s.description, s.body), encoding="utf-8")
        installed.append(s.slug)
    return installed


def remove_skills(paths: HermesPaths) -> list[str]:
    removed: list[str] = []
    for s in SKILLS:
        target = skills_dir(paths) / s.slug
        doc = target / "SKILL.md"
        if target.exists() and doc.exists() and SKILL_MARKER in doc.read_text(encoding="utf-8"):
            shutil.rmtree(target)
            removed.append(s.slug)
    return removed


# ---------------------------------------------------------------------------
# 应用 / 移除 / 工作区初始化 / 状态
# ---------------------------------------------------------------------------

def apply(s: EngSettings, paths: HermesPaths | None = None) -> list[str]:
    paths = paths or detect()
    save_settings(s)
    changes: list[str] = []
    if write_soul_block(paths, compose_block(s)):
        changes.append("SOUL.md 工程纪律块已写入（用户自有内容未改动）")
    else:
        changes.append("SOUL.md 已是最新，无变更")
    installed = install_skills(paths, s.skills_enabled)
    if installed:
        changes.append("技能已安装：" + "、".join(installed))
    config = load_config(paths)
    agent_node = ensure_path(config, "agent")
    if s.inject_coding_instructions:
        compact = (compose_block(s).split("### 铁律", 1)[-1]
                   .split("### 需求消化流程", 1)[0]).strip()
        agent_node["coding_instructions"] = compact
        changes.append("agent.coding_instructions 已注入（编程任务强制遵循铁律）")
    else:
        if "coding_instructions" in agent_node:
            agent_node.pop("coding_instructions")
            changes.append("已移除 agent.coding_instructions")
    save_config(config, paths)
    return changes


def remove(keep_skills: bool = False, paths: HermesPaths | None = None) -> list[str]:
    paths = paths or detect()
    changes: list[str] = []
    if strip_soul_block(paths):
        changes.append("SOUL.md 托管块已移除")
    config = load_config(paths)
    agent_node = get_path(config, "agent", None)
    if isinstance(agent_node, dict) and "coding_instructions" in agent_node:
        agent_node.pop("coding_instructions")
        changes.append("已移除 agent.coding_instructions")
        save_config(config, paths)
    if not keep_skills:
        removed = remove_skills(paths)
        if removed:
            changes.append("技能已卸载：" + "、".join(removed))
    return changes


def init_workspace(s: EngSettings, paths: HermesPaths | None = None) -> list[str]:
    from app.hermes.workspace_service import validate_root  # 同为 S2 校验，避免模块级循环

    validate_root(s.workspace or DEFAULT_WORKSPACE)
    paths = paths or detect()
    base = Path(s.workspace or DEFAULT_WORKSPACE).expanduser()
    created: list[str] = []
    dirs = [
        base / "projects",
        base / "downloads",
        base / "scratch",
        base / "archive",
    ]
    for d in dirs:
        if not d.exists():
            d.mkdir(parents=True)
            created.append(str(d.relative_to(base)))
    readme = base / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Hermes 工作区\n\n由 Hermes Console 按工程规范初始化。\n\n"
            "- `projects/<项目名>/`：所有产出归属项目，动手前先建项目\n"
            "- `downloads/`：外部下载材料\n- `scratch/`：一次性实验\n"
            "- `archive/`：完结项目（追加日期后缀）\n",
            encoding="utf-8")
        created.append("README.md")
    # 项目模板文件落到一个示例项目（仅首次）
    sample = base / "projects" / "example-project" / "docs"
    if not sample.exists():
        sample.mkdir(parents=True)
        (sample / "requirements.md").write_text(DEFAULT_REQUIREMENT_TEMPLATE, encoding="utf-8")
        (sample / "plan.md").write_text("# 计划：<项目名>\n\n1. \n2. \n", encoding="utf-8")
        created.append("projects/example-project/docs/*")
    return created


@dataclass
class EngStatus:
    soul_managed: bool = False
    soul_chars: int = 0            # SOUL.md 总字符
    user_soul_chars: int = 0       # 排除托管块后的用户内容字符
    skills: dict = field(default_factory=dict)
    workspace_exists: bool = False
    workspace: str = DEFAULT_WORKSPACE
    coding_instructions_set: bool = False


def get_status(paths: HermesPaths | None = None) -> EngStatus:
    paths = paths or detect()
    s = load_settings()
    before, managed, after = read_soul_sections(paths)
    soul_path = _soul_path(paths)
    total = soul_path.stat().st_size if soul_path.exists() else 0
    st = EngStatus(
        soul_managed=managed is not None,
        soul_chars=total,
        user_soul_chars=len((before + after).encode("utf-8")),
        skills=skills_status(paths),
        workspace_exists=Path(s.workspace or DEFAULT_WORKSPACE).expanduser().exists(),
        workspace=s.workspace,
    )
    config = load_config(paths)
    st.coding_instructions_set = "coding_instructions" in (get_path(config, "agent", {}) or {})
    return st
