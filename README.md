# 离线刀轨审阅台（G-code Review Bench）

面向车间工艺员的离线 G-code 语义对比工具。导入**原版**与**修改版**两个程序，
系统逐行保留原文与字节位置，解释机床模态状态，并由解释后的状态生成几何刀轨；
比较时区分纯格式变化、等价模态重申与真实运动变化，给出每个差异的最早状态分叉点
与全部受影响运动，并做数值安全检查。不连接真实机床。

## 准备

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 演示

```bash
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 5241
```

打开 <http://127.0.0.1:5241> 即可看到“刀轨审阅台”。首次启动会写入一个
“演示：法兰轮廓”项目（含进给变化、IJK↔R 等价圆弧与一处真实圆弧改动）。

## 能力

- **逐行保真导入**：每行保留原文、起止字节偏移、行终止符（支持 `\n` / `\r\n` / `\r`），
  注释与厂商片段原样保留。
- **模态解释**：单位 G20/G21、绝对/增量 G90/G91、工作坐标系 G54–G59.3 与 G10 L2/G92、
  刀具半径补偿 G40/G41/G42、刀长补偿 G43/G49、进给 G93/G94、主轴 M3/M4/M5、冷却 M7/M8/M9、
  固定循环 G80–G89（G98/G99 回退、G82 暂停、G83 啄钻）。
- **几何预览**：三平面 G17/G18/G19 圆弧，IJK 与 R（含 R 负大弧）写法按弧长重采样做
  几何等价；螺旋线性插值；固定循环展开为定位/进刀/啄钻/回退折线。快速移动虚线。
- **语义比较**：difflib 在原文行上对齐，差异判定走解释器：
  `format`（空格/注释/前导零）、`equivalent`（等价模态重申/圆弧写法）、`changed`（真实变化）、
  `unknown`（未识别厂商指令）、`added`/`removed`。每个分叉给出最早行、受影响运动集合，
  以及刀路重新汇合的位置；G91 等上游改动会传播到后续长段。
- **安全检查（数值断言，非文本猜测）**：
  - `rapid_through_stock`：G0/循环快速段用 slab 法求与毛坯安全 AABB 的进入点；
  - `unit_not_restored` / `unit_change_in_diff`：单位切换遗漏、两版单位不一致；
  - `cutter_comp_left_on` / `tool_length_left_on` / `cycle_not_cancelled`：模态未恢复；
  - 未识别厂商指令一律判 `unsafe`，阻止“安全无变化”结论。
- **人工语义确认**：对每条未知指令登记确认，**必须**包含 G 代码版本/方言与适用机型，
  以及确认人与说明，存入 SQLite 并显示在原行上。
- **逐行联动**：点文本行高亮刀轨、点刀轨定位文本行；原版/修改版/差异层三视图；
  展开任意运动查看执行前后的完整模态快照（工件坐标与机床坐标）。
- **导出**：所有导出 = 原始字节 + 明确补丁（difflib hunk 锚定字节区间，应用前强校验），
  未改行逐字节一致；补丁产物必须与修改版完全相同才允许成功。

## HTTP 接口

- `POST /api/projects` / `GET /api/projects`：建/列项目（base64 上传两版）
- `PUT /api/projects/{id}/versions/{a|b}`、`PUT /api/projects/{id}/settings`
- `GET  /api/projects/{id}/review`：对齐、解释、几何、分叉、安全的完整 JSON
- `GET/POST /api/projects/{id}/confirmations`：人工语义（版本+机型必填）
- `GET  /api/projects/{id}/patches`、`GET  /api/projects/{id}/export`

## 代码结构

```
gcode_review/
  lexer.py       逐行原文/字节位置/词元/厂商片段
  state.py       模态状态、G 码分组、工作坐标系
  geometry.py    三平面圆弧（IJK/R）、弧长重采样、slab-AABB 求交
  interpreter.py 逐行解释器：状态推进 + 运动折线 + 固定循环 + 未知项
  compare.py     原文对齐、行级分类、状态分叉段传播与汇合
  safety.py      数值安全检查与 verdict
  exporter.py    字节补丁构建/校验/应用
  db.py          SQLite（项目、版本字节、人工确认）
  service.py     审阅 JSON 装配
app.py           FastAPI 入口与静态页面
static/          单页前端（Canvas 刀轨 + 逐行表格 + 模态快照）
tests/           pytest：绝对/增量、三圆弧平面、WCS、补偿、固定循环、
                 未知指令、影响范围、导出字节、安全数值断言、API
```

数据默认存于工作目录 `review.db`（可用环境变量 `REVIEW_DB` 覆盖）。
