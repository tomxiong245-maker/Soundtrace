# Claude Code 无头连接器（迁移自 Codex 临时工作区）

## 作用

这个小工具只负责把 Claude Code 作为一个可解析的本地子进程调用，方便不同 Agent 窗口共享同一份桌面项目。它不是音频剪辑器，也不替代 `delivery_orchestrator.py`。

默认工作目录应是仓库根目录：

`<PROJECT_ROOT>/`

它保留三种权限档案：

- `readonly`：只读检查；
- `none`：不开放工具；
- `full`：仅在负责人明确授权、且任务边界清楚时使用。

无头调用仍必须遵守项目的本地处理、原始音频只读、真人审核和 Challenger/Champion 边界。

## 用法

```bash
python3 'main/orchestrator/claude_headless/scripts/claude_headless.py' \
  --cwd '<HOME>/Desktop/minglue/剪辑项目' \
  status

python3 'main/orchestrator/claude_headless/scripts/claude_headless.py' \
  --cwd '<HOME>/Desktop/minglue/剪辑项目' \
  run --permission-profile readonly '只读检查当前项目进度，列出下一道完成门'
```

测试：

```bash
python3 -m unittest discover \
  -s 'main/orchestrator/claude_headless/tests' \
  -p 'test_claude_headless.py' -v
```

本目录不保存 Claude 会话、认证信息、真实音频或转写。
