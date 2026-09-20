const state = { result: null, selected: null };
const $ = (id) => document.getElementById(id);
const sampleOriginal = `G21 G90 G54 G17
G0 X0 Y0 Z10
M3 S1200
G1 F120
G1 X20 Y0
G2 X40 Y20 R20
G1 X40 Y40
G0 Z20
M5
G80 G40 G49
M30
`;
const sampleModified = `(格式调整)
G21 G90 G54 G17 G90
G0  X0 Y0 Z10
M3 S1200
G1 F120
G1 X20 Y0
G2 X40 Y20 I20 J0
G1 X40 Y40
G91 G0 X0 Y0 Z10
G90
M5
G80 G40 G49
M30
`;

$("sample").onclick = () => {
  $("original").value = sampleOriginal;
  $("modified").value = sampleModified;
};

async function analyze() {
  const body = {
    original: $("original").value,
    modified: $("modified").value,
    machine_model: $("vendorMachine").value || "generic",
    stock_min: [-10, -10, -10],
    stock_max: [110, 70, 5],
  };
  $("status").textContent = "解释中…";
  const response = await fetch("/api/review", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  if (!response.ok) {
    $("status").textContent = "分析失败";
    return;
  }
  state.result = await response.json();
  render();
}

$("analyze").onclick = analyze;
$("layer").onchange = drawPreview;
$("plane").onchange = drawPreview;

function lineClass(side, lineNumber) {
  const comparison = state.result.comparison;
  for (const diff of comparison.differences) {
    const lines = side === "original" ? diff.original_lines : diff.modified_lines;
    if (lines.includes(lineNumber)) return diff.kind;
  }
  return "";
}

function renderLines(side) {
  const root = $(side === "original" ? "originalLines" : "modifiedLines");
  root.innerHTML = "";
  for (const line of state.result[side].lines) {
    const row = document.createElement("div");
    row.className = `line ${lineClass(side, line.line_number)} ${line.unknown?.length ? "unknown" : ""}`;
    row.dataset.side = side;
    row.dataset.line = line.line_number;
    const number = document.createElement("span");
    number.className = "lineno";
    number.textContent = line.line_number;
    const code = document.createElement("span");
    code.className = "code";
    code.textContent = line.raw || " ";
    row.append(number, code);
    row.onclick = () => selectLine(side, line.line_number);
    root.appendChild(row);
  }
}

function renderSummary() {
  const c = state.result.comparison;
  const safeClass = c.safe_no_change ? "safe-ok" : "safe-bad";
  $("summary").innerHTML = `
    <div class="${safeClass}">${c.safe_no_change ? "可判定：没有真实语义变化" : "不能判定为安全无变化"}</div>
    <div style="margin-top:8px">
      <span class="badge green">格式 ${c.summary.format || 0}</span>
      <span class="badge purple">等价模态 ${c.summary.equivalent_modal || 0}</span>
      <span class="badge purple">等价几何 ${c.summary.equivalent_geometry || 0}</span>
      <span class="badge amber">真实模态 ${c.summary.real_modal || 0}</span>
      <span class="badge red">真实运动 ${c.summary.real_motion || 0}</span>
      <span class="badge red">未知 ${c.summary.unknown || 0}</span>
    </div>`;
  $("findings").innerHTML = state.result.safety.length
    ? state.result.safety.map(f => `<div class="finding ${f.severity}"><b>${f.code}</b> · ${f.side} · ${f.message}</div>`).join("")
    : `<div class="muted">未发现配置范围内的安全风险。</div>`;
  $("status").textContent = c.safe_no_change ? "分析完成：语义等价" : "分析完成：存在语义/安全风险";
}

function render() {
  renderLines("original");
  renderLines("modified");
  renderSummary();
  drawPreview();
}

function pointFor(point) {
  const plane = $("plane").value;
  return plane === "XY" ? [point.X, point.Y] : plane === "XZ" ? [point.X, point.Z] : [point.Y, point.Z];
}

function drawPreview() {
  if (!state.result) return;
  const canvas = $("preview");
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * devicePixelRatio;
  canvas.height = rect.height * devicePixelRatio;
  const ctx = canvas.getContext("2d");
  ctx.scale(devicePixelRatio, devicePixelRatio);
  ctx.clearRect(0, 0, rect.width, rect.height);
  const layer = $("layer").value;
  const sides = layer === "original" ? ["original"] : layer === "modified" ? ["modified"] : ["original", "modified"];
  const allMotions = sides.flatMap(side => state.result[side].motions.map(m => ({...m, side})));
  const points = allMotions.flatMap(m => m.points.map(pointFor));
  if (!points.length) return;
  const minX = Math.min(...points.map(p => p[0]));
  const maxX = Math.max(...points.map(p => p[0]));
  const minY = Math.min(...points.map(p => p[1]));
  const maxY = Math.max(...points.map(p => p[1]));
  const scale = Math.min((rect.width - 50) / Math.max(1, maxX - minX), (rect.height - 50) / Math.max(1, maxY - minY));
  const project = (p) => {
    const [x, y] = pointFor(p);
    return [25 + (x - minX) * scale, rect.height - 25 - (y - minY) * scale];
  };
  ctx.lineWidth = 1.5;
  for (const motion of allMotions) {
    const selected = state.selected?.motion_id === motion.id && state.selected?.side === motion.side;
    ctx.strokeStyle = selected ? "#fff" : motion.side === "original" ? "#5aa9ff" : "#ff6b6b";
    if (layer === "difference") ctx.strokeStyle = motion.kind === "rapid" ? "#ff6b6b" : "#5aa9ff";
    if (lineClass(motion.side, motion.line_number) === "real_motion") ctx.strokeStyle = "#ff6b6b";
    if (lineClass(motion.side, motion.line_number) === "real_modal") ctx.strokeStyle = "#f2c14e";
    if (lineClass(motion.side, motion.line_number) === "format") ctx.strokeStyle = "#58d68d";
    ctx.beginPath();
    motion.points.forEach((point, i) => {
      const [x, y] = project(point);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }
  canvas._project = project;
  canvas._motions = allMotions;
}

$("preview").onclick = (event) => {
  const canvas = $("preview");
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  let best = null;
  let bestDistance = 8;
  for (const motion of canvas._motions || []) {
    for (const point of motion.points) {
      const [px, py] = canvas._project(point);
      const distance = Math.hypot(px - x, py - y);
      if (distance < bestDistance) { bestDistance = distance; best = motion; }
    }
  }
  if (best) selectLine(best.side, best.line_number, best.id);
};

function selectLine(side, lineNumber, motionId = null) {
  state.selected = {side, line: lineNumber, motion_id: motionId};
  document.querySelectorAll(".line").forEach(row => {
    row.classList.toggle("active", row.dataset.side === side && Number(row.dataset.line) === lineNumber);
  });
  const line = state.result[side].lines.find(item => item.line_number === lineNumber);
  const other = side === "original" ? "modified" : "original";
  const mapping = state.result.comparison.aligned
    .find(item => item[side] === lineNumber);
  const otherLine = mapping ? state.result[other].lines.find(item => item.line_number === mapping[other]) : null;
  $("selection").innerHTML = `
    <b>${side === "original" ? "原版" : "修改版"} 第 ${lineNumber} 行</b>
    <div class="muted">字节 ${line.start}–${line.end} · ${line.unknown.length ? "未知：" + line.unknown.join(", ") : "已解释"}</div>
    <pre>${line.raw}</pre>
    <div>前模态<pre>${JSON.stringify(line.state_before, null, 2)}</pre></div>
    <div>后模态<pre>${JSON.stringify(line.state_after, null, 2)}</pre></div>
    ${otherLine ? `<div>同步行：${other} 第 ${otherLine.line_number} 行</div>` : ""}
  `;
  drawPreview();
}

$("confirmVendor").onclick = async () => {
  const payload = {
    instruction: $("vendorInstruction").value,
    machine_model: $("vendorMachine").value,
    semantics_version: $("vendorVersion").value,
    effect: $("vendorEffect").value,
    confirmed_by: $("vendorUser").value,
    note: $("vendorNote").value,
  };
  const response = await fetch("/api/vendor-confirmations", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)
  });
  $("status").textContent = response.ok ? "厂商指令人工语义已保存，可重新分析" : "确认保存失败";
};

async function exportData(url) {
  const body = {original: $("original").value, modified: $("modified").value};
  const response = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  const text = await response.text();
  const blob = new Blob([text], {type: response.headers.get("content-type")});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = url.endsWith("diff") ? "review.diff" : "review.patch.json";
  link.click();
}
$("diffExport").onclick = () => exportData("/api/export/diff");
$("patchExport").onclick = () => exportData("/api/export/patch");
