# 朋友圈创作助手

在 Codex 中用 `$wechat-moments-assistant` 创作、改写和润色朋友圈。项目工作台用于保存草稿、切换版本、确认定稿、双份归档和管理配图。

## 本机启动

从克隆后的项目根目录运行 `python3 scripts/serve_workbench.py`，访问 `http://127.0.0.1:8765`。工作台使用 Python 3.10 及以上的标准库，图片校验使用 macOS 自带的 `sips`。写稿和生成图片由 Codex 执行。

如需同步定稿到 Obsidian，在项目根目录创建 `.moments.local/obsidian-dir`，写入一行目标目录的绝对路径。也可以在启动脚本时传 `--obsidian-dir`。草稿和项目副本默认保存在本项目的 `output/`。

可在 `references/persona.local.md` 保存自己的写作画像。在 Codex 中安装 `humanizer-zh` 后，Skill 会在编辑阶段调用它。

## 验证

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_workbench_ui.cjs
```

Python 测试使用临时目录和本机临时端口，Node 测试使用内置模块。
