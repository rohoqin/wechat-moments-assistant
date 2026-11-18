# 工作台与持久状态

## 项目内使用

在本项目目录新建 Codex 聊天，使用 `$wechat-moments-assistant` 或直接要求写朋友圈。项目入口位于 `.agents/skills/wechat-moments-assistant/SKILL.md`，链接到仓库根技能；`references`、`scripts` 等资源同样链接，避免维护两份规则。这个聊天用于开发，测试样稿不得写入真实 Obsidian。

可编辑页面使用 Python 3.10+ 标准库，无需安装生产依赖、配置 API Key：

```bash
python3 scripts/serve_workbench.py
```

打开 `http://127.0.0.1:8765`。支持草稿编辑、版本选择、明确点击定稿并双份归档、风格设置、图片预览与导入。页面自身不调用模型：写作与实际生图在 Codex 中执行；复制配图请求并粘贴到本项目创作聊天，助手完成生图与关联后，页面会自动刷新预览。复制请求不表示图片已生成。实际生图仍使用内置 `image_gen`。

## 保存与恢复

以下命令的脚本路径统一为仓库 `scripts/workflow.py`，不根据调用者当前目录猜测。默认工作数据是仓库 `output/workbench/<文案ID>/state.json`，默认归档位置为项目目录及 `.moments.local/obsidian-dir` 配置的 Obsidian 目录。`--repo-root`、`--obsidian-dir` 仅在隔离测试或用户明确更改位置时使用，且须置于子命令之前。

新写一条实际朋友圈时，把正文原样写入 UTF-8 输入文件，再执行：

```bash
python3 scripts/workflow.py create --title '主题' --text-file /private/tmp/moments-draft.txt --style auto
python3 scripts/workflow.py list
python3 scripts/workflow.py get --id 文案ID
python3 scripts/workflow.py update --id 文案ID --revision 当前修订号 --text-file /private/tmp/moments-draft.txt
```

每次交付实际草稿或改稿前保存状态，不把确认问题和分析混进正文。保存草稿不表示定稿，也不会写入 Obsidian。工具返回 `ok:true` 才算操作成功；保存失败需说明，不能声称新聊天可恢复未保存内容。

新聊天收到“接着上次”时先 `list`，再 `get` 读取目标的正文、所有版本、`active_version`、`confirmed_version`、`style`、归档与图片状态。用户提供 ID 或明确主题时按其选择；只有一条记录时可直接恢复；有多条且无法唯一确定时简短询问选哪条，不把最近更新时间当作用户选稿。普通新建请求从草稿开始，不擅自续改上条。页面启动默认显示最近保存记录，实际操作仍以明确选中稿为准。

`revision` 是并发修订号，`active_version` 是正文版本号，两者不能混用。更新、确认前先 `get` 取得最新修订号；冲突后重新读取并核对，不能直接用新修订号强行覆盖另一窗口的内容。选回旧版本使用 `update --active-version 版本号`，也会撤销当前确认。

## 定稿和风格

仅在用户明确确认当前正文后执行：

```bash
python3 scripts/workflow.py confirm --id 文案ID --revision 当前修订号 --confirmed
```

返回外层 `ok:true` 仅表示状态操作完成，还须检查 `data.archive.ok`、`repo_saved`、`obsidian_saved`。正文改动创建新版本并撤销确认；仅修改风格使用 `update --style '水彩'`，保留原文案确认但使不匹配的图片成为历史。不能把“水彩”当作确认指令。重复确认重试沿用首次确认日期并去重；明确指定归档日期才传 `--date YYYY-MM-DD`。

## 生图与关联

```bash
python3 scripts/workflow.py image-request --id 文案ID
```

该命令会验证已确认版本和两处实际归档正文，返回与文案 ID、版本、正文、风格绑定的 `request_id`、正文与提示词，并给出带绝对脚本路径及当前保存目录的 `cli_prefix`。从页面复制请求或使用隔离目录时，后续读取和关联必须保留同一 `cli_prefix`，不能退回默认目录。读取后使用内置生图工具生成并实际查看图片，把最终图片复制或保存到本地；将实际使用的提示词写入 UTF-8 文件，然后：

```bash
python3 scripts/workflow.py attach-image --id 文案ID --request-id 实际请求ID \
  --image /绝对路径/实际图片.png --prompt-file /private/tmp/moments-image-prompt.txt \
  --actual-style '水彩' --engine image_gen
```

结果会将图片和提示词保存到该篇原归档目录，并绑定对应版本。提示词中的自动风格须落实为实际风格记录。生图期间稿件或风格有变化时，关联会拒绝旧请求；保留旧图供查看，重新读取当前请求再执行，不给旧图强换请求 ID。失败用 `image-failed --id 文案ID --request-id 实际请求ID --error '实际错误'` 留下续做信息。生图失败不撤销已确认文案，也不重复写月文件。

工具只返回内联图且无法取到文件时，展示真实输出并说明尚未关联，不虚构本地成功。`get` 的 `current_image` 指向当前确认版本及风格的现存图片；`images` 保留历史记录。跨天重画不改变原归档日期。页面的“导入配图”仅关联用户选定的真实图片，不冒充模型生成结果。
