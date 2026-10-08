# MobileWork 日志收集 Skill

本仓库提供 `mobilework-log-collection` Skill。完成 MobileWork 测试后，可以让支持本地 Skill 的 Agent 先核对测试题与项目，再从本地会话数据库收集运行证据。Skill 不给任务打分，也不复制题目、rubric 或交付文件正文。

## 安装与前置条件

1. 下载或克隆整个仓库，保留 `mobilework-log-collection/` 下的 `SKILL.md`、`scripts/`、`references/` 和 `agents/`。不要只复制 `SKILL.md`。
2. 将该文件夹放入所用 Agent 工具的 Skill 目录，或按该工具文档导入本地 Skill。Codex 的用户级目录通常是 `~/.codex/skills/`。WorkBuddy 等其他工具请依照其当前版本的安装说明；确认其能读取 `SKILL.md` 并运行本地 Python。
3. 准备 Python 3.10+、可读的 MobileWork SQLite 会话数据库、测试数据集目录和可写的新输出目录。多数据集模式要求每个待收集题目目录有 `问题描述.txt` 和 `rubric.md`。脚本仅用 Python 标准库。数据库常见于用户目录下 `.mobilework/xdg/data/opencode/opencode.db`，实际位置请先检查。
4. 给待收集项目使用可核对的根会话标题，例如 `W1-236b-0928`；独立第二次运行可用 `W1-236b-0928-2`。同一会话内输入 `继续` 不构成第二次运行。

日志可能包含助手文本、工具参数和结果，可能涉及敏感信息。取得使用授权后在可信环境运行，公开分享前自行脱敏。不要将真实数据库、配置或收集结果提交到本仓库。

## 使用流程

告诉 Agent 测试数据集根目录、需收集的数据集和维度、项目标题规则或明确映射、数据库位置、预期题数及拟用的输出目录。MAF 日志位置可选；不确定的信息先检查，不要猜测。可以直接发送：

> 请使用 mobilework-log-collection Skill 收集这批 MobileWork 测试日志。先只检查测试题目录、项目标题和全部运行次数的对应关系，列出缺失或歧义，等我确认后再收集。保持数据集、维度和题目原文件夹名称，独立运行分别放在第1次、第2次目录。完成后核验并告诉我完整输出路径、题数、运行次数及异常。数据库位置、数据集目录、命名规则和输出目录如下：……

Agent 应先展示只读检查结果；确认后才收集。重复标题、缺失项目或题数不符时，应先核对。脚本不会覆盖非空输出目录，核验失败不能报告完成。

## 命令行

多数据集模式使用 [`collect_multi.py`](mobilework-log-collection/scripts/collect_multi.py)。参考 [`config.example.json`](mobilework-log-collection/config.example.json) 建立本地配置，替换全部示例路径、目录名、维度名、标题后缀和题数。`dataset` 与 `versions` 是兼容旧提取器的字段；`dataset` 填实际存在的目录，`versions` 填非空列表。

```text
python mobilework-log-collection/scripts/inspect_schema.py --database <数据库路径>
python mobilework-log-collection/scripts/collect_multi.py --config <本地配置.json> --check
python mobilework-log-collection/scripts/collect_multi.py --config <本地配置.json> --collect
python mobilework-log-collection/scripts/collect_multi.py --config <本地配置.json> --verify
```

若有本地 MAF 日志并希望建立审查索引，在核验后另运行 `python mobilework-log-collection/scripts/maf_index.py --config <本地配置.json> --log <maf-engine.log> --log <maf.log>`。它不保存预览正文；只把输入片段与数据库输入在 120 秒内唯一匹配的记录关联到某次运行，其余记录标为未关联。详见 [`maf-index.md`](mobilework-log-collection/references/maf-index.md)。

旧版单数据集、标题编号与题目编号直接相同的布局仍可按 [`configuration.md`](mobilework-log-collection/references/configuration.md) 使用 `discover.py`、`collect.py`、`validate.py`、`verify_source.py`。两种布局不可混用。映射细节见 [`multi-dataset-layout.md`](mobilework-log-collection/references/multi-dataset-layout.md)。

## 预期文件

```text
指定输出目录/
├─ 数据集原文件夹名/
│  ├─ manifest.json              数据集与全部题目索引
│  ├─ metrics.csv                每次运行的客观统计
│  └─ 维度原文件夹名/
│     └─ 题目原文件夹名/
│        ├─ 任务会话索引.json     对应会话与运行次数
│        ├─ 第1次/
│        │  ├─ summary.md        人工阅读摘要
│        │  ├─ run.json          指标与来源元数据
│        │  ├─ trace.jsonl       事件索引
│        │  └─ evidence/         会话、消息、工具及文件元数据
│        └─ 第2次/                仅有独立第二次运行时生成
├─ metrics字段说明.md
└─ MAF审查索引.jsonl             可选，MAF 事件及审慎关联结果
```

保存内容是本地数据库可观察到的证据，不等于完整服务器端推理或网关审计记录。可选的 MAF 日志需要单独核对；缺少唯一会话关联时，不可仅凭时间邻近断言归属。完成后 Agent 应报告绝对输出路径，便于检查和打包。
