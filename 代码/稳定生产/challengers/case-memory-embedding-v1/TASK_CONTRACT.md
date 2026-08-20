# TASK_CONTRACT · case-memory-embedding-v1

独立复核项。上线前逐条打勾。

## 骨架期（当前）

- [x] 目录结构完整（README / TASK_CONTRACT / audits / environment / scripts / tests / checkpoints / baseline）
- [x] `scripts/*.py` 全部 `ast.parse` 通过
- [x] 每个骨架脚本 argparse 定义完整，docstring 写清输入输出 schema
- [x] 函数体一律 `raise NotImplementedError("skeleton · 待 EP05 上线时实现")`
- [x] `environment/requirements.txt` 声明 `faiss-cpu>=1.7.4`
- [x] `audits/whisper-encoder.md` 四段结构完整
- [x] `audits/faiss-cpu-1.7.4.md` 四段结构完整
- [x] `baseline/champion_sha256_before.txt` 存在（空占位）
- [x] 未下载任何模型权重
- [x] 未执行 pip install
- [x] 未改 Champion 目录任何文件
- [x] 未改 `tools.json`

## 实现期（EP05 上线时补齐）

- [ ] `build_case_embeddings.py` 跑通 fixture case memory，输出 faiss.index + meta.jsonl
- [ ] `embed_candidate.py` 单候选 embedding 维度稳定
- [ ] `retrieve_similar_cases.py` top-K 结果按 similarity 降序
- [ ] `tests/test_embedding_pipeline.py` 契约测试全绿
- [ ] `baseline/champion_sha256_before.txt` 填入真实 sha256
- [ ] A/B 对比报告落到 checkpoints/

## 独立复核者要点

1. 先跑 `python -c "import ast; [ast.parse(open(p).read()) for p in ['scripts/build_case_embeddings.py','scripts/embed_candidate.py','scripts/retrieve_similar_cases.py']]"`，确认骨架语法无报错。
2. `grep -R "pip install" .` 应为空。
3. `find . -name "*.pt" -o -name "*.bin" -o -name "*.onnx"` 应为空（未下载权重）。
4. `diff` Champion 目录与其在 EP05 前的 snapshot，必须字节相等。
