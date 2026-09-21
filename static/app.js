"use strict";
const $ = (s) => document.querySelector(s);
const state = {
  review: null, projectId: null, side: "a", layer: "diff",
  selectedLine: null, hit: [], view: null, files: { a: null, b: null },
  confirmations: [],
};

// ---------- API ----------
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}
const b64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));

async function loadProjects(selectId) {
  const list = await api("/api/projects");
  const sel = $("#project");
  sel.innerHTML = "";
  list.forEach((p) => {
    const o = document.createElement("option");
    o.value = p.id; o.textContent = `#${p.id} ${p.name}`;
    sel.appendChild(o);
  });
  if (list.length) { sel.value = list[0].id; await selectProject(list[0].id); }
}

async function selectProject(pid) {
  state.projectId = pid;
  try {
    state.review = await api(`/api/projects/${pid}/review`);
    state.confirmations = await api(`/api/projects/${pid}/confirmations`);
  } catch (e) {
    state.review = null;
  }
  const params = new URLSearchParams(location.search);
  const side = params.get("side");
  if (side === "a" || side === "b") state.side = side;
  syncSideTab();
  const line = params.get("line");
  state.selectedLine = line !== null && /^\d+$/.test(line) ? Number(line) : null;
  renderAll();
  if (state.selectedLine !== null) {
    const tr = document.querySelector(`#codeTable tr[data-line="${state.selectedLine}"]`);
    if (tr) tr.scrollIntoView({ block: "center" });
  }
}

// ---------- 渲染：代码表 ----------
function entryMap(side) {
  const m = new Map();
  state.review.entries.forEach((e) => {
    const key = side === "a" ? e.a_line : e.b_line;
    if (key !== null && key !== undefined) m.set(key, e);
  });
  return m;
}

function renderCode() {
  const tb = $("#codeTable tbody");
  tb.innerHTML = "";
  if (!state.review) {
    tb.innerHTML = `<tr><td colspan=2 class="muted" style="padding:14px">请导入两个版本或选择演示项目。</td></tr>`;
    return;
  }
  const side = state.side, data = state.review[side];
  const em = entryMap(side);
  const affected = new Set();
  state.review.divergences.forEach((d) => {
    const start = side === "a" ? d.start_a : d.start_b;
    const end = side === "a" ? d.end_a : d.end_b;
    if (start === null || start === undefined) return;
    for (let i = start; i <= ((end === null || end === undefined) ? data.lines.length - 1 : end); i++)
      affected.add(i);
  });
  data.lines.forEach((ln) => {
    const e = em.get(ln.index);
    const tr = document.createElement("tr");
    tr.dataset.line = ln.index;
    if (state.selectedLine === ln.index) tr.className = "sel";
    else if (affected.has(ln.index) && (!e || e.kind !== "equal")) tr.className = "affected";
    const num = document.createElement("td");
    num.className = "num"; num.textContent = ln.index + 1;
    const code = document.createElement("td");
    let prefix = "";
    if (e && e.kind !== "equal") {
      prefix = `<span class="tag k-${e.kind}">${kindLabel(e)}${e.propagated ? "↳" : ""}</span>`;
    }
    code.innerHTML = `${prefix}<span>${escapeHtml(ln.text || " ")}</span>
      <span class="muted small"> [${ln.start}–${ln.end}]</span>`;
    tr.onclick = () => selectLine(ln.index);
    tr.append(num, code);
    tb.appendChild(tr);
  });
}

function kindLabel(e) {
  return { format: "格式", equivalent: "等价", changed: "变化", unknown: "未知",
    added: "新增", removed: "删除" }[e.kind] || e.kind;
}
function escapeHtml(s) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function selectLine(index) {
  state.selectedLine = index;
  renderCode();
  renderSnapshot();
  draw();
  const tr = document.querySelector(`#codeTable tr[data-line="${index}"]`);
  if (tr) tr.scrollIntoView({ block: "nearest" });
}

// ---------- 渲染：刀轨画布 ----------
const cv = $("#cv"), ctx = cv.getContext("2d");
function resize() {
  const r = cv.parentElement.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  cv.width = r.width * dpr; cv.height = r.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener("resize", () => { resize(); draw(); });

function motionSets() {
  const r = state.review;
  if (!r) return { lists: {}, box: null };
  let lists;
  if (state.layer === "a") lists = { a: r.a.motions };
  else if (state.layer === "b") lists = { b: r.b.motions };
  else lists = { a: r.a.motions, b: r.b.motions };
  return { lists, box: r.settings.stock_box };
}

function computeView() {
  const { lists } = motionSets();
  // 俯视投影：包围盒只取 X/Y（Z 不参与缩放，避免抬刀把刀路缩没）。
  let xmin = Infinity, xmax = -Infinity, ymin = Infinity, ymax = -Infinity;
  Object.values(lists).forEach((ms) => ms.forEach((m) => m.points.forEach((p) => {
    xmin = Math.min(xmin, p[0]); xmax = Math.max(xmax, p[0]);
    ymin = Math.min(ymin, p[1]); ymax = Math.max(ymax, p[1]);
  })));
  if (!isFinite(xmin)) { xmin = ymin = 0; xmax = ymax = 10; }
  if (xmax - xmin < 1e-6) { xmin -= 5; xmax += 5; }
  if (ymax - ymin < 1e-6) { ymin -= 5; ymax += 5; }
  const w = cv.clientWidth - 120, h = cv.clientHeight - 110;
  const scale = Math.min(w / (xmax - xmin), h / (ymax - ymin)) * 0.9;
  const cx = (xmin + xmax) / 2, cy = (ymin + ymax) / 2;
  return { scale, ox: cv.clientWidth / 2 - cx * scale, oy: cv.clientHeight / 2 + cy * scale };
}
const T = (x, y) => [state.view.ox + x * state.view.scale, state.view.oy - y * state.view.scale];

function isRapid(m) {
  return m.kind === "rapid" || m.kind === "retract" ||
    (m.detail && (m.detail.cycle_phase === "position_xy" || m.detail.cycle_phase === "to_r" ||
                  m.detail.cycle_phase === "peck_retract"));
}
function dangerSet() {
  const s = new Set();
  state.review.safety.findings.forEach((f) => {
    if (f.code === "rapid_through_stock") {
      const arr = f.version === "a" ? state.review.a.motions : state.review.b.motions;
      s.add(`${f.version}:${f.motion_id}`);
    }
  });
  return s;
}

function draw() {
  resize();
  const W = cv.clientWidth, H = cv.clientHeight;
  ctx.clearRect(0, 0, W, H);
  if (!state.review) return;
  state.view = computeView();
  const { lists, box } = motionSets();

  // 网格：按缩放选择 1/5/10/50/100 mm 步距，只画屏幕覆盖范围
  ctx.strokeStyle = "#1b242d"; ctx.lineWidth = 1;
  const step = [1, 5, 10, 25, 50, 100, 250, 500]
    .find((g) => g * state.view.scale >= 28) || 1000;
  const wx0 = (0 - state.view.ox) / state.view.scale;
  const wx1 = (cv.clientWidth - state.view.ox) / state.view.scale;
  const wy0 = -(cv.clientHeight - state.view.oy) / state.view.scale;
  const wy1 = state.view.oy / state.view.scale;
  const gx0 = Math.floor(wx0 / step) * step, gx1 = Math.ceil(wx1 / step) * step;
  const gy0 = Math.floor(wy0 / step) * step, gy1 = Math.ceil(wy1 / step) * step;
  ctx.beginPath();
  for (let gx = gx0; gx <= gx1; gx += step) {
    const [x1, y1] = T(gx, wy0), [x2, y2] = T(gx, wy1);
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
  }
  for (let gy = gy0; gy <= gy1; gy += step) {
    const [x1, y1] = T(wx0, gy), [x2, y2] = T(wx1, gy);
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
  }
  ctx.stroke();
  // 毛坯安全区（俯视，画 XY 投影矩形）
  if (box && state.layer !== "diff" ? true : box) {
    const [x1, y1] = T(box.xmin, box.ymin), [x2, y2] = T(box.xmax, box.ymax);
    ctx.fillStyle = "rgba(255,93,93,0.07)";
    ctx.strokeStyle = "rgba(255,93,93,0.55)";
    ctx.setLineDash([5, 4]);
    ctx.fillRect(x1, y2, x2 - x1, y1 - y2);
    ctx.strokeRect(x1, y2, x2 - x1, y1 - y2);
    ctx.setLineDash([]);
  }

  const dangers = dangerSet();
  const colors = { a: "#4fa3ff", b: "#ff9f43" };
  state.hit = [];
  Object.entries(lists).forEach(([side, motions]) => {
    motions.forEach((m) => {
      const key = `${side}:${m.id}`;
      let color = colors[side];
      if (state.layer === "diff") {
        const idsA = new Set(), idsB = new Set();
        state.review.divergences.forEach((d) => {
          d.a_motion_ids.forEach((x) => idsA.add(x));
          d.b_motion_ids.forEach((x) => idsB.add(x));
        });
        const inDiff = side === "a" ? idsA.has(m.id) : idsB.has(m.id);
        if (!inDiff) color = "#5c6b7a";
      }
      if (dangers.has(key)) color = "#ff5d5d";
      const selStep = state.review[side].steps[state.selectedLine];
      if (selStep && selStep.motion_ids.includes(m.id)) color = "#ffdd57";
      ctx.strokeStyle = color;
      ctx.lineWidth = isRapid(m) ? 1.2 : 2;
      ctx.setLineDash(isRapid(m) ? [6, 4] : []);
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.beginPath();
      m.points.forEach((p, i) => {
        const [x, y] = T(p[0], p[1]);
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke(); ctx.setLineDash([]);
      // 命中区：每条运动的屏幕包围盒
      const xs = m.points.map((p) => T(p[0], p[1])[0]);
      const ys = m.points.map((p) => T(p[0], p[1])[1]);
      state.hit.push({ side, id: m.id, line: m.line,
        bbox: [Math.min(...xs) - 4, Math.min(...ys) - 4,
               Math.max(...xs) + 4, Math.max(...ys) + 4] });
    });
  });
  drawAxes();
}

function drawAxes() {
  ctx.fillStyle = "#7d8fa0"; ctx.font = "11px monospace";
  const [ox, oy] = T(0, 0);
  ctx.strokeStyle = "#3a4a5a";
  ctx.beginPath(); ctx.moveTo(8, oy); ctx.lineTo(cv.clientWidth - 8, oy); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(ox, 8); ctx.lineTo(ox, cv.clientHeight - 8); ctx.stroke();
  ctx.fillText("X+", cv.clientWidth - 18, oy - 6);
  ctx.fillText("Y+", ox + 6, 16);
}

cv.addEventListener("click", (ev) => {
  if (!state.review) return;
  const rect = cv.getBoundingClientRect();
  const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
  for (const h of state.hit) {
    if (mx >= h.bbox[0] && mx <= h.bbox[2] && my >= h.bbox[1] && my <= h.bbox[3]) {
      state.side = h.side;
      document.querySelectorAll(".tabs")[0].querySelectorAll(".tab")
        .forEach((t) => t.classList.toggle("active", t.dataset.side === h.side));
      selectLine(h.line);
      return;
    }
  }
});

// ---------- 右侧面板 ----------
function renderFindings() {
  const el = $("#findings"), s = state.review.safety;
  const label = { safe: "安全 · 无变化", review: "需人工复核", unsafe: "不安全" };
  const v = $("#verdict");
  v.textContent = label[s.verdict];
  v.className = `badge v-${s.verdict}`;
  if (state.review.a.unknowns.length || state.review.b.unknowns.length) {
    v.textContent += "（含未知指令，禁止判定无变化）";
  }
  if (!s.findings.length) {
    el.innerHTML = `<span class="muted small">无数值违规。</span>`;
    return;
  }
  el.innerHTML = s.findings.map((f) => `
    <div class="finding f-${f.severity}">
      <b>${findingName(f.code)}</b>
      <span class="muted small"> [${f.severity === "danger" ? "危险" : "警告"} · ${f.version === "both" ? "两版" : f.version.toUpperCase()}${f.line !== null && f.line !== undefined ? " · 行" + (f.line + 1) : ""}]</span>
      <div class="small">${f.message}</div>
      ${f.values && Object.keys(f.values).length ? `<div class="small mono muted">${escapeHtml(JSON.stringify(f.values))}</div>` : ""}
    </div>`).join("");
}
function findingName(code) {
  return { rapid_through_stock: "快速穿过毛坯安全区", unit_not_restored: "单位切换未恢复",
    unit_change_in_diff: "两版单位不一致", cutter_comp_left_on: "半径补偿未取消",
    tool_length_left_on: "刀长补偿未取消", cycle_not_cancelled: "固定循环未取消",
    interp_error: "解释错误" }[code] || code;
}

function renderDivs() {
  const el = $("#divs");
  const ds = state.review.divergences;
  if (!ds.length) { el.innerHTML = `<span class="muted small">无分叉（格式与等价重申已分离）。</span>`; return; }
  el.innerHTML = ds.map((d) => {
    const line = (d.b_line ?? d.a_line) + 1;
    return `<div class="finding div-head" data-div="${d.id}">
      <b>分叉 #${d.id}</b> <span class="muted small">最早：行 ${line}</span><br>
      <span class="small">${d.reasons.map(escapeHtml).join(", ")}</span><br>
      <span class="small muted">受影响运动 A:[${d.a_motion_ids.join(",")}] B:[${d.b_motion_ids.join(",")}]</span>
    </div>`;
  }).join("");
  el.querySelectorAll(".div-head").forEach((h) => h.onclick = () => {
    const d = ds.find((x) => x.id == h.dataset.div);
    const key = d.b_line ?? d.a_line;
    state.side = d.b_line !== null && d.b_line !== undefined ? "b" : "a";
    syncSideTab(); selectLine(key);
  });
}

function syncSideTab() {
  document.querySelectorAll(".tabs")[0].querySelectorAll(".tab")
    .forEach((t) => t.classList.toggle("active", t.dataset.side === state.side));
}

function renderSnapshot() {
  const el = $("#snapshot");
  if (state.selectedLine === null) {
    el.innerHTML = `<span class="muted small">点选左侧行或刀轨运动，查看该行前后模态。</span>`;
    return;
  }
  const side = state.side, data = state.review[side];
  const st = data.steps[state.selectedLine];
  const mot = data.motions.filter((m) => st.motion_ids.includes(m.id));
  el.innerHTML = `
    <div class="small muted">${side.toUpperCase()} 行 ${state.selectedLine + 1}：${escapeHtml(data.lines[state.selectedLine].text)}</div>
    ${st.errors.length ? `<div class="finding f-danger small">${st.errors.map(escapeHtml).join("；")}</div>` : ""}
    <h3 style="margin-top:8px">执行前</h3>${kv(st.before)}
    <h3 style="margin-top:8px">执行后</h3>${kv(st.after)}
    <h3 style="margin-top:8px">本段运动（${mot.length}）</h3>
    ${mot.map((m) => `<div class="finding small">
      <b>#${m.id} ${m.kind}</b> · F${m.feed} · ${m.points.length} 点<br>
      <span class="mono muted">${escapeHtml(JSON.stringify(m.points[0]))} → ${escapeHtml(JSON.stringify(m.points[m.points.length - 1]))}</span>
    </div>`).join("") || `<span class="muted small">无运动</span>`}`;
}
function kv(snap) {
  const rows = [
    ["单位", snap.units === "mm" ? "G21 mm" : "G20 inch"],
    ["绝对/增量", snap.distance === "absolute" ? "G90 绝对" : "G91 增量"],
    ["平面", snap.plane], ["坐标系", snap.wcs],
    ["半径补偿", snap.cutter_comp], ["刀长补偿", snap.tool_length],
    ["固定循环", snap.cycle], ["循环回退", snap.cycle_retract],
    ["运动模态", snap.motion], ["进给", `${snap.feed} ${snap.feed_mode}`],
    ["主轴", `${snap.spindle} @${snap.spindle_speed}`], ["冷却", snap.coolant ? "M8" : "M9"],
    ["刀具", `T${snap.tool}`],
    ["工件坐标", `(${snap.work_pos.join(", ")})`],
    ["机床坐标", `(${snap.machine_pos.join(", ")})`],
  ];
  return `<div class="kv">${rows.map(([k, v]) =>
    `<span class="k">${k}</span><span class="mono">${escapeHtml(String(v))}</span>`).join("")}</div>`;
}

function renderUnknowns() {
  const el = $("#unknowns");
  const groups = new Map();
  ["a", "b"].forEach((side) => state.review[side].unknowns.forEach((u) => {
    const sig = `${u.kind}:${u.code}`;
    if (!groups.has(sig)) groups.set(sig, { ...u, side });
  }));
  if (!groups.size) { el.innerHTML = `<span class="muted small">无未识别指令。</span>`; return; }
  el.innerHTML = [...groups.values()].map((u) => {
    const sig = `${u.kind}:${u.code}`;
    const c = state.confirmations.find((x) => x.signature === sig);
    return `<div class="finding f-warning" data-sig="${escapeHtml(sig)}">
      <b>${escapeHtml(u.code)}</b> <span class="muted small">行${u.line + 1} · ${u.side.toUpperCase()}</span>
      <div class="small mono muted">${escapeHtml(u.text)}</div>
      ${c ? `<div class="small">已确认：${escapeHtml(c.semantics)}<br>
        <span class="muted">版本 ${escapeHtml(c.version)} · 机型 ${escapeHtml(c.machine_models.join("/"))} · ${escapeHtml(c.confirmed_by || "匿名")}</span></div>`
        : `<button class="confirmBtn" data-sig="${escapeHtml(sig)}" data-code="${escapeHtml(u.code)}" data-text="${escapeHtml(u.text)}">确认人工语义…</button>`}
    </div>`;
  }).join("");
  el.querySelectorAll(".confirmBtn").forEach((btn) => btn.onclick = () => confirmDialog(btn.dataset, el));
}

function confirmDialog(d, root) {
  const html = `
    <div style="display:flex;flex-direction:column;gap:6px;margin-top:8px">
      <input id="cfVer" placeholder="G 代码版本/方言（必填），如 Fanuc 31i-B" value="Fanuc 31i-B">
      <input id="cfMach" placeholder="适用机型（必填，逗号分隔），如 VMC850, DMU 50" value="VMC850">
      <textarea id="cfSem" rows="2" placeholder="人工语义说明（必填）"></textarea>
      <input id="cfBy" placeholder="确认人（可选）">
      <button id="cfSave">保存确认</button>
    </div>`;
  const box = root.querySelector(`[data-sig="${CSS.escape(d.sig)}"]`);
  box.insertAdjacentHTML("beforeend", html);
  box.querySelector("#cfSave").onclick = async () => {
    try {
      await api(`/api/projects/${state.projectId}/confirmations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          signature: d.sig, code: d.code, sample_text: d.text,
          version: box.querySelector("#cfVer").value,
          machine_models: box.querySelector("#cfMach").value.split(",").map((s) => s.trim()).filter(Boolean),
          semantics: box.querySelector("#cfSem").value,
          confirmed_by: box.querySelector("#cfBy").value,
        }),
      });
      state.confirmations = await api(`/api/projects/${state.projectId}/confirmations`);
      renderUnknowns();
    } catch (e) { alert(e.message); }
  };
}

function renderAll() {
  if (!state.review) {
    renderCode(); draw();
    $("#findings").innerHTML = `<span class="muted small">数据不足</span>`;
    $("#divs").innerHTML = ""; $("#snapshot").innerHTML = ""; $("#unknowns").innerHTML = "";
    $("#verdict").textContent = "—"; $("#verdict").className = "badge v-review";
    return;
  }
  renderCode(); draw(); renderFindings(); renderDivs(); renderSnapshot(); renderUnknowns();
}

// ---------- 交互绑定 ----------
document.querySelectorAll(".tabs")[0].querySelectorAll(".tab").forEach((t) =>
  t.onclick = () => {
    state.side = t.dataset.side;
    syncSideTab(); state.selectedLine = null; renderCode(); renderSnapshot(); draw();
  });
document.querySelectorAll(".layer").forEach((t) =>
  t.onclick = () => {
    document.querySelectorAll(".layer").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    state.layer = t.dataset.layer; draw();
  });
$("#project").onchange = (e) => selectProject(Number(e.target.value));

$("#newBtn").onclick = async () => {
  const name = prompt("项目名称", "刀轨对比 " + new Date().toLocaleString());
  if (!name) return;
  const r = await api("/api/projects", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  await loadProjects();
  $("#project").value = r.id;
  await selectProject(r.id);
};

["fileA", "fileB"].forEach((id) => $(`#${id}`).onchange = (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const side = id === "fileA" ? "a" : "b";
  f.arrayBuffer().then((buf) => { state.files[side] = { buf, name: f.name }; });
  $("#uploadBtn").disabled = !(state.files.a && state.files.b);
});

$("#uploadBtn").onclick = async () => {
  let pid = state.projectId;
  const proj = state.review ? null : pid;
  if (!state.review) {
    const r = await api("/api/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "导入项目 " + new Date().toLocaleString() }),
    });
    pid = r.id;
  }
  for (const side of ["a", "b"]) {
    const f = state.files[side];
    await api(`/api/projects/${pid}/versions/${side}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content_b64: b64(f.buf), filename: f.name }),
    });
  }
  state.files = { a: null, b: null };
  $("#fileA").value = $("#fileB").value = ""; $("#uploadBtn").disabled = true;
  await loadProjects();
  $("#project").value = pid;
  await selectProject(pid);
};

$("#exportBtn").onclick = async () => {
  if (!state.projectId) return alert("请先选择项目");
  try {
    const res = await fetch(`/api/projects/${state.projectId}/export`);
    if (!res.ok) throw new Error("导出失败");
    const text = await res.text();
    const n = res.headers.get("X-Patch-Count");
    const blob = new Blob([text], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "modified_exported.nc";
    a.click();
    alert(`已从原始字节应用 ${n} 条明确补丁导出，未改行字节一致。`);
  } catch (e) { alert(e.message); }
};

loadProjects();
