# 无候选区域随机抽查工具

`sample_no_candidate_windows.py` 为每一期已经冻结的 run 生成一份可复现的“无候选区域”人工试听计划。它解决的是**漏剪抽查**：如果系统没有在某一处提出候选，这个位置仍可能有口癖、长停顿、重说或其他该处理的问题；不能因为候选数很少，就假设没有漏检。

它不是自动剪辑工具，不产生 EDL、不写审核决定、不读取真实音频，也不证明整期没有漏剪。

## 输入与边界

工具只读取 run 根目录的三个 JSON：

- `run_identity.json`
- `input_manifest.json`
- `all_candidates.json`

它会验证 `run_id`、`episode_id`、`run_identity_sha256`、采样率、总 sample 数与逐轨对齐信息；逐个读取候选的 `[start_sample, end_sample)`，在前后各留出可配置的保护区，再从剩余位置抽取 8–20 个互不重叠的 20–30 秒同步时间窗。

它不会打开、解码、复制或重算任何 WAV/MP3，也不会沿着 `input_relpath` 访问媒体。输出只包含 JSON 与 Markdown 的时间线索引。

如果可用区域不足以抽满要求的窗口数，命令会 fail closed：不写输出目录，也不留下半成品。

## 使用方法

从项目根目录运行。种子必须显式指定；相同 run 哈希、参数和种子会得到相同的窗口。

```bash
python3 benchmark/editing-e2e-v1/sample_no_candidate_windows.py \
  --run-dir main/runs/EP04/EP04-v20-20260814-1617 \
  --output-dir benchmark/editing-e2e-v1/audits/EP04-v20-20260814-1617.no-candidate-audit-v1 \
  --seed EP04-v20-no-candidate-audit-v1 \
  --count 8 \
  --window-seconds 25 \
  --handle-seconds 5
```

参数：

- `--count`：8–20，默认 8；
- `--window-seconds`：20–30，默认 25；必须能精确换算为整数 sample；
- `--handle-seconds`：每个候选前后排除的范围，默认 5；同样必须能精确换算为整数 sample；
- `--output-dir`：必须是一个尚不存在的目录。工具先在同级临时目录写完 JSON+Markdown，再一次性发布该目录，避免半成品。

输出目录只会有：

- `no_candidate_windows.json`：机器可读、含 run/identity/input/all-candidates SHA、种子、参数、保护区与窗口；
- `no_candidate_windows.md`：给审核人的同步试听表格。

## 人工试听与结论边界

审核人必须在本地原 run 的试听环境中，对每个窗口的**所有对齐轨道**同步试听。每个窗口至少记录：`无明确问题`、`可能漏剪` 或 `明确漏剪/需要新候选`，并说明时间点和原因。

发现问题时，应把它变成一个新的受审核候选；不得直接改 EDL，更不能把这份抽样表当成自动删剪授权。

抽到的窗口都没有问题，只表示这 8–20 个随机样本没有发现明确漏剪，**不证明节目其余区域没有漏剪**。它还不能衡量候选 precision、语义正确性、剪口自然度或整片最终质量。减少 Mentor 审核量之前，仍需将这份抽查结果和已采用剪口的盲听、严重语义误删一起放进 development benchmark。

## 验证

```bash
python3 -m unittest discover \
  -s benchmark/editing-e2e-v1/tests \
  -p 'test_sample_no_candidate_windows.py' \
  -v
```

测试只构造 JSON 与不存在的假音频路径；它验证固定种子可复现、候选保护区与窗口互不重叠、空间不足时无输出、以及最终 bundle 只含 JSON/Markdown。
