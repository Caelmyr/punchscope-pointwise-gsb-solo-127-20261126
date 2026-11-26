"use strict";

const $ = (id) => document.getElementById(id);
const edges = ["left", "top", "right", "bottom"];
const scalars = ["scan_y", "threshold", "min_area", "scale", "edge_margin"];
const modes = ["probe", "box", "scan"];
const downloadLinks = { csv: "section.csv", proof: "proof.png", crop: "crop.png", receipt: "receipt.zip" };
const state = { card: null, image: null, overlay: null, report: null, probe: null,
  mode: "probe", drag: null, preview: null, busy: false,
  gaugeHoleId: null, gauge: null, adopted: null, clearance: null, gaugeSeq: 0 };

async function request(path, options) {
  const response = await fetch(`/punchscope/${path}`, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "读取检查卡失败");
  return body;
}

function send(method, payload) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}

function cardPath(suffix = "") {
  return `cards/${state.card.id}${suffix ? "/" + suffix : ""}`;
}

function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").dataset.error = String(error);
}

async function run(work) {
  if (state.busy) return;
  state.busy = true;
  document.body.setAttribute("aria-busy", "true");
  notice("");
  try {
    await work();
  } catch (error) {
    notice(error.message, true);
  } finally {
    state.busy = false;
    document.body.setAttribute("aria-busy", "false");
  }
}

function picture(url) {
  const image = new Image();
  return new Promise((resolve, reject) => {
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("原片或开口图层加载失败"));
    image.src = url;
  });
}

async function listing() {
  const { cards } = await request("cards");
  const picker = $("cards");
  picker.replaceChildren(new Option("选择检查卡", ""));
  cards.forEach((card) => picker.add(new Option(card.name, card.id)));
  if (state.card) picker.value = state.card.id;
  $("open").disabled = cards.length === 0;
}

function inputSettings() {
  const box = Object.fromEntries(edges.map((edge) => [edge, $(edge).valueAsNumber]));
  return { box, ...Object.fromEntries(scalars.map((id) => [id, $(id).valueAsNumber])) };
}

function writeInputs() {
  const card = state.card;
  const settings = card.settings;
  edges.forEach((edge) => { $(edge).value = settings.box[edge]; });
  scalars.forEach((id) => { $(id).value = settings[id]; });
  $("left").max = card.size[0] - 1;
  $("right").max = card.size[0];
  $("top").max = card.size[1] - 1;
  $("bottom").max = card.size[1];
  $("scan_y").max = card.size[1] - 1;
  $("edge_margin").max = card.size[1];
  $("min_area").max = card.size[0] * card.size[1];
  $("card-name").textContent = card.name;
  $("revision").textContent = `已保存 · v${card.revision}`;
  $("source-meta").textContent = `${card.synthetic ? "合成练习" : card.source_name} · ${card.size.join(" × ")} px`;
  for (const [key, path] of Object.entries(downloadLinks)) {
    $(`download-${key}`).href = `/punchscope/${cardPath(path)}`;
  }
}

function outline(context, box, color) {
  context.strokeStyle = color;
  context.strokeRect(box.left + .5, box.top + .5, box.right - box.left - 1, box.bottom - box.top - 1);
}

function drawEdges(context, points) {
  context.fillStyle = "#dfed92";
  const weight = Math.max(1, context.lineWidth);
  for (const column of points) {
    if (column.top !== null) context.fillRect(column.x, column.top, 1, weight);
    if (column.bottom !== null) context.fillRect(column.x, column.bottom, 1, weight);
  }
}

function paint() {
  if (!state.image) return;
  const photo = $("photo");
  const context = photo.getContext("2d");
  context.clearRect(0, 0, photo.width, photo.height);
  context.drawImage(state.image, 0, 0);
  if (state.overlay && $("show-map").checked) context.drawImage(state.overlay, 0, 0);
  context.lineWidth = Math.max(1, photo.width / 600);
  const settings = state.card.settings;
  outline(context, settings.box, "#4ce5ce");
  if (state.report) drawEdges(context, state.report.edges.points);
  context.beginPath();
  context.strokeStyle = "#ffb55a";
  context.moveTo(settings.box.left, settings.scan_y + .5);
  context.lineTo(settings.box.right, settings.scan_y + .5);
  context.stroke();
  const draft = inputSettings();
  const candidate = state.preview || draft.box;
  if (candidate.right > candidate.left && candidate.bottom > candidate.top &&
      edges.some((edge) => candidate[edge] !== settings.box[edge])) {
    context.setLineDash([6, 4]);
    outline(context, candidate, "#fff9df");
    context.setLineDash([]);
  }
  if (Number.isFinite(draft.scan_y) && draft.scan_y !== settings.scan_y) {
    context.setLineDash([5, 5]);
    context.beginPath();
    context.strokeStyle = "#fff9df";
    context.moveTo(candidate.left, draft.scan_y + .5);
    context.lineTo(candidate.right, draft.scan_y + .5);
    context.stroke();
    context.setLineDash([]);
  }
  if (state.probe) {
    const { x, y } = state.probe;
    if (state.probe.hole) outline(context, state.probe.hole.box, "#ffffff");
    context.lineWidth = Math.max(2, context.lineWidth);
    context.strokeStyle = "#ffffff";
    context.beginPath();
    context.moveTo(x - 8, y);
    context.lineTo(x + 8, y);
    context.moveTo(x, y - 8);
    context.lineTo(x, y + 8);
    context.stroke();
  }
  if (state.gauge?.fits) drawGauge(context, state.gauge);
  if (state.clearance?.inside) drawClearance(context, state.clearance);
}

function polygonPath(context, corners) {
  context.beginPath();
  corners.forEach((point, index) => {
    if (index) context.lineTo(point.x, point.y);
    else context.moveTo(point.x, point.y);
  });
  context.closePath();
}

function labelChip(context, x, y, text, color) {
  context.font = `${Math.max(12, Math.round(context.canvas.width / 52))}px ui-monospace, monospace`;
  const padding = 5;
  const width = context.measureText(text).width + padding * 2;
  const height = Math.max(18, Math.round(context.canvas.width / 46));
  context.fillStyle = "rgba(13, 20, 15, .82)";
  context.fillRect(x, y - height, width, height);
  context.strokeStyle = color;
  context.lineWidth = 1;
  context.strokeRect(x + .5, y - height + .5, width - 1, height - 1);
  context.fillStyle = color;
  context.textBaseline = "middle";
  context.fillText(text, x + padding, y - height / 2 + 1);
  context.textBaseline = "alphabetic";
  return { width, height };
}

function drawGauge(context, fit) {
  const gauge = fit.gauge;
  if (!gauge?.corners?.length) return;
  const adopted = state.adopted?.hole === fit.hole;
  const color = adopted ? "#6ef2cf" : "#d6ee83";
  context.save();
  polygonPath(context, gauge.corners);
  context.fillStyle = adopted ? "rgba(110, 242, 207, .16)" : "rgba(214, 238, 131, .13)";
  context.fill();
  context.lineWidth = Math.max(1.6, context.canvas.width / 430);
  context.strokeStyle = color;
  if (!adopted) context.setLineDash([9, 5]);
  context.stroke();
  context.setLineDash([]);
  // Centre line along the gauge length.
  const [a, b, c] = gauge.corners;
  const mid01 = { x: (gauge.corners[0].x + gauge.corners[1].x) / 2,
                  y: (gauge.corners[0].y + gauge.corners[1].y) / 2 };
  const mid23 = { x: (gauge.corners[2].x + gauge.corners[3].x) / 2,
                  y: (gauge.corners[2].y + gauge.corners[3].y) / 2 };
  context.strokeStyle = "rgba(255,255,255,.75)";
  context.lineWidth = Math.max(1, context.canvas.width / 900);
  context.setLineDash([4, 5]);
  context.beginPath();
  context.moveTo(mid01.x, mid01.y);
  context.lineTo(mid23.x, mid23.y);
  context.stroke();
  context.setLineDash([]);
  const minX = Math.min(...gauge.corners.map((p) => p.x));
  const minY = Math.min(...gauge.corners.map((p) => p.y));
  const text = `扁规 宽${gauge.width_mm}mm · 进深${gauge.depth_mm}mm`;
  labelChip(context, minX, minY - 7, text, color);
  context.restore();
}

function drawClearance(context, clearance) {
  const { point, sides } = clearance;
  context.save();
  context.lineWidth = Math.max(1.6, context.canvas.width / 430);
  context.strokeStyle = "#ff9d8a";
  context.fillStyle = "#ff9d8a";
  for (const side of sides) {
    const end = side.wall || side.limit;
    if (!end) continue;
    context.beginPath();
    context.moveTo(side.edge.x, side.edge.y);
    context.lineTo(end.x, end.y);
    context.stroke();
    context.beginPath();
    context.arc(end.x, end.y, Math.max(2.5, context.canvas.width / 260), 0, Math.PI * 2);
    context.fill();
    const text = `${side.label} ${side.gap_mm}mm`;
    const offset = side.side === "minus" ? -8 : 16;
    labelChip(context, end.x + 6, end.y + offset, text, "#ffc7bb");
  }
  context.strokeStyle = "#ffffff";
  context.lineWidth = Math.max(1.2, context.canvas.width / 650);
  context.beginPath();
  context.arc(point.x, point.y, Math.max(3, context.canvas.width / 240), 0, Math.PI * 2);
  context.stroke();
  context.restore();
}

function drawProfile(section) {
  const canvas = $("profile");
  const context = canvas.getContext("2d");
  const { width, height } = canvas;
  context.clearRect(0, 0, width, height);
  context.font = "13px monospace";
  context.fillStyle = "#a9baa8";
  if (!section.points.length) {
    context.fillText("截线在量区外", 20, 28);
    return;
  }
  const xAt = (index) => 33 + index * (width - 51) / Math.max(1, section.points.length - 1);
  const yAt = (gray) => 14 + (255 - gray) * (height - 35) / 255;
  for (const gray of [255, 128, 0]) {
    context.strokeStyle = "#38463b";
    context.beginPath();
    context.moveTo(33, yAt(gray));
    context.lineTo(width - 18, yAt(gray));
    context.stroke();
    context.fillText(gray, 5, yAt(gray) + 4);
  }
  context.strokeStyle = "#61daba";
  context.beginPath();
  section.points.forEach((point, index) => {
    if (index) context.lineTo(xAt(index), yAt(point.gray));
    else context.moveTo(xAt(index), yAt(point.gray));
  });
  context.stroke();
  context.fillText(section.points[0].x, 32, height - 3);
  context.textAlign = "right";
  context.fillText(section.points.at(-1).x, width - 18, height - 3);
  context.textAlign = "left";
  if (state.clearance?.inside && Number.isFinite(state.clearance.x)) {
    const index = state.clearance.x - section.points[0].x;
    if (index >= 0 && index < section.points.length) {
      const markerX = xAt(index);
      context.strokeStyle = "#ff9d8a";
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(markerX, 10);
      context.lineTo(markerX, height - 16);
      context.stroke();
      context.fillStyle = "#ff9d8a";
      context.fillText("规位", markerX + 4, 24);
    }
  }
}

function tableBody(id, rows) {
  const fragment = document.createDocumentFragment();
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((item) => {
      const td = document.createElement("td");
      if (item instanceof Node) td.append(item);
      else td.textContent = item;
      tr.append(td);
    });
    fragment.append(tr);
  });
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.textContent = "当前无测量结果";
    tr.append(td);
    fragment.append(tr);
  }
  $(id).replaceChildren(fragment);
}

function holeButton(hole) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = `孔 ${hole.id}`;
  const point = hole.gauge || hole.center;
  button.addEventListener("click", () => probe(Math.round(point.x), Math.round(point.y)));
  return button;
}

function gaugePickButton(hole) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "gauge-pick";
  button.dataset.gauge = String(hole.id);
  button.dataset.testid = "select-gauge";
  button.setAttribute("aria-label", `为孔 ${hole.id} 选扁规`);
  button.textContent = "选扁规";
  return button;
}

function renderHoleRows(holes) {
  tableBody("holes", holes.map((hole) => [
    holeButton(hole), `${hole.center.x}, ${hole.center.y}`, hole.area,
    hole.gauge?.diameter ?? "贴边", gaugePickButton(hole),
  ]));
  const tbody = $("holes");
  [...tbody.rows].forEach((tr, index) => {
    const hole = holes[index];
    if (!hole) return;
    tr.dataset.hole = String(hole.id);
    tr.tabIndex = 0;
    tr.setAttribute("aria-selected", String(state.gaugeHoleId === hole.id));
    if (state.gaugeHoleId === hole.id) tr.classList.add("selected");
    tr.addEventListener("click", () => selectHole(hole.id));
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectHole(hole.id);
      }
    });
  });
}

function renderReport() {
  const { area, section, edges: borders, holes } = state.report;
  $("open-rate").textContent = `${(area.fraction * 100).toFixed(2)}%`;
  $("raw-rate").textContent = `阈值以上像素 ${(area.raw_fraction * 100).toFixed(2)}%`;
  $("hole-count").textContent = area.count;
  $("hole-detail").textContent = `测量表列出 ${area.listed} 个`;
  $("run-count").textContent = section.runs.length;
  $("run-detail").textContent = `原片 Y = ${section.row}`;
  $("edge-height").textContent = borders.valid ? `${borders.minimum}–${borders.maximum} mm` : "—";
  $("edge-detail").textContent = `两侧有证 ${borders.valid} 列`;
  $("line-detail").textContent = section.points.length ? `原片行 Y=${section.row} · 亮度 0–255` : "截线在量区外";
  renderHoleRows(holes);
  tableBody("gaps", section.gaps.map((gap) => [gap.from, gap.to, gap.pitch]));
  drawProfile(section);
  renderGaugePanel();
}

async function loadMeasurements() {
  const [report, overlay] = await Promise.all([
    request(cardPath("report")),
    picture(`/punchscope/${cardPath("overlay.png")}?revision=${state.card.revision}`),
  ]);
  if (report.revision !== state.card.revision) throw new Error("检查卡已经更新，请重新打开");
  state.report = report;
  state.overlay = overlay;
  renderReport();
  paint();
}

async function openCard(card) {
  const source = await picture(`/punchscope/cards/${card.id}/source.png`);
  state.card = card;
  state.image = source;
  state.probe = null;
  state.preview = null;
  state.report = null;
  state.overlay = null;
  state.gaugeHoleId = null;
  state.gauge = null;
  state.adopted = null;
  state.clearance = null;
  state.gaugeSeq += 1;
  const photo = $("photo");
  [photo.width, photo.height] = card.size;
  writeInputs();
  $("probe-result").textContent = "点击原片查看像素与所在孔件。";
  $("clearance-result").textContent = "在孔件表里选孔并「采用扁规」后，在这里沿规的长度点一个位置，读短边方向两侧离原孔壁多远。";
  $("clearance-result").dataset.inside = "false";
  setGapMetrics(null);
  $("profile").setAttribute("aria-armed", "false");
  renderGaugePanel();
  $("desk").hidden = false;
  paint();
  await loadMeasurements();
  await listing();
}

function probe(x, y) {
  return run(async () => {
    const result = await request(`${cardPath("probe")}?x=${x}&y=${y}`);
    if (result.revision !== state.card.revision) throw new Error("检查卡已经更新，请重新打开");
    state.probe = result;
    const hole = result.hole;
    const text = [`(${x}, ${y})`, `RGB ${result.rgb.join("/")}`, result.status];
    if (hole) text.push(`孔 ${hole.id} · ${hole.area} px²`, hole.gauge ? `内接直径 ${hole.gauge.diameter} mm` : "轮廓贴边");
    $("probe-result").textContent = text.join(" · ");
    paint();
  });
}

async function selectHole(holeId) {
  if (!state.card) return;
  const changed = state.gaugeHoleId !== holeId;
  state.gaugeHoleId = holeId;
  if (changed) {
    state.adopted = null;
    state.clearance = null;
  }
  document.querySelectorAll("#holes tr").forEach((tr) => {
    const selected = Number(tr.dataset.hole) === holeId;
    tr.classList.toggle("selected", selected);
    tr.setAttribute("aria-selected", String(selected));
  });
  const seq = ++state.gaugeSeq;
  $("gauge-adopt").disabled = true;
  $("gauge-panel").dataset.state = "loading";
  $("gauge-result").textContent = "正在拟合沿孔长放置的扁规…";
  try {
    const data = await request(cardPath(`gauge?hole=${holeId}`));
    if (seq !== state.gaugeSeq) return;
    if (data.revision !== state.card.revision) throw new Error("检查卡已经更新，请重新打开");
    state.gauge = data;
    setupWidthSlider(data);
    renderGaugePanel();
    paint();
  } catch (error) {
    if (seq !== state.gaugeSeq) return;
    state.gauge = null;
    renderGaugePanel(error.message);
    paint();
    notice(error.message, true);
  }
}

function setupWidthSlider(data) {
  const slider = $("gauge-width");
  const widths = data.frontier.map((entry) => entry.width_px);
  slider.disabled = !data.fits || widths.length === 0;
  if (!data.fits) return;
  slider.min = String(Math.min(...widths));
  slider.max = String(Math.max(...widths));
  slider.step = "1";
  slider.value = String(data.gauge.width_px);
}

function setMetricHooks(panel, position, widthMm, depthMm, fits) {
  if (panel) {
    panel.dataset.fits = String(fits);
    if (fits) panel.dataset.state = "ready";
  }
  const setText = (id, text) => { const node = $(id); if (node) node.textContent = text; };
  setText("gauge-position", position);
  setText("gauge-width-mm", widthMm);
  setText("gauge-depth-mm", depthMm);
}

function renderGaugePanel(errorMessage) {
  const result = $("gauge-result");
  const adopt = $("gauge-adopt");
  const slider = $("gauge-width");
  const value = $("gauge-width-value");
  const panel = $("gauge-panel");
  const fit = state.gauge;
  if (errorMessage) {
    result.textContent = errorMessage;
    adopt.disabled = true;
    slider.disabled = true;
    value.textContent = "不可用";
    if (panel) panel.dataset.state = "error";
    setMetricHooks(panel, "—", "—", "—", false);
    if (panel) panel.dataset.state = "error";
    return;
  }
  if (!state.gaugeHoleId || !fit) {
    result.textContent = "在孔件表中点一行或「选扁规」，这里给出规的位置、宽度和进深。";
    adopt.disabled = true;
    slider.disabled = true;
    value.textContent = "未选孔";
    if (panel) panel.dataset.state = "idle";
    setMetricHooks(panel, "—", "—", "—", false);
    if (panel) panel.dataset.state = "idle";
    return;
  }
  if (!fit.fits) {
    result.textContent = fit.message || "这个孔件不能给出通过的扁规。";
    adopt.disabled = true;
    slider.disabled = true;
    value.textContent = "不通过";
    if (panel) panel.dataset.state = "rejected";
    setMetricHooks(panel, "—", "—", "—", false);
    if (panel) panel.dataset.state = "rejected";
    return;
  }
  const g = fit.gauge;
  result.textContent = `扁规 · 孔 ${fit.hole} · 位置 (${g.center.x}, ${g.center.y}) · 宽 ${g.width_px} px / ${g.width_mm} mm · 进深 ${g.depth_px} px / ${g.depth_mm} mm · 方向 ${fit.orientation.angle_deg}°`;
  value.textContent = `${g.width_px} px · ${g.width_mm} mm`;
  setMetricHooks(panel, `(${g.center.x}, ${g.center.y})`, `${g.width_mm} mm`,
                  `${g.depth_mm} mm`, true);
  panel.dataset.hole = String(fit.hole);
  panel.dataset.widthPx = String(g.width_px);
  panel.dataset.depthPx = String(g.depth_px);
  adopt.disabled = false;
  const armed = state.adopted?.hole === fit.hole;
  adopt.setAttribute("aria-pressed", String(armed));
  adopt.textContent = armed ? "已采用" : "采用";
  $("profile").setAttribute("aria-armed", String(armed));
}

let widthTimer = null;
$("gauge-width").addEventListener("input", () => {
  const width = $("gauge-width").valueAsNumber;
  $("gauge-width-value").textContent = `${width} px · 换算中…`;
  clearTimeout(widthTimer);
  widthTimer = setTimeout(() => changeGaugeWidth(width), 120);
});

async function changeGaugeWidth(widthPx) {
  if (!state.gaugeHoleId) return;
  const seq = ++state.gaugeSeq;
  try {
    const data = await request(cardPath(`gauge?hole=${state.gaugeHoleId}&width=${widthPx}`));
    if (seq !== state.gaugeSeq) return;
    if (data.revision !== state.card.revision) throw new Error("检查卡已经更新，请重新打开");
    state.gauge = data;
    if (state.adopted?.hole === data.hole) {
      state.adopted.width_px = data.gauge.width_px;
      state.clearance = null;
      $("clearance-result").textContent = "宽度已调整，请再次在截线图上沿规长点选位置。";
      if (state.report) drawProfile(state.report.section);
    }
    renderGaugePanel();
    paint();
  } catch (error) {
    notice(error.message, true);
  }
}

$("gauge-adopt").addEventListener("click", () => {
  if (!state.gauge?.fits) return;
  state.adopted = { hole: state.gauge.hole, width_px: state.gauge.gauge.width_px };
  state.clearance = null;
  setGapMetrics(null);
  $("clearance-result").textContent =
    `已采用孔 ${state.adopted.hole} 的扁规（宽 ${state.gauge.gauge.width_mm} mm）。在下方截线图上沿规的长度点一个位置，读短边方向两侧离原孔壁的距离。`;
  renderGaugePanel();
  if (state.report) drawProfile(state.report.section);
  paint();
});

function setGapMetrics(data) {
  const sides = data?.inside ? data.sides : null;
  const a = sides?.[0];
  const b = sides?.[1];
  const labelA = $("gap-a-label");
  const labelB = $("gap-b-label");
  const valA = $("gap-a-mm");
  const valB = $("gap-b-mm");
  if (labelA) labelA.textContent = a ? `${a.label}间隙` : "短边一侧间隙";
  if (labelB) labelB.textContent = b ? `${b.label}间隙` : "另一侧间隙";
  if (valA) valA.textContent = a ? `${a.gap_mm} mm` : "—";
  if (valB) valB.textContent = b ? `${b.gap_mm} mm` : "—";
}

async function readClearance(nativeX) {
  const adopted = state.adopted;
  if (!adopted) return;
  const target = $("clearance-result");
  target.textContent = "正在读取两侧离壁距离…";
  try {
    const query = `hole=${adopted.hole}&x=${nativeX}&width=${adopted.width_px}`;
    const data = await request(cardPath(`gauge/clearance?${query}`));
    if (data.revision !== state.card.revision) throw new Error("检查卡已经更新，请重新打开");
    state.clearance = data;
    target.dataset.inside = String(Boolean(data.inside));
    if (data.inside) {
      target.dataset.gapAMm = String(data.gaps_mm?.[0] ?? "");
      target.dataset.gapBMm = String(data.gaps_mm?.[1] ?? "");
      target.textContent = data.message;
    } else {
      target.textContent = data.message || "这个点不在扁规长度范围内。";
    }
    setGapMetrics(data);
    paint();
    if (state.report) drawProfile(state.report.section);
  } catch (error) {
    target.textContent = error.message;
    notice(error.message, true);
  }
}

function nativePoint(event) {
  const photo = $("photo");
  const area = photo.getBoundingClientRect();
  const x = Math.floor((event.clientX - area.left - photo.clientLeft) * photo.width / photo.clientWidth);
  const y = Math.floor((event.clientY - area.top - photo.clientTop) * photo.height / photo.clientHeight);
  return { x: Math.max(0, Math.min(photo.width - 1, x)), y: Math.max(0, Math.min(photo.height - 1, y)) };
}

function span(a, b) {
  return { left: Math.min(a.x, b.x), right: Math.max(a.x, b.x) + 1,
    top: Math.min(a.y, b.y), bottom: Math.max(a.y, b.y) + 1 };
}

for (const mode of modes) {
  $(`mode-${mode}`).addEventListener("click", () => {
    state.mode = mode;
    modes.forEach((value) => $(`mode-${value}`).setAttribute("aria-pressed", String(mode === value)));
  });
}
$("photo").addEventListener("pointerdown", (event) => {
  if (state.busy || event.button !== 0) return;
  const point = nativePoint(event);
  if (state.mode === "probe") return probe(point.x, point.y);
  if (state.mode === "scan") {
    $("scan_y").value = point.y;
    return paint();
  }
  $("photo").setPointerCapture(event.pointerId);
  state.drag = point;
  state.preview = span(point, point);
});
$("photo").addEventListener("pointermove", (event) => {
  const point = nativePoint(event);
  $("coordinates").textContent = `X ${point.x} · Y ${point.y}`;
  if (state.drag) {
    state.preview = span(state.drag, point);
    paint();
  }
});
$("photo").addEventListener("pointerup", (event) => {
  if (!state.drag) return;
  const box = span(state.drag, nativePoint(event));
  edges.forEach((edge) => { $(edge).value = box[edge]; });
  state.drag = null;
  state.preview = null;
  paint();
});
$("photo").addEventListener("pointercancel", () => { state.drag = null; state.preview = null; paint(); });
$("show-map").addEventListener("change", paint);
$("settings").addEventListener("input", paint);
$("profile").addEventListener("click", (event) => {
  const section = state.report?.section;
  if (!section?.points.length) return;
  const box = $("profile").getBoundingClientRect();
  const viewportX = (event.clientX - box.left) * $("profile").width / box.width;
  const index = Math.round((viewportX - 33) * (section.points.length - 1) / ($("profile").width - 51));
  if (index < 0 || index >= section.points.length) return;
  const nativeX = section.points[index].x;
  probe(nativeX, section.row);
  if (state.adopted) readClearance(nativeX);
});
$("upload").addEventListener("submit", (event) => {
  event.preventDefault();
  run(async () => {
    const form = new FormData(event.currentTarget || $("upload"));
    await openCard(await request("cards", { method: "POST", body: form }));
    notice("背光原片已经导入。");
  });
});
$("practice").addEventListener("submit", (event) => {
  event.preventDefault();
  run(async () => {
    await openCard(await request("practice", send("POST", { sample: $("sample").value })));
    notice("合成练习已经打开。");
  });
});
$("open").addEventListener("click", () => {
  if (!$("cards").value) return;
  run(async () => openCard(await request(`cards/${$("cards").value}`)));
});
$("settings").addEventListener("submit", (event) => {
  event.preventDefault();
  run(async () => {
    const next = await request(cardPath(), send("PATCH", { revision: state.card.revision, ...inputSettings() }));
    state.card = next;
    state.probe = null;
    state.adopted = null;
    state.clearance = null;
    $("probe-result").textContent = "量区已更新，点击原片重新点读。";
    writeInputs();
    await loadMeasurements();
    if (state.gaugeHoleId && state.report.holes.some((hole) => hole.id === state.gaugeHoleId)) {
      await selectHole(state.gaugeHoleId);
    } else {
      state.gaugeHoleId = null;
      state.gauge = null;
      renderGaugePanel();
      paint();
    }
    notice("检查设置与本次测量已经保存。");
  });
});
$("transfer").addEventListener("click", () => {
  run(async () => {
    const { url } = await request(cardPath("prepare"), { method: "POST" });
    window.location.assign(url);
  });
});
async function boot() {
  await listing();
  const params = new URLSearchParams(window.location.search);
  const linked = params.get("card") || params.get("id") || params.get("key");
  if (linked && /^[0-9a-f]{20}$/.test(linked)) {
    try {
      await run(async () => openCard(await request(`cards/${linked}`)));
    } catch (error) {
      notice(error.message, true);
    }
  }
}
boot().catch((error) => notice(error.message, true));
