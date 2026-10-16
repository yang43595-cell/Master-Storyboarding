import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

let activePreview = null;
const stateNames = {pending: '待载入校验', ready: '可预览', stale: '来源已变更', error: '暂不可用'};

function escapeText(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[character]);
}

function disposeObject(root) {
  if (!root) return;
  const geometries = new Set(), materials = new Set(), textures = new Set();
  root.traverse(object => {
    if (object.geometry) geometries.add(object.geometry);
    for (const material of [].concat(object.material || [])) materials.add(material);
    if (object.skeleton?.boneTexture) textures.add(object.skeleton.boneTexture);
  });
  for (const material of materials) {
    for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
    material.dispose();
  }
  for (const texture of textures) texture.dispose();
  for (const geometry of geometries) geometry.dispose();
}

function validateManifest(manifest) {
  const matrix = value => Array.isArray(value) && value.length === 16 && value.every(Number.isFinite);
  if (!manifest || !Array.isArray(manifest.frames) || !manifest.frames.length ||
      !Number.isFinite(manifest.duration_seconds) || manifest.duration_seconds < 0 ||
      !Number.isFinite(manifest.fps) || manifest.fps <= 0 ||
      !Array.isArray(manifest.resolution) || manifest.resolution.length !== 2 ||
      !manifest.resolution.every(value => Number.isFinite(value) && value > 0)) {
    throw Error('预览的时间轴或画幅信息不完整，请重新检查导出结果。');
  }
  let previous = -1;
  for (const frame of manifest.frames) {
    if (!Number.isFinite(frame.time) || frame.time < 0 || frame.time <= previous ||
        frame.time > manifest.duration_seconds + 0.000001 ||
        !matrix(frame.matrix_world) || !matrix(frame.projection_matrix)) {
      throw Error('预览的摄影机帧信息无效，请重新检查导出结果。');
    }
    previous = frame.time;
  }
  if (manifest.frames[0].time !== 0) throw Error('预览时间轴须从 0 秒开始。');
  return manifest;
}

function sameOriginUrl(value) {
  const url = new URL(value, location.href);
  if (url.origin !== location.origin) throw Error('预览文件须由本地资产库提供。');
  return url;
}

export function openPrevis({projectId, projectName = '当前项目'}) {
  if (!projectId) return;
  activePreview?.close();
  const session = new PrevisSession(projectId, projectName);
  activePreview = session;
  session.open();
}

class PrevisSession {
  constructor(projectId, projectName) {
    this.projectId = projectId;
    this.projectName = projectName;
    this.lifetime = new AbortController();
    this.loadController = null;
    this.requestVersion = 0;
    this.closed = false;
    this.playing = false;
    this.frameIndex = 0;
    this.cameraMode = 'director';
    this.raf = null;
    this.opener = document.activeElement;
  }

  open() {
    this.dialog = document.createElement('dialog');
    this.dialog.id = 'previs-dialog';
    this.dialog.className = 'previs-dialog';
    this.dialog.setAttribute('aria-labelledby', 'previs-title');
    this.dialog.innerHTML = `<div class="dialog-head"><div><h2 id="previs-title">${escapeText(this.projectName)} · 白模预览</h2><p class="previs-heading-note">查看空间、站位与运镜</p></div><button type="button" data-previs-close class="ghost" aria-label="关闭白模预览">✕</button></div>
      <div class="previs-body"><div class="previs-toolbar"><label class="previs-shot-field"><span>镜头</span><select data-previs-shot aria-label="选择白模镜头" disabled><option>正在读取镜头…</option></select></label><span class="previs-status" data-previs-status role="status"></span><button type="button" data-previs-refresh>刷新列表</button></div>
      <div class="previs-views" role="group" aria-label="观察方式"><button type="button" data-previs-view="director" aria-pressed="true" disabled>导演机位</button><button type="button" data-previs-view="free" aria-pressed="false" disabled>自由观察</button><button type="button" data-previs-view="top" aria-pressed="false" disabled>俯视</button><label class="previs-path-toggle"><input type="checkbox" data-previs-paths disabled>辅助路径</label></div>
      <div class="previs-viewport" data-previs-viewport><span class="previs-camera-hint" data-previs-hint hidden></span><div class="previs-message" data-previs-message role="status" aria-live="polite"><strong>正在读取白模</strong><p>稍候即可选择镜头。</p></div></div>
      <div class="previs-transport"><button type="button" class="previs-play" data-previs-play disabled>播放</button><div class="previs-jumps" role="group" aria-label="跳转到镜头起止"><button type="button" data-previs-jump="start" disabled>起幅</button><button type="button" data-previs-jump="end" disabled>落幅</button></div><input type="range" class="previs-timeline" data-previs-timeline min="0" max="0" step="1" value="0" disabled aria-label="白模播放时间轴"><output class="previs-time" data-previs-time>0.00 / 0.00 秒</output></div>
      <div class="previs-legend" data-previs-legend aria-label="人物与认领色"></div><div class="previs-footer"><span data-previs-info></span><span>只读预览 · 原文件与确认状态保持不变</span></div></div>`;
    document.body.append(this.dialog);
    this.find = selector => this.dialog.querySelector(selector);
    this.viewport = this.find('[data-previs-viewport]');
    this.selector = this.find('[data-previs-shot]');
    this.timeline = this.find('[data-previs-timeline]');
    const signal = this.lifetime.signal;
    this.find('[data-previs-close]').addEventListener('click', () => this.close(), {signal});
    this.dialog.addEventListener('close', () => this.close(), {signal});
    this.find('[data-previs-refresh]').addEventListener('click', () => this.loadList(), {signal});
    this.selector.addEventListener('change', () => this.loadShot(this.selector.value), {signal});
    this.find('[data-previs-play]').addEventListener('click', () => this.togglePlayback(), {signal});
    for (const button of this.dialog.querySelectorAll('[data-previs-jump]')) {
      button.addEventListener('click', () => {
        if (!this.manifest) return;
        this.setPlaying(false);
        this.seek(button.dataset.previsJump === 'start' ? 0 : this.manifest.frames.length - 1);
      }, {signal});
    }
    this.timeline.addEventListener('input', () => {
      this.setPlaying(false);
      this.seek(Number(this.timeline.value));
    }, {signal});
    for (const button of this.dialog.querySelectorAll('[data-previs-view]')) {
      button.addEventListener('click', () => this.setCameraMode(button.dataset.previsView), {signal});
    }
    this.find('[data-previs-paths]').addEventListener('change', event => {
      if (event.target.checked && !this.paths) this.buildPaths();
      if (this.paths) this.paths.visible = event.target.checked;
      this.render();
    }, {signal});
    window.addEventListener('previs-project-change', event => {
      if (event.detail?.projectId !== this.projectId) this.close();
    }, {signal});
    window.addEventListener('pagehide', () => this.close(), {signal});
    this.dialog.showModal();
    this.loadList();
  }

  async request(previewId, signal) {
    const query = new URLSearchParams({project_id: this.projectId});
    if (previewId) query.set('preview_id', previewId);
    const response = await fetch(`/api/previs?${query}`, {signal, cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw Error(data.error || '读取白模失败，请稍后刷新。');
    return data;
  }

  beginRequest() {
    this.loadController?.abort();
    this.loadController = new AbortController();
    this.clearViewer();
    this.setControls(false);
    return {version: ++this.requestVersion, signal: this.loadController.signal};
  }

  isCurrent(version) {
    return !this.closed && version === this.requestVersion;
  }

  async loadList() {
    const selected = this.selector.value;
    const {version, signal} = this.beginRequest();
    this.selector.disabled = true;
    this.showMessage('正在读取白模', '读取项目中已有的镜头预览。');
    try {
      const data = await this.request(null, signal);
      if (!this.isCurrent(version)) return;
      this.previews = Array.isArray(data.previews) ? data.previews : [];
      this.selector.replaceChildren();
      if (!this.previews.length) {
        this.selector.add(new Option('暂无白模镜头', ''));
        this.showMessage('还没有白模预览', '此项目暂无已导出的三维预览。');
        this.setStatus('');
        return;
      }
      for (const preview of this.previews) {
        this.selector.add(new Option(`${preview.shot_id || preview.id} · ${preview.revision || ''} · ${stateNames[preview.status] || '待检查'}`, preview.id));
      }
      this.selector.disabled = false;
      this.selector.value = this.previews.some(item => item.id === selected) ? selected :
        (this.previews.find(item => ['pending', 'ready'].includes(item.status)) || this.previews[0]).id;
      await this.loadShot(this.selector.value);
    } catch (error) {
      if (error.name !== 'AbortError' && this.isCurrent(version)) {
        this.selector.replaceChildren(new Option('未能读取镜头', ''));
        this.showMessage('读取失败', error.message);
        this.setStatus('error');
      }
    }
  }

  async loadShot(previewId) {
    if (!previewId || this.closed) return;
    const {version, signal} = this.beginRequest();
    this.setStatus('');
    this.showMessage('正在载入镜头', '准备三维场景与摄影机时间轴。');
    let gltf = null;
    try {
      const data = await this.request(previewId, signal);
      if (!this.isCurrent(version)) return;
      this.setStatus(['pending', 'ready'].includes(data.status) ? 'pending' : data.status);
      if (!['pending', 'ready'].includes(data.status)) {
        this.showMessage(data.status === 'stale' ? '这个预览的来源已变更' : '此镜头暂不可预览',
          data.error || (data.status === 'stale' ? '请检查并重新导出对应镜头，再刷新列表。' : '请检查此镜头的导出结果，再刷新列表。'));
        return;
      }
      const manifest = validateManifest(data.manifest);
      if (!data.glb_url) throw Error('三维预览文件缺失，请检查导出结果。');
      const url = sameOriginUrl(data.glb_url);
      const response = await fetch(url, {signal, cache: 'no-store'});
      if (!this.isCurrent(version)) return;
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}));
        throw Error(failure.error || '三维文件读取失败，请检查文件是否仍然存在。');
      }
      const buffer = await response.arrayBuffer();
      if (!this.isCurrent(version)) return;
      gltf = await new GLTFLoader().parseAsync(buffer, new URL('.', url).href);
      if (!this.isCurrent(version)) { disposeObject(gltf.scene); return; }
      this.mountViewer(gltf, manifest);
      this.setStatus('ready');
      this.setControls(true);
      this.find('[data-previs-message]').hidden = true;
      this.seek(0);
    } catch (error) {
      if (gltf && gltf.scene !== this.model) disposeObject(gltf.scene);
      if (error.name !== 'AbortError' && this.isCurrent(version)) {
        this.clearViewer();
        this.setControls(false);
        this.setStatus('error');
        this.showMessage('载入失败', error.message);
      }
    }
  }

  mountViewer(gltf, manifest) {
    this.manifest = manifest;
    this.model = gltf.scene;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#232629');
    this.scene.add(this.model);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x7c8288, 2.1));
    const light = new THREE.DirectionalLight(0xffffff, 2.3);
    light.position.set(5, 9, 4);
    this.scene.add(light);
    this.renderer = new THREE.WebGLRenderer({antialias: true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor('#08090b');
    this.renderer.domElement.setAttribute('aria-label', '白模三维场景');
    this.viewport.prepend(this.renderer.domElement);
    this.viewerLifetime = new AbortController();
    this.renderer.domElement.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      this.setPlaying(false);
      this.showMessage('三维显示已中断', '请刷新列表重新载入镜头。');
    }, {signal: this.viewerLifetime.signal});
    this.directorCamera = new THREE.PerspectiveCamera();
    this.directorCamera.matrixAutoUpdate = false;
    this.freeCamera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
    this.topCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, 1000);
    this.controls = new OrbitControls(this.freeCamera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.addEventListener('change', () => this.render());
    this.bounds = this.sceneBounds(manifest);
    this.frameFreeCamera();
    if (gltf.animations.length) {
      this.mixer = new THREE.AnimationMixer(this.model);
      this.action = this.mixer.clipAction(gltf.animations[0]);
      this.action.setLoop(THREE.LoopOnce, 1);
      this.action.clampWhenFinished = true;
      this.action.play();
    }
    this.actorRoots = (manifest.actors || []).map(actor => {
      let root = null;
      this.model.traverse(object => {
        if (!root && object.userData?.previs_actor_id === actor.id) root = object;
      });
      return {actor, root};
    });
    this.timeline.max = String(manifest.frames.length - 1);
    this.timeline.value = '0';
    this.frameIndex = 0;
    this.find('[data-previs-paths]').checked = false;
    this.renderLegend(manifest.actors || []);
    this.find('[data-previs-info]').textContent = `${manifest.resolution[0]} × ${manifest.resolution[1]} · ${manifest.fps} 帧/秒`;
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.viewport);
    this.setCameraMode('director');
    this.resize();
  }

  sceneBounds(manifest) {
    const min = manifest.bounds?.min, max = manifest.bounds?.max;
    if (Array.isArray(min) && Array.isArray(max) && min.length === 3 && max.length === 3 &&
        [...min, ...max].every(Number.isFinite) && min.every((value, index) => value <= max[index])) {
      return new THREE.Box3(new THREE.Vector3(...min), new THREE.Vector3(...max));
    }
    const box = new THREE.Box3().setFromObject(this.model);
    return box.isEmpty() ? new THREE.Box3(new THREE.Vector3(-1, 0, -1), new THREE.Vector3(1, 2, 1)) : box;
  }

  frameFreeCamera() {
    const center = this.bounds.getCenter(new THREE.Vector3());
    const size = this.bounds.getSize(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 0.1);
    this.freeCamera.near = Math.max(radius / 1000, 0.001);
    this.freeCamera.far = radius * 100 + 10;
    this.freeCamera.position.copy(center).add(new THREE.Vector3(radius * 1.6, radius * 1.1, radius * 1.6));
    this.controls.target.copy(center);
    this.controls.minDistance = radius * 0.025;
    this.controls.maxDistance = radius * 20;
    this.controls.update();
    this.topCamera.up.set(0, 0, -1);
    this.topCamera.position.copy(center).add(new THREE.Vector3(0, radius * 3 + 1, 0));
    this.topCamera.lookAt(center);
    this.topCamera.far = radius * 20 + 10;
  }

  resize() {
    if (!this.renderer || this.closed) return;
    this.width = Math.max(1, this.viewport.clientWidth);
    this.height = Math.max(1, this.viewport.clientHeight);
    this.renderer.setSize(this.width, this.height, false);
    const aspect = this.width / this.height;
    this.freeCamera.aspect = aspect;
    this.freeCamera.updateProjectionMatrix();
    const size = this.bounds.getSize(new THREE.Vector3());
    const halfHeight = Math.max(size.z * 0.58, size.x * 0.58 / aspect, 0.1);
    this.topCamera.left = -halfHeight * aspect;
    this.topCamera.right = halfHeight * aspect;
    this.topCamera.top = halfHeight;
    this.topCamera.bottom = -halfHeight;
    this.topCamera.updateProjectionMatrix();
    this.render();
  }

  setCameraMode(mode) {
    this.cameraMode = mode;
    if (this.controls) this.controls.enabled = mode === 'free';
    for (const button of this.dialog.querySelectorAll('[data-previs-view]')) {
      button.setAttribute('aria-pressed', String(button.dataset.previsView === mode));
    }
    const hint = this.find('[data-previs-hint]');
    hint.hidden = !this.renderer;
    hint.textContent = mode === 'director' ? '导演机位 · 按项目画幅' :
      mode === 'top' ? '俯视 · 观察站位' : '拖动旋转 · 滚轮缩放 · 右键平移';
    this.render();
  }

  applyAnimation(time) {
    if (this.mixer) {
      // Reset the action so backwards scrubbing also works after the terminal hold.
      this.action.reset().play();
      this.mixer.setTime(time);
    }
    this.model?.updateMatrixWorld(true);
  }

  seek(index) {
    if (!this.manifest) return;
    this.frameIndex = Math.min(this.manifest.frames.length - 1, Math.max(0, Math.round(index)));
    const frame = this.manifest.frames[this.frameIndex];
    this.applyAnimation(frame.time);
    const camera = this.directorCamera;
    camera.matrix.fromArray(frame.matrix_world);
    camera.matrixWorld.copy(camera.matrix);
    camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
    camera.projectionMatrix.fromArray(frame.projection_matrix);
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
    this.timeline.value = String(this.frameIndex);
    this.timeline.setAttribute('aria-valuetext', `${frame.time.toFixed(2)} 秒`);
    this.find('[data-previs-time]').textContent = `${frame.time.toFixed(2)} / ${this.manifest.duration_seconds.toFixed(2)} 秒`;
    this.render();
  }

  render() {
    if (!this.renderer || !this.manifest || this.closed) return;
    const width = this.width || 1, height = this.height || 1;
    this.renderer.setScissorTest(false);
    this.renderer.setViewport(0, 0, width, height);
    this.renderer.clear();
    let camera = this.cameraMode === 'free' ? this.freeCamera : this.topCamera;
    if (this.cameraMode === 'director') {
      camera = this.directorCamera;
      const aspect = this.manifest.resolution[0] / this.manifest.resolution[1];
      const viewWidth = Math.min(width, height * aspect), viewHeight = viewWidth / aspect;
      const x = (width - viewWidth) / 2, y = (height - viewHeight) / 2;
      this.renderer.setViewport(x, y, viewWidth, viewHeight);
      this.renderer.setScissor(x, y, viewWidth, viewHeight);
      this.renderer.setScissorTest(true);
    }
    this.renderer.render(this.scene, camera);
    this.renderer.setScissorTest(false);
  }

  togglePlayback() {
    if (!this.manifest) return;
    if (this.playing) { this.setPlaying(false); return; }
    if (this.frameIndex === this.manifest.frames.length - 1) this.seek(0);
    this.playing = true;
    this.find('[data-previs-play]').textContent = '暂停';
    const started = performance.now();
    const startTime = this.manifest.frames[this.frameIndex].time;
    const tick = now => {
      if (!this.playing || this.closed || !this.manifest) return;
      const time = Math.min(this.manifest.duration_seconds, startTime + (now - started) / 1000);
      let low = 0, high = this.manifest.frames.length - 1;
      while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        if (this.manifest.frames[middle].time <= time + 0.000001) low = middle;
        else high = middle - 1;
      }
      if (low !== this.frameIndex) this.seek(low);
      if (time >= this.manifest.duration_seconds) {
        this.seek(this.manifest.frames.length - 1);
        this.setPlaying(false);
      } else this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  setPlaying(value) {
    this.playing = value;
    if (!value && this.raf !== null) { cancelAnimationFrame(this.raf); this.raf = null; }
    if (this.dialog) this.find('[data-previs-play]').textContent = value ? '暂停' : '播放';
  }

  buildPaths() {
    if (!this.model || this.paths) return;
    this.paths = new THREE.Group();
    const tracks = this.actorRoots.filter(item => item.root).map(item => ({...item, points: []}));
    const currentTime = this.manifest.frames[this.frameIndex].time;
    for (const frame of this.manifest.frames) {
      this.applyAnimation(frame.time);
      for (const track of tracks) track.points.push(track.root.getWorldPosition(new THREE.Vector3()));
    }
    this.applyAnimation(currentTime);
    for (const track of tracks) {
      const color = /^#[0-9a-f]{6}$/i.test(track.actor.color || '') ? track.actor.color : '#e7e7df';
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(track.points),
        new THREE.LineBasicMaterial({color, transparent: true, opacity: 0.8}));
      line.name = `辅助路径 · ${track.actor.label || track.actor.id}`;
      this.paths.add(line);
    }
    this.scene.add(this.paths);
  }

  renderLegend(actors) {
    const legend = this.find('[data-previs-legend]');
    legend.replaceChildren();
    for (const actor of actors) {
      const item = document.createElement('span');
      item.className = 'previs-person';
      const swatch = document.createElement('span');
      swatch.className = 'previs-swatch';
      swatch.style.background = /^#[0-9a-f]{6}$/i.test(actor.color || '') ? actor.color : '#b3b3aa';
      swatch.setAttribute('aria-hidden', 'true');
      item.append(swatch, document.createTextNode(actor.label || actor.id));
      legend.append(item);
    }
  }

  setControls(enabled) {
    for (const control of this.dialog.querySelectorAll('[data-previs-view],[data-previs-paths],[data-previs-play],[data-previs-jump],[data-previs-timeline]')) {
      control.disabled = !enabled;
    }
  }

  setStatus(status) {
    const label = this.find('[data-previs-status]');
    label.dataset.state = status || '';
    label.textContent = stateNames[status] || '';
  }

  showMessage(title, text) {
    const message = this.find('[data-previs-message]');
    message.querySelector('strong').textContent = title;
    message.querySelector('p').textContent = text;
    message.hidden = false;
  }

  clearViewer() {
    this.setPlaying(false);
    this.viewerLifetime?.abort();
    this.viewerLifetime = null;
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.controls?.dispose();
    this.controls = null;
    if (this.mixer) { this.mixer.stopAllAction(); this.mixer.uncacheRoot(this.model); }
    this.mixer = null;
    this.action = null;
    disposeObject(this.model);
    disposeObject(this.paths);
    this.model = null;
    this.paths = null;
    this.scene = null;
    this.manifest = null;
    this.actorRoots = [];
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
      this.renderer.forceContextLoss();
      this.renderer = null;
    }
    if (this.dialog) {
      this.find('[data-previs-hint]').hidden = true;
      this.find('[data-previs-legend]').replaceChildren();
      this.find('[data-previs-info]').textContent = '';
      this.find('[data-previs-time]').textContent = '0.00 / 0.00 秒';
      this.timeline.value = '0';
    }
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.requestVersion++;
    this.lifetime.abort();
    this.loadController?.abort();
    this.clearViewer();
    if (this.dialog.open) this.dialog.close();
    this.dialog.remove();
    if (activePreview === this) activePreview = null;
    if (this.opener?.isConnected) this.opener.focus();
  }
}
