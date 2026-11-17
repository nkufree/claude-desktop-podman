'use strict';
const $ = (q) => document.querySelector(q);
const form = $('#config-form');
let state, loaded = false, dirty = false, polling = false, activeView = 'overview';
const csrf = $('meta[name="csrf-token"]').content;
const operationNames = {start: '正在启动桌面', stop: '正在停止', rebuild: '正在构建镜像', machine: '正在准备虚拟机', setup: '正在配置 Podman 环境', logs: '正在读取日志', save: '正在保存容器'};

const passwordField = $('#keyring-password');
const passwordToggle = $('#toggle-password');
let passwordPinned = false, passwordHovered = false;
function renderPassword() {
  const visible = passwordPinned || passwordHovered;
  passwordField.type = visible ? 'text' : 'password';
  passwordToggle.textContent = visible ? '隐藏' : '显示';
  passwordToggle.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
  passwordToggle.setAttribute('aria-pressed', String(visible));
}
passwordToggle.addEventListener('pointerenter', event => {
  if (event.pointerType === 'mouse') { passwordHovered = true; renderPassword(); }
});
passwordToggle.addEventListener('pointerleave', () => { passwordHovered = false; renderPassword(); });
passwordToggle.addEventListener('click', () => {
  passwordPinned = !passwordPinned;
  passwordHovered = false;
  renderPassword();
});
function hidePassword() { passwordPinned = false; passwordHovered = false; renderPassword(); }
passwordToggle.addEventListener('keydown', event => { if (event.key === 'Escape') hidePassword(); });
window.addEventListener('blur', hidePassword);
document.addEventListener('visibilitychange', () => { if (document.hidden) hidePassword(); });

function markDirty() { dirty = true; $('#save-state').textContent = '有未保存的更改'; }
function addMount(mount = {source: '', target: '', readonly: false}) {
  const row = $('#mount-template').content.firstElementChild.cloneNode(true);
  row.querySelector('.mount-source').value = mount.source;
  row.querySelector('.mount-target').value = mount.target;
  row.querySelector('.mount-readonly').checked = mount.readonly;
  row.querySelector('.remove-mount').addEventListener('click', () => { row.remove(); markDirty(); });
  $('#custom-mounts').append(row);
}
$('#add-mount').addEventListener('click', () => { addMount(); markDirty(); });
function addPort(mapping = {host: '', container: '', protocol: 'tcp'}) {
  const row = $('#port-template').content.firstElementChild.cloneNode(true);
  row.querySelector('.port-host').value = mapping.host;
  row.querySelector('.port-container').value = mapping.container;
  row.querySelector('.port-protocol').value = mapping.protocol;
  row.querySelector('.remove-port').addEventListener('click', () => { row.remove(); markDirty(); });
  $('#custom-ports').append(row);
}
$('#add-port').addEventListener('click', () => { addPort(); markDirty(); });

async function api(path, data) {
  const response = await fetch(path, {headers: data ? {'Content-Type': 'application/json', 'X-CSRF-Token': csrf} : {}, method: data ? 'POST' : 'GET', body: data ? JSON.stringify(data) : undefined});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
function notice(text, error = false) {
  $('#notice').textContent = text;
  $('#notice').className = `notice${error ? ' error' : ''}`;
  $('#notice').hidden = !text;
}
function readConfig() {
  const values = Object.fromEntries(new FormData(form));
  values.cpus = Number(values.cpus);
  values.memory = Number(values.memory);
  values.mounts = [...document.querySelectorAll('.mount-row')].map(row => ({
    source: row.querySelector('.mount-source').value.trim(),
    target: row.querySelector('.mount-target').value.trim(),
    readonly: row.querySelector('.mount-readonly').checked,
  }));
  values.ports = [...document.querySelectorAll('.port-row')].map(row => ({
    host: Number(row.querySelector('.port-host').value),
    container: Number(row.querySelector('.port-container').value),
    protocol: row.querySelector('.port-protocol').value,
  }));
  return values;
}
async function saveConfig() {
  if (!form.reportValidity()) throw new Error('请检查配置字段');
  // Disabled fieldsets are omitted by FormData, so collect before disabling.
  const values = readConfig();
  await api('/api/config', values);
  dirty = false;
  $('#save-state').textContent = '配置已保存';
}
function connect(force = false) {
  const frame = $('#desktop');
  const ready = state?.ready;
  $('#desktop-empty').hidden = ready;
  frame.hidden = !ready;
  if (ready && (!frame.getAttribute('src') || force)) {
    frame.src = '/desktop/vnc.html?autoconnect=1&resize=scale&reconnect=1&reconnect_delay=2000&path=desktop%2Fwebsockify';
  } else if (!ready && frame.getAttribute('src')) frame.removeAttribute('src');
}
function view(name) {
  hidePassword();
  activeView = name;
  document.querySelectorAll('.view').forEach(e => e.hidden = e.id !== `view-${name}`);
  document.querySelectorAll('.nav').forEach(e => e.classList.toggle('active', e.dataset.view === name));
  $('#page-title').textContent = {overview: '概览与配置', desktop: '远程桌面', logs: '运行日志'}[name];
  if (name === 'desktop') connect();
}
function populateConfig(values) {
  if (!loaded) {
    for (const [key, value] of Object.entries(values)) {
      if (key === 'mounts') { value.forEach(addMount); continue; }
      if (key === 'ports') { value.forEach(addPort); continue; }
      const field = form.elements.namedItem(key);
      if (!field) continue;
      // Config files can contain valid resource values beyond the presets.
      if (field.tagName === 'SELECT' && ![...field.options].some(o => o.value === String(value))) field.add(new Option(String(value), value));
      field.value = value;
    }
    loaded = true;
  }
}
function render(next) {
  const previous = state;
  state = next;
  populateConfig(state.config);
  const env = state.environment;
  $('#engine-status').textContent = env.connected ? 'Podman 已连接' : env.installed ? '等待启动引擎' : '尚未安装 Podman';
  $('#engine-detail').textContent = env.version ? `Podman ${env.version}` : 'Podman · Rootless';
  $('#desktop-detail').textContent = `英文界面 · ${state.config.timezone}`;
  $('#engine-warning').hidden = env.connected;
  $('#engine-help').textContent = env.installed ? (env.platform === 'Darwin' ? 'macOS 需要启动 Podman machine。新虚拟机默认分配 4 核、6 GB 内存。' : '请检查 Podman 服务与当前用户权限。') : '请在终端安装：brew install podman（macOS），或使用 Linux 发行版包管理器。';
  $('#engine-error').textContent = env.message;
  $('#machine-button').hidden = !env.installed || env.platform !== 'Darwin';
  $('#config-fields').disabled = state.busy;
  $('#save-button').disabled = state.busy;
  const label = state.busy ? operationNames[state.operation] : {running: '运行中', starting: '桌面准备中', stopped: '未启动', exited: '已停止'}[state.state];
  $('#state-badge').textContent = label;
  $('#state-badge').className = `badge ${state.ready ? 'running' : state.busy ? 'working' : ''}`;
  $('#desktop-dot').classList.toggle('online', state.ready);
  $('#open-desktop').hidden = !state.ready;
  $('#start-button').hidden = state.ready;
  $('#start-button').textContent = state.busy ? `${label}…` : '保存并启动桌面 ↗';
  $('#launch-description').textContent = state.busy ? '可在运行日志中查看进度' : state.ready ? '桌面在线 · 点击进入工作空间' : '无需导入宿主机登录凭据';
  document.querySelectorAll('[data-action]').forEach(b => {
    b.disabled = state.busy || (!['machine', 'setup'].includes(b.dataset.action) && !env.connected) || (b.dataset.action === 'save' && state.state === 'stopped');
  });
  $('#container-name').textContent = state.container;
  $('#default-volume-name').textContent = state.home_path || state.volume;
  $('#saved-image-info').hidden = !state.saved_image;
  if (state.saved_image) {
    const saved = state.saved_image;
    $('#saved-image-name').textContent = saved.image;
    $('#saved-image-time').textContent = new Date(saved.created_at).toLocaleString();
  }
  $('#log-status').textContent = state.busy ? label : '工作空间日志';
  const output = state.logs.map(l => `${l.time}  ${l.message}`).join('\n');
  const log = $('#logs');
  if (output && output !== log.textContent) {
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 40;
    log.textContent = output;
    if (atBottom) log.scrollTop = log.scrollHeight;
  }
  if (state.error) notice(state.error, true);
  else if (previous?.error) notice('');
  if (activeView === 'desktop') connect();
  if (previous?.busy && !state.busy && state.ready && !state.error && ['start', 'rebuild'].includes(previous.operation)) view('desktop');
}
async function poll() {
  if (polling) return;
  polling = true;
  try { render(await api('/api/status')); }
  catch (e) { notice(`控制台连接失败：${e.message}`, true); }
  finally { polling = false; }
}
form.addEventListener('input', markDirty);
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  try { await saveConfig(); notice('配置已保存；下次启动时生效。修改安装源或版本后请重建镜像。'); }
  catch (e) { notice(e.message, true); }
});
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => view(button.dataset.view)));
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', async () => {
  const action = button.dataset.action;
  if (action === 'start' && state?.ready && !confirm('应用配置并重新创建桌面容器？请先保存桌面中正在编辑的文件，挂载目录会保留。')) return;
  if (action === 'rebuild' && !confirm('将重新构建镜像并替换当前启动镜像及桌面容器。容器内保存的系统改动不会带入新镜像，登录数据卷会保留。继续？')) return;
  if (action === 'stop' && !confirm('停止桌面容器？请先保存桌面内正在编辑的文件。')) return;
  button.disabled = true;
  try {
    if (['start', 'rebuild', 'setup'].includes(action)) await saveConfig();
    notice('');
    await api('/api/action', {action});
    if (['start', 'rebuild', 'machine', 'setup', 'logs', 'save'].includes(action)) view('logs');
    await poll();
  } catch (e) { notice(e.message, true); }
  finally { if (state) render(state); }
}));
$('#reconnect').addEventListener('click', () => connect(true));
$('#fullscreen').addEventListener('click', () => $('#desktop-frame').requestFullscreen?.().catch(e => notice(e.message, true)));
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
async function initialize() {
  try {
    populateConfig((await api('/api/config')).config);
    $('#config-fields').disabled = false;
    $('#save-button').disabled = false;
  } catch (e) { notice(e.message, true); }
  await poll();
}
initialize();
setInterval(poll, 2500);
