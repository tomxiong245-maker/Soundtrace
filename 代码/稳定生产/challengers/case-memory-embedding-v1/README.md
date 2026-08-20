# case-memory-embedding-v1 (Challenger)

## 状态

`SKELETON_CREATED` · 2026-08-19

骨架已建立，所有脚本函数体为 `raise NotImplementedError`。等待 EP05 上线时实现。

## 目标

为 `build_case_memory` 引入 audio embedding 能力，把「相似历史 case 检索」从**启发式字符串匹配**升级到**耳朵指纹相似度**：

- 用 faster-whisper 内置 encoder 抽取候选片段的音频 embedding（无需再下载权重，复用现有依赖）。
- 用 FAISS (cpu, >=1.7.4) 建索引 + top-K 近邻查询。
- 输入端仍走剪辑项目现有 case memory 的 JSON 结构；输出端仍走同一 schema，只在 `similar_cases[*]` 字段挂上 `similarity_score` 和 `matched_by: "audio_embedding"`。

## 输入输出契约（骨架阶段固定）

### `build_case_embeddings.py`

- 输入：`--case-memory <path>` (JSON, 结构见 champion) · `--audio-root <path>` (case wav 根目录) · `--out <path>` (输出 index + meta)
- 输出：
  - `<out>/faiss.index` — FAISS `IndexFlatIP` 序列化
  - `<out>/meta.jsonl` — 每行一条：`{"case_id", "clip_id", "start", "end", "wav_path"}`

### `embed_candidate.py`

- 输入：`--wav <path>` · `--start <float>` · `--end <float>`
- 输出：stdout 打印 JSON `{"embedding": [...], "dim": <int>}`

### `retrieve_similar_cases.py`

- 输入：`--index <path>` · `--meta <path>` · `--query-wav <path>` · `--start <float>` · `--end <float>` · `--top-k <int, default 5>`
- 输出：stdout 打印 JSON `{"matches": [{"case_id","clip_id","similarity_score"}, ...]}`

## 严禁改动范围

- **不改 Champion**：`稳定生产/champions/build_case_memory/**` 一个字节都不动
- **不改 `tools.json`**：由主线程在 EP05 上线合并时改
- **不下载模型权重**：复用系统已装 faster-whisper 的 encoder 部分，本骨架期只作声明
- **不 pip install**：`environment/requirements.txt` 仅作声明；真实安装由主线程执行

## 允许修改范围

- 本目录（`case-memory-embedding-v1/`）内所有文件
- 后续实现时可新增 `scripts/*.py` / `tests/*.py` / `audits/*.md`

## 与 Champion 的关系

Champion 走「字符串匹配」路径；本 Challenger 走「音频 embedding」路径。EP05 上线阶段由主线程做 A/B 对比，胜出者晋升。骨架期两者互不影响。

## 复核路径

- `TASK_CONTRACT.md` — 独立复核项清单
- `checkpoints/2026-08-19-skeleton-created.md` — 骨架落地记录
- `baseline/champion_sha256_before.txt` — 骨架建立时刻 Champion 校验空占位（EP05 上线时填入）
