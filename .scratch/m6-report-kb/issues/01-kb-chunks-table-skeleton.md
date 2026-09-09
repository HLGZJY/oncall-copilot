Status: resolved
Blocked by: —

# kb chunks table skeleton

## 任务

新建第九表 kb_chunks（D-55：id/incident_id FK 一对多/investigation_id FK nullable/section/seq/text/source_meta_json/hit_count/created_at/superseded_at）+ oncall.kb 包骨架（kb/__init__.py）；pyproject C3 禁列增补 oncall.kb（照 oncall.remediation 先例）；frozen-face 契约测试（六/八/九表冻结列键集合断言，M5 test_m5_frozen_face 同款形制——D-25/D-31/D-46 冻结列不动）

## 验收（可机械判定）

- [x] pytest 绿：kb_chunks create_all 幂等落库；frozen-face 断言既有表列集合不变；import-linter C3 含 oncall.kb 禁列
