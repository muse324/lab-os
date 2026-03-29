# lab-os

## project決定の流れ

1. quick_add / import / sync
2. resolve_project_id_from_text（唯一の推定ロジック）
3. resolve_project_id（DB整合）

### 効果

- 判定ロジックが1箇所に集約
- 調整は resolve_project_id_from_text だけでOK
- 精度チューニングが非常にやりやすい状態
