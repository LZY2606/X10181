# 刀轨审阅台

离线 G-code 语义比较工具。导入原始与修改版程序后，系统先逐行保留原文和 UTF-8 字节位置，再通过模态解释器生成机床坐标系运动，比较格式、等价模态、等价圆弧、真实模态、真实运动和未知厂商指令。

## 准备与演示

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 5241
```

打开 http://127.0.0.1:5241 ，页面标题为“刀轨审阅台”。

## 功能

- 逐行保存原文、换行符、UTF-8 字节范围、注释和词法范围。
- 解释 G20/G21、G90/G91、G17/G18/G19、G54–G59、G10 L2、G52、G40–G42、G43/G44/G49、进给、主轴、冷却和 G80–G89 固定循环。
- 圆弧几何由状态机采样生成，支持 R 与 IJK 写法以及 G17/G18/G19 平面。
- 差异分类包括格式变化、等价模态重申、等价几何、真实模态变化、真实运动变化和未知指令。
- 对快速移动穿过毛坯安全区、单位切换不一致、补偿未恢复、固定循环未取消和未知指令做安全阻断。
- 人工确认厂商指令时记录语义版本、适用机型、确认人、语义效果和备注。
- 导出 unified diff 或字节偏移补丁；补丁从原始文本应用，未改字节不重写。

## API

- `POST /api/review`：解释和比较两份程序。
- `POST /api/export/diff`：导出 unified diff。
- `POST /api/export/patch`：导出显式字节偏移补丁并返回回读校验。
- `POST /api/vendor-confirmations`：新增或更新厂商指令人工语义。
- `GET /api/vendor-confirmations`：查看人工确认记录。
