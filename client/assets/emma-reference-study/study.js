const SVG_NS = 'http://www.w3.org/2000/svg';
const byId = (id) => document.getElementById(id);
const state = { data: null, part: null, mode: 'overlay', opacity: 0.85, zoom: 1, layers: new Map() };
const api = { ready: false, currentPart: null, setPart: selectPart, setMode, getState: () => ({ part: state.part?.id, mode: state.mode, opacity: state.opacity, zoom: state.zoom }) };
window.emmaReferenceStudy = api;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svgElement(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function updateViewBox() {
  if (!state.part) return;
  const [x, y, w, h] = state.part.viewBox;
  const nw = w / state.zoom;
  const nh = h / state.zoom;
  byId('reference-drawing').setAttribute('viewBox', [x + (w - nw) / 2, y + (h - nh) / 2, nw, nh].join(' '));
}

function setMode(mode) {
  if (!['reference', 'overlay', 'trace'].includes(mode)) return;
  state.mode = mode;
  document.querySelectorAll('[data-mode]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
  byId('source-image').style.opacity = mode === 'trace' ? '0' : '1';
  byId('path-layer').style.opacity = mode === 'reference' ? '0' : String(state.opacity);
  byId('path-layer').querySelectorAll('path').forEach((path) => path.setAttribute('fill', mode === 'trace' ? path.dataset.traceFill : 'none'));
  byId('landmark-layer').style.display = mode !== 'reference' && byId('show-landmarks').checked ? '' : 'none';
  byId('trace-opacity').disabled = mode === 'reference';
  byId('show-landmarks').disabled = mode === 'reference';
  byId('mode-label').textContent = { reference: 'ORIGINAL REFERENCE', overlay: 'REFERENCE + TRACE', trace: 'TRACE / DRAFT' }[mode];
}

function resetZoom() {
  state.zoom = 1;
  byId('drawing-zoom').value = '100';
  byId('zoom-value').textContent = '1×';
  updateViewBox();
}

function selectPart(id, focus = false) {
  const part = state.data?.parts.find((item) => item.id === id);
  if (!part) return;
  state.part = part;
  api.currentPart = part.id;
  document.querySelectorAll('.part-tab').forEach((button) => {
    const selected = button.dataset.part === id;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
    if (focus && selected) button.focus();
  });
  byId('part-panel').setAttribute('aria-labelledby', `tab-${part.id}`);
  byId('part-number').textContent = part.step;
  byId('drawing-number').textContent = part.step;
  byId('part-name').textContent = part.name;
  byId('drawing-name').textContent = part.name;
  byId('drawing-title').textContent = `${part.name} — ภาพต้นแบบและเส้นศึกษา`;
  byId('part-download').href = `./parts/${encodeURIComponent(part.id)}.svg`;
  byId('part-observation').textContent = part.observation || '';
  byId('part-construction').textContent = part.construction || '';
  byId('part-uncertainty').textContent = part.uncertainty || '';
  byId('uncertainty-note').hidden = !part.uncertainty;
  ['x', 'y', 'width', 'height'].forEach((attr, index) => byId('crop-rectangle').setAttribute(attr, part.viewBox[index]));
  resetZoom();
  renderPaths(part);
  renderLandmarks(part);
  renderDimensions(part);
  setMode(state.mode);
}

function renderPaths(part) {
  const paths = byId('path-layer');
  const controls = byId('layer-controls');
  paths.replaceChildren();
  controls.replaceChildren();
  (part.paths || []).forEach((path) => {
    const key = `${part.id}:${path.id}`;
    const visible = state.layers.get(key) ?? true;
    const color = path.color || '#b8dfd3';
    const drawn = svgElement('path', { d: path.d, stroke: color, fill: 'none', 'fill-rule': 'evenodd', 'stroke-dasharray': path.guide ? '4 4' : 'none', 'data-layer': path.id, 'data-trace-fill': path.fill || 'none' });
    drawn.style.display = visible ? '' : 'none';
    paths.append(drawn);
    const label = element('label', 'layer-chip');
    label.style.setProperty('--layer-color', color);
    const input = element('input');
    input.type = 'checkbox';
    input.checked = visible;
    input.dataset.layer = path.id;
    input.addEventListener('change', () => {
      state.layers.set(key, input.checked);
      drawn.style.display = input.checked ? '' : 'none';
    });
    const dot = element('span', 'layer-dot');
    dot.setAttribute('aria-hidden', 'true');
    label.append(input, dot, document.createTextNode(path.label));
    controls.append(label);
  });
}

function renderLandmarks(part) {
  const layer = byId('landmark-layer');
  layer.replaceChildren();
  const unit = part.viewBox[2] / 500;
  (part.landmarks || []).forEach((landmark) => {
    const group = svgElement('g', {});
    group.append(svgElement('circle', { cx: landmark.x, cy: landmark.y, r: 2.9 * unit }));
    const label = svgElement('text', { x: landmark.x + 6 * unit, y: landmark.y - 6 * unit, 'font-size': 10 * unit });
    label.textContent = landmark.label;
    group.append(label);
    layer.append(group);
  });
  layer.style.display = byId('show-landmarks').checked ? '' : 'none';
}

function renderDimensions(part) {
  const dimensions = byId('dimensions');
  dimensions.replaceChildren();
  byId('dimensions-section').hidden = !part.dimensions?.length;
  (part.dimensions || []).forEach((dimension) => {
    const row = element('div', 'dimension');
    row.append(element('dt', '', dimension.label), element('dd', '', dimension.value));
    if (dimension.note) row.append(element('dd', 'dimension-note', dimension.note));
    dimensions.append(row);
  });
}

function renderViews(data) {
  const target = byId('reference-views');
  target.replaceChildren();
  (data.views || []).forEach((view, index) => {
    const figure = element('figure', 'view-card');
    const imageContainer = element('div', 'view-image');
    const number = element('span', 'view-index', String(index + 1).padStart(2, '0'));
    number.setAttribute('aria-hidden', 'true');
    const svg = svgElement('svg', { viewBox: view.viewBox.join(' '), role: 'img', 'aria-label': `ภาพต้นแบบ ${view.name}` });
    const defs = svgElement('defs', {});
    const cropId = `view-crop-${index}`;
    const clip = svgElement('clipPath', { id: cropId });
    clip.append(svgElement('rect', { x: view.viewBox[0], y: view.viewBox[1], width: view.viewBox[2], height: view.viewBox[3] }));
    defs.append(clip);
    svg.append(defs, svgElement('image', { href: data.source, width: data.sourceSize[0], height: data.sourceSize[1], preserveAspectRatio: 'none', 'clip-path': `url(#${cropId})` }));
    imageContainer.append(number, svg);
    const caption = element('figcaption');
    caption.append(element('h3', '', view.name), element('p', '', view.note));
    figure.append(imageContainer, caption);
    target.append(figure);
  });
}

async function initialize() {
  const response = await fetch('./reference-map.json', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Reference map: HTTP ${response.status}`);
  const data = await response.json();
  if (!data.parts?.length || !data.sourceSize?.length || !data.source) throw new Error('Incomplete reference map');
  // Wait for the actual source asset as well as the tracing data before declaring ready.
  await new Promise((resolve, reject) => {
    const original = new Image();
    original.onload = resolve;
    original.onerror = () => reject(new Error('Unable to load reference image'));
    original.src = data.source;
  });
  state.data = data;
  if (data.intro) byId('intro-text').textContent = data.intro;
  const source = byId('source-image');
  source.setAttribute('href', data.source);
  source.setAttribute('width', data.sourceSize[0]);
  source.setAttribute('height', data.sourceSize[1]);
  data.parts.forEach((part) => {
    const button = element('button', 'part-tab');
    button.type = 'button';
    button.id = `tab-${part.id}`;
    button.dataset.part = part.id;
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-controls', 'part-panel');
    button.append(element('span', 'tab-number', part.step), document.createTextNode(part.name));
    button.addEventListener('click', () => selectPart(part.id));
    button.addEventListener('keydown', (event) => {
      const current = data.parts.indexOf(part);
      let next;
      if (event.key === 'ArrowRight') next = (current + 1) % data.parts.length;
      if (event.key === 'ArrowLeft') next = (current - 1 + data.parts.length) % data.parts.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = data.parts.length - 1;
      if (next !== undefined) {
        event.preventDefault();
        selectPart(data.parts[next].id, true);
      }
    });
    byId('part-tabs').append(button);
  });
  document.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => setMode(button.dataset.mode)));
  byId('trace-opacity').addEventListener('input', (event) => {
    state.opacity = Number(event.target.value) / 100;
    byId('opacity-value').textContent = `${event.target.value}%`;
    setMode(state.mode);
  });
  byId('drawing-zoom').addEventListener('input', (event) => {
    state.zoom = Number(event.target.value) / 100;
    byId('zoom-value').textContent = `${Number(state.zoom.toFixed(2))}×`;
    updateViewBox();
  });
  byId('show-landmarks').addEventListener('change', (event) => {
    byId('landmark-layer').style.display = event.target.checked && state.mode !== 'reference' ? '' : 'none';
  });
  byId('reset-view').addEventListener('click', resetZoom);
  renderViews(data);
  selectPart(data.parts[0].id);
  byId('workspace').hidden = false;
  byId('load-status').hidden = true;
  api.ready = true;
}

initialize().catch((error) => {
  const status = byId('load-status');
  status.textContent = 'เปิดแบบศึกษาไม่ได้ กรุณารีเฟรชหน้าและตรวจว่าไฟล์ภาพกับข้อมูลอ้างอิงยังอยู่ครบ';
  status.classList.add('error');
  console.error(error);
});
