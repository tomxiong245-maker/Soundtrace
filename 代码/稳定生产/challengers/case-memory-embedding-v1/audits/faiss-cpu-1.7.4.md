# Audit · faiss-cpu >= 1.7.4

## 固定信息

- 包名：`faiss-cpu`（Meta AI，向量近邻检索库）
- 目标版本：`>=1.7.4`（在 1.7.4 以后 wheel 覆盖 macOS arm64 完整，无需从源码编译）
- License：MIT
- 平台：CPU-only 版本，纯 wheel 安装；不需要 CUDA / MKL 特殊配置
- 关键类型：`IndexFlatIP`（内积精确检索）· `IndexFlatL2`（L2 距离）

## 使用范围（本 Challenger 内）

- 索引类型固定为 `IndexFlatIP`（配合 L2 归一化 embedding，内积 = 余弦相似度）
- 单机、单进程、纯 CPU；case memory 数量级预期 O(1e3~1e4)，`IndexFlatIP` 已足够
- 序列化走 `faiss.write_index(index, path)` / `faiss.read_index(path)`
- 不使用 GPU 版本、不使用 `IndexIVF*` / `IndexHNSW*`（骨架阶段不引入近似检索）

## 已知限制

- `IndexFlatIP` 是暴力检索，规模超过 1e5 后 latency 会明显（骨架期无需担心）
- FAISS 需要 float32 numpy contiguous 数组，接入前须 `np.ascontiguousarray(vec, dtype=np.float32)`
- macOS arm64 上 1.7.4 之前的版本 wheel 覆盖不全，1.7.4 起稳定
- 索引不带 metadata；`meta.jsonl` 需与索引 row order 严格对齐，任何 append/delete 要同步

## 状态引语

> **SKELETON**：本骨架期未安装 `faiss-cpu`，未创建任何索引。`environment/requirements.txt` 中的声明仅作实现期契约。EP05 实现时由主线程统一 `pip install`，并在此处补「实测」小节记录本机 `faiss.__version__`。
