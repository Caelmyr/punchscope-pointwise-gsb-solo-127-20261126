"use strict";

const $ = (id) => document.getElementById(id);
const edges = ["left", "top", "right", "bottom"];
const scalars = ["scan_y", "threshold", "min_area", "scale", "edge_margin"];
const modes = ["probe", "box", "scan"];
const downloadLinks = { csv: "section.csv", proof: "proof.png", crop: "crop.png", receipt: "receipt.zip" };
const state = { card: null, image: null, overlay: null, report: null, probe: null,
  mode: "probe", drag: null, preview: null, busy: false };

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
    td.colSpan = 4;
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
  tableBody("holes", holes.map((hole) => [
    holeButton(hole), `${hole.center.x}, ${hole.center.y}`, hole.area, hole.gauge?.diameter ?? "贴边",
  ]));
  tableBody("gaps", section.gaps.map((gap) => [gap.from, gap.to, gap.pitch]));
  drawProfile(section);
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
  const photo = $("photo");
  [photo.width, photo.height] = card.size;
  writeInputs();
  $("probe-result").textContent = "点击原片查看像素与所在孔件。";
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
  if (index >= 0 && index < section.points.length) probe(section.points[index].x, section.row);
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
    $("probe-result").textContent = "量区已更新，点击原片重新点读。";
    writeInputs();
    await loadMeasurements();
    notice("检查设置与本次测量已经保存。");
  });
});
$("transfer").addEventListener("click", () => {
  run(async () => {
    const { url } = await request(cardPath("prepare"), { method: "POST" });
    window.location.assign(url);
  });
});
listing().catch((error) => notice(error.message, true));
