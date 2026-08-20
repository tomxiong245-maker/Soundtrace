# Checkpoint · 2026-08-19 · SKELETON_CREATED

## 事件

在 `challengers/case-memory-embedding-v1/` 下建立骨架，用于给 `build_case_memory` 加 audio embedding，替换现有启发式字符串匹配的历史 case 检索路径。

## 落地清单

- `README.md` · 状态 / 目标 / 输入输出契约 / 允改与禁改范围
- `TASK_CONTRACT.md` · 独立复核项 (骨架期 + 实现期)
- `audits/whisper-encoder.md` · faster-whisper 内置 encoder 审计 (四段：固定信息/使用范围/已知限制/状态引语)
- `audits/faiss-cpu-1.7.4.md` · faiss-cpu 检索库审计 (四段)
- `environment/requirements.txt` · 声明 `faiss-cpu>=1.7.4`
- `scripts/build_case_embeddings.py` · 骨架，argparse 契约固定
- `scripts/embed_candidate.py` · 骨架，argparse 契约固定
- `scripts/retrieve_similar_cases.py` · 骨架，argparse 契约固定
- `tests/test_embedding_pipeline.py` · 契约测试 (ast.parse / argparse required / main raises)
- `tests/fixtures/.gitkeep` · 空占位
- `baseline/champion_sha256_before.txt` · 空占位 (EP05 上线时回填)

## 边界

- 未下载任何模型权重
- 未执行 pip install
- 未修改 Champion 目录任何文件
- 未修改 `tools.json` (留给主线程)

## 骨架期验证

- `python -m py_compile scripts/*.py` 全部通过
- `python -m unittest discover tests` 骨架测试全绿

## 下一步 (EP05 上线阶段, 非本次)

1. 主线程执行 `pip install -r environment/requirements.txt`
2. 实现三个脚本函数体
3. 用真实 case memory 建 index → A/B 对比 Champion → 出胜负报告
4. 回填 `baseline/champion_sha256_before.txt` 与 `TASK_CONTRACT.md` 实现期勾选
