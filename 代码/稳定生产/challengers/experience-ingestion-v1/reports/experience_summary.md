# Challenger 经验案例库 · 摘要

- 总案例数：27
- 按 episode：{'EP03': 11, 'EP04': 16}
- 按 reason_key：{'filler_hesitation': 6, 'immediate_repetition': 20, 'global_long_pause': 1}
- 按决定：{'reject': 15, 'accept': 12}
- 按 review_basis：{'text_only': 11, 'text_with_audio': 14, 'text_and_audio': 2}
- 有 EDL：10；无 EDL：17
- 音频证据完整率：100.00%（27/27）
- 已排除 bulk_accept：26；quarantine：0
- 审核人：['熊镇正']

## 按规则的人工接受率（不是模型 precision）

- `filler_hesitation`：总 6，accept 3，reject 3，adjust 0；人工接受率 50.00%；节目 ['EP03', 'EP04']
- `immediate_repetition`：总 20，accept 8，reject 12，adjust 0；人工接受率 40.00%；节目 ['EP03', 'EP04']
- `global_long_pause`：总 1，accept 1，reject 0，adjust 0；人工接受率 100.00%；节目 ['EP04']

## 口径

- 所有已导入案例的 `eligible_for_model_training=False`；仍处 `pending_review_mode`。
- 候选集合不是随机样本，不能宣称 precision/recall。
- 无候选区域的漏剪召回率未在本报告统计。
