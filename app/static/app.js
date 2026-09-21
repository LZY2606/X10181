"use strict";
let STATE = {data:null, selA:null, selB:null, hoverSeg:null,
  affA:new Set(), affB:new Set()};

const $ = id => document.getElementById(id);
const esc = s => (s==null?"":String(s)).replace(/[&<>]/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

const SAMPLE_A = [
"G21 G90 G54 G17",
"G0 X0 Y0 Z50",
"M3 S2400",
"G43 H1 Z5",
"G1 Z-2 F120",
"G1 X40 Y0 F300",
"G2 X60 Y20 I0 J20",
"G1 X60 Y50",
"G1 X0 Y50",
"G1 X0 Y0",
"G0 Z50",
"G40",
"M5",
"M30"].join("\n");
const SAMPLE_B = [
"G21 G90 G54 G17 ( face mill )",
"G0 X0 Y0 Z50",
"M03 S2400",
"G43 H01 Z5.0",
"G1 Z-2.0 F120",
"G1 X40. Y0 F300.",
"G2 X60. Y20. R20.0",
"G1 X60. Y50.",
"G1 X0 Y50",
"G1 X0 Y0",
"G0 Z50",
"M5",
"M30"].join("\n");
const SAMPLE_A2 = [
"G21 G90 G54",
"G0 X0 Y0 Z50",
"M98 P1000",
"G1 X30 F200",
"G0 Z50",
"M30"].join("\n");
const SAMPLE_B2 = [
"G21 G90 G54",
"G0 X0 Y0 Z50",
"M200 (vendor probe)",
"G1 X30 F200",
"G0 Z50",
"M30"].join("\n");

$("btnSample").onclick = () => {
  $("srcA").value = SAMPLE_A;
  $("srcB").value = SAMPLE_B;
  doCompare();
};

async function doCompare(){
  const body = {text_a:$("srcA").value, text_b:$("srcB").value,
    version:$("version").value, machine:$("machine").value,
    stock:{xmin:-5,xmax:65,ymin:-5,ymax:55,zmin:-100,z_clear:1}};
  const r = await fetch("/api/compare",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  if(!r.ok){ alert("比较失败: "+(await r.text())); return; }
  STATE.data = await r.json();
  renderAll();
}
$("btnCompare").onclick = doCompare;

function renderAll(){
  const d = STATE.data, c = d.comparison;
  STATE.affA = new Set(c.changed_lines_a);
  STATE.affB = new Set(c.changed_lines_b);
  const v = $("verdict");
  const map = {equivalent:["等价（仅格式/重申）","equivalent"],
    different:["存在真实运动变化","different"],
    blocked:["未知指令阻止安全结论","blocked"]};
  v.textContent = map[c.verdict][0]+" · "+JSON.stringify(c.counts);
  v.className = "pill "+map[c.verdict][1];
  renderSide("A"); renderSide("B"); renderForks(); renderSnap(); draw();
}

function alignRows(side){
  const d = STATE.data, c = d.comparison;
  const isA = side==="A";
  const rows = [];
  for(const item of c.alignment){
    const li = isA ? item.a : item.b;
    if(li===null || li===undefined) {
      rows.push({blank:true, kind:isA?"insert":"delete", item});
    } else {
      rows.push({blank:false, index:li, kind:item.kind, item});
    }
  }
  return rows;
}

function renderSide(side){
  const d = STATE.data;
  const model = side==="A"?d.model_a:d.model_b;
  const tbody = side==="A"?$("rowsA"):$("rowsB");
  const aff = side==="A"?STATE.affA:STATE.affB;
  const rows = alignRows(side);
  let html="";
  for(const r of rows){
    if(r.blank){
      html += `<tr class="${r.kind}"><td class="lno"></td><td class="code">—</td></tr>`;
      continue;
    }
    const ln = model.lines[r.index];
    const unk = (ln.unknowns||[]).filter(u=>!u.confirmed).length;
    const tag = r.kind!=="unchanged"
      ? `<span class="tag ${r.kind}">${({format:"格式",restate:"重申",
          changed:"变化",insert:"新增",delete:"删除"})[r.kind]}</span>`:"";
    const utag = unk ? ` <span class="tag delete">未知</span>`:"";
    const cls = [r.kind, aff.has(r.index)?"affected":"",
      (side==="A"?STATE.selA:STATE.selB)===r.index?"selected":"",
      unk?"unknown":""].join(" ");
    html += `<tr class="${cls}" data-side="${side}" data-line="${r.index}">
      <td class="lno">${r.index+1}</td>
      <td class="code">${esc(ln.raw)||" "}${tag}${utag}</td></tr>`;
  }
  tbody.innerHTML = html;
  tbody.querySelectorAll("tr").forEach(tr=>{
    tr.onclick = () => {
      const li = tr.dataset.line;
      if(li===undefined) return;
      selectLine(side, parseInt(li));
    };
  });
  $(side==="A"?"statA":"statB").textContent =
    `${model.segments.length} 段 · 未知 ${model.unknowns.length}`;
}

function pairOf(side, line){
  const al = STATE.data.comparison.alignment;
  for(const it of al){
    if(side==="A" && it.a===line) return it.b;
    if(side==="B" && it.b===line) return it.a;
  }
  return null;
}

function selectLine(side, line){
  if(side==="A"){STATE.selA=line; STATE.selB=pairOf("A",line);}
  else {STATE.selB=line; STATE.selA=pairOf("B",line);}
  renderSide("A"); renderSide("B"); renderSnap(); draw();
  scrollToLine("A",STATE.selA); scrollToLine("B",STATE.selB);
}
function scrollToLine(side,line){
  if(line==null) return;
  const tr = document.querySelector(
    `#rows${side} tr[data-line="${line}"]`);
  if(tr) tr.scrollIntoView({block:"nearest"});
}

function modalKv(s){
  if(!s) return "<i class='muted'>无对应行</i>";
  const p = s.position;
  const items = [
    ["单位",s.units],["坐标方式",s.positioning],["平面",s.plane],
    ["运动模态",s.motion],["刀补",s.cutter_comp],["长度补偿",s.length_comp],
    ["工件坐标系",s.wcs],["循环",s.cycle||"—"],["返回",s.retract_mode],
    ["进给",s.feed==null?"—":s.feed.toFixed(2)],
    ["主轴",`${s.spindle} ${s.spindle_speed??""}`],
    ["冷却",s.coolant?"开":"关"],
    ["X",p.x.toFixed(3)],["Y",p.y.toFixed(3)],["Z",p.z.toFixed(3)]];
  return `<div class="kv">${items.map(([k,v])=>
    `<div><b>${k}</b>${esc(v)}</div>`).join("")}</div>`;
}

function renderSnap(){
  const d=STATE.data;
  let html="";
  if(STATE.selA!=null || STATE.selB!=null){
    const la = STATE.selA!=null?d.model_a.lines[STATE.selA]:null;
    const lb = STATE.selB!=null?d.model_b.lines[STATE.selB]:null;
    html += `<div style="display:flex;gap:18px;flex-wrap:wrap">
      <div class="snapcol"><h3>A 第${la?la.index+1:"—"}行 模态快照（行前）</h3>
        ${la?modalKv(la.before):""}
        <h3 style="margin-top:6px">行后</h3>${la?modalKv(la.after):""}</div>
      <div class="snapcol b"><h3>B 第${lb?lb.index+1:"—"}行 模态快照（行前）</h3>
        ${lb?modalKv(lb.before):""}
        <h3 style="margin-top:6px">行后</h3>${lb?modalKv(lb.after):""}</div>
    </div>`;
    const unks = [...(la?la.unknowns:[]),...(lb?lb.unknowns:[])];
    for(const u of unks){
      html += `<div class="violation error" style="margin-top:6px">
        未知指令 <b>${esc(u.code)}</b>（${u.kind}）
        ${u.confirmed?`已确认: ${esc(u.effect)} / ${esc(u.note)}`:""}
        <button data-code='${esc(u.code)}' data-raw='${esc(u.raw_text)}'
          class="cfbtn">${u.confirmed?"更新人工确认":"确认人工语义"}</button></div>`;
    }
  } else {
    html += safetyHtml("A",d.safety_a)+safetyHtml("B",d.safety_b);
  }
  if(html==="") html = safetyHtml("A",d.safety_a)+safetyHtml("B",d.safety_b);
  $("snap").innerHTML = html;
  document.querySelectorAll(".cfbtn").forEach(b=>{
    b.onclick=()=>openModal(b.dataset.code,b.dataset.raw);
  });
}

function safetyHtml(side,rep){
  const vs = rep.violations.map(v=>
    `<div class="violation ${v.severity}">${side} · 第${(v.line_index??-1)+1}行：${esc(v.message)}</div>`
  ).join("") || `<div class="muted">${side}：未发现安全违规</div>`;
  return `<div class="snapcol ${side==="B"?"b":""}"><h3>${side} 安全检查</h3>${vs}</div>`;
}

function renderForks(){
  const forks = STATE.data.comparison.forks;
  $("forkbar").innerHTML = forks.length
    ? forks.map(f=>`<div class="forkitem" data-fork="${f.fork_id}">
        🔀 分叉#${f.fork_id} 行 A${(f.line_a??-1)+1}/B${(f.line_b??-1)+1}
        · ${f.reason==="motion"?"运动变化":"仅模态"}
        · 受影响运动 A[${f.affected_a.length}] B[${f.affected_b.length}]
        · ${f.converged?("在 A"+(f.converges_at[0]+1)+"/B"+(f.converges_at[1]+1)+" 收敛"):"至程序末尾未收敛"}
        — ${esc(f.detail)}</div>`).join("")
    : `<span class="muted">无状态分叉</span>`;
  document.querySelectorAll(".forkitem").forEach(el=>{
    el.onclick=()=>{
      const f=forks[parseInt(el.dataset.fork)-1];
      selectLine("A",f.line_a??f.converges_at?.[0]??0);
    };
  });
}

function getPlane(){
  const r = document.querySelector('input[name=plane]:checked');
  return r ? r.value : "XY";
}
function proj(p){
  const pl = getPlane();
  return pl==="XY" ? [p[0],p[1]] : pl==="XZ" ? [p[0],p[2]] : [p[1],p[2]];
}

function draw(){
  const cv=$("cv"), dpr=window.devicePixelRatio||1;
  const w=cv.clientWidth, h=cv.clientHeight;
  cv.width=w*dpr; cv.height=h*dpr;
  const ctx=cv.getContext("2d"); ctx.scale(dpr,dpr);
  ctx.clearRect(0,0,w,h);
  if(!STATE.data) return;

  const d=STATE.data;
  const all=[];
  const collect=model=>model.segments.forEach(s=>s.points.forEach(p=>all.push(proj(p))));
  collect(d.model_a); collect(d.model_b);
  if(!all.length){$("pickInfo").textContent="无运动";return;}
  let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
  all.forEach(([x,y])=>{minx=Math.min(minx,x);maxx=Math.max(maxx,x);
    miny=Math.min(miny,y);maxy=Math.max(maxy,y);});
  const pad=34, sx=(w-2*pad)/Math.max(1e-6,maxx-minx),
    sy=(h-2*pad)/Math.max(1e-6,maxy-miny);
  const sc=Math.min(sx,sy);
  const X=x=>pad+(x-minx)*sc, Y=y=>h-pad-(y-miny)*sc;
  // 毛坯安全盒（仅 XY 俯视有意义）
  if(getPlane()==="XY"){
    const st=d.stock;
    ctx.strokeStyle="rgba(255,107,107,.5)"; ctx.setLineDash([6,5]);
    ctx.strokeRect(X(st.xmin),Y(st.ymax),(st.xmax-st.xmin)*sc,(st.ymax-st.ymin)*sc);
    ctx.setLineDash([]);
    ctx.fillStyle="rgba(255,107,107,.6)";
    ctx.fillText("毛坯安全区",X(st.xmin)+4,Y(st.ymax)-4);
  }
  const drawModel=(model,color,layerOn,isA)=>{
    if(!layerOn) return;
    const segIds=isA?null:null;
    model.segments.forEach((s,si)=>{
      const isSel=(isA?STATE.selA:STATE.selB)===s.line_index;
      const aff=(isA?STATE.affA:STATE.affB);
      const isAff=aff.has(s.line_index);
      ctx.beginPath();
      s.points.forEach((p,i)=>{const[x,y]=proj(p);
        i?ctx.lineTo(X(x),Y(y)):ctx.moveTo(X(x),Y(y));});
      ctx.strokeStyle=isSel?"#fff":(isAff&&$("layerDiff").checked?"#ff5d5d":color);
      ctx.lineWidth=isSel?2.6:(s.kind==="rapid"?1:1.7);
      if(s.kind==="rapid"&&!isSel){ctx.setLineDash([3,3]);}
      ctx.globalAlpha=($("layerDiff").checked&&isAff)?1:0.85;
      ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha=1;
      if(s.kind==="rapid"&&!isSel){
        const p0=s.points[0],p1=s.points[s.points.length-1];
        ctx.fillStyle=color;
        ctx.beginPath();ctx.arc(X(proj(p1)[0]),Y(proj(p1)[1]),1.8,0,7);ctx.fill();
      }
    });
  };
  drawModel(d.model_a,"#5aa9ff",$("layerA").checked,true);
  drawModel(d.model_b,"#ffb454",$("layerB").checked,false);
  // 起点标记
  const origin=d.model_a.segments[0]?.points[0];
  if(origin){const [x,y]=proj(origin);
    ctx.fillStyle="#46c46a";ctx.beginPath();ctx.arc(X(x),Y(y),4,0,7);ctx.fill();
    ctx.fillStyle="#8b93a3";ctx.fillText("起",X(x)+6,Y(y)+3);}
}
window.addEventListener("resize",()=>STATE.data&&draw());
["layerA","layerB","layerDiff"].forEach(id=>$(id).onchange=()=>draw());
document.querySelectorAll('input[name=plane]').forEach(r=>r.onchange=()=>draw());

// ---- 未知指令人工确认 ----
function openModal(code,raw){
  $("cfCode").value=code;
  $("cfRaw").value=raw;
  $("cfNote").value="";
  $("cfVersion").value=$("version").value;
  $("cfMachine").value=$("machine").value;
  $("modalBg").classList.add("show");
}
$("cfCancel").onclick=()=>$("modalBg").classList.remove("show");
$("cfSave").onclick=async()=>{
  const body={code:$("cfCode").value, effect:$("cfEffect").value,
    note:$("cfNote").value, version:$("cfVersion").value,
    machine:$("cfMachine").value};
  const r=await fetch("/api/confirmations",{method:"POST",
    headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  if(!r.ok){alert("保存失败："+(await r.text()));return;}
  $("modalBg").classList.remove("show");
  $("version").value=body.version; $("machine").value=body.machine;
  await doCompare();
};

// ---- 导出：先把两段文本落库，再让后端从原始文本生成显式补丁 ----
$("btnExport").onclick=async()=>{
  const up=async(name,text)=>{
    const fd=new FormData();
    fd.append("name",name); fd.append("machine",$("machine").value);
    fd.append("file",new Blob([new TextEncoder().encode(text)]),"g.gcode");
    const r=await fetch("/api/programs",{method:"POST",body:fd});
    if(!r.ok) throw new Error(await r.text());
    return (await r.json()).id;
  };
  try{
    const pa=await up("original.gcode",$("srcA").value);
    const pb=await up("modified.gcode",$("srcB").value);
    const r=await fetch("/api/export",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({program_a:pa,program_b:pb,
        version:$("version").value,machine:$("machine").value})});
    if(!r.ok){alert("导出失败："+(await r.text()));return;}
    const blob=await r.blob();
    const a=document.createElement("a");
    a.href=URL.createObjectURL(blob);a.download="review_export.zip";a.click();
  }catch(e){alert("导出失败："+e.message);}
};
