import * as THREE from 'three';
import { VRMLoaderPlugin, VRMExpressionPresetName } from '@pixiv/three-vrm';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

const VISEME_BANDS = [
  { name: 'A', lo: 200, hi: 500 },
  { name: 'I', lo: 500, hi: 1000 },
  { name: 'U', lo: 1000, hi: 2000 },
  { name: 'E', lo: 200, hi: 800 },
  { name: 'O', lo: 200, hi: 400 },
];

const VISEME_KEYS = ['A', 'I', 'U', 'E', 'O'];

export class VRMScene {
  constructor() {
    this.container = null;
    this.renderer = null;
    this.scene = null;
    this.camera = null;
    this.vrm = null;
    this.clock = new THREE.Clock();
    this.animationId = null;
    this._destroyed = false;

    this._modelBaseY = 0;
    this.cameraDistance = 2.0;
    this._camTheta = 0;
    this._camPhi = 0.45;
    this._camTarget = new THREE.Vector3(0, 0.6, 0);

    this.breathTime = 0;
    this.expressionTimer = 0;
    this.currentExpression = 'neutral';

    this.lipSyncActive = false;
    this.lipSyncAnalyser = null;
    this.lipSyncData = null;
    this.lipSyncWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };
    this.prevLipSyncWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };
    this.lipSyncSmoothFactor = 0.3;

    this._pointerDown = false;
    this._pointerStart = { x: 0, y: 0 };
    this._camStart = { theta: 0, phi: 0 };
    this._initialized = false;

    this._pixiCanvas = null;
    this._canvasVisible = true;
  }

  init(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
    });
    this.renderer.setPixelRatio(window.devicePixelRatio || 1);
    this.renderer.setSize(window.innerWidth, window.innerHeight);
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    const canvas = this.renderer.domElement;
    canvas.style.position = 'absolute';
    canvas.style.top = '0';
    canvas.style.left = '0';
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.pointerEvents = 'auto';
    canvas.style.zIndex = '1';
    container.appendChild(canvas);

    this.scene = new THREE.Scene();

    this.camera = new THREE.PerspectiveCamera(25, window.innerWidth / window.innerHeight, 0.1, 20);
    this._updateCameraPosition();

    const ambient = new THREE.AmbientLight(0xffffff, 0.7);
    this.scene.add(ambient);

    const mainLight = new THREE.DirectionalLight(0xffffff, 1.2);
    mainLight.position.set(1, 2, 2);
    this.scene.add(mainLight);

    const fillLight = new THREE.DirectionalLight(0x8888ff, 0.5);
    fillLight.position.set(-1, 1, -1.5);
    this.scene.add(fillLight);

    const rimLight = new THREE.DirectionalLight(0xffffff, 0.6);
    rimLight.position.set(-0.5, 1.5, -2);
    this.scene.add(rimLight);

    window.addEventListener('resize', this._onResize);
    this._initialized = true;
    this._animate();
  }

  _onResize = () => {
    if (!this.renderer || this._destroyed) return;
    const w = window.innerWidth;
    const h = window.innerHeight;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  _updateCameraPosition() {
    const theta = this._camTheta;
    const phi = Math.max(0.1, Math.min(Math.PI / 2 - 0.05, this._camPhi));
    const dist = this.cameraDistance;
    const x = dist * Math.sin(phi) * Math.sin(theta);
    const y = dist * Math.cos(phi);
    const z = dist * Math.sin(phi) * Math.cos(theta);
    this.camera.position.set(
      this._camTarget.x + x,
      this._camTarget.y + y,
      this._camTarget.z + z,
    );
    this.camera.lookAt(this._camTarget);
  }

  getCanvas() {
    return this.renderer ? this.renderer.domElement : null;
  }

  getBounds() {
    if (!this.vrm || !this.camera || !this.renderer) {
      return { x: window.innerWidth * 0.5 - 150, y: 0, width: 300, height: window.innerHeight };
    }
    const box = new THREE.Box3().setFromObject(this.vrm.scene);
    const corners = [
      new THREE.Vector3(box.min.x, box.min.y, box.min.z),
      new THREE.Vector3(box.max.x, box.min.y, box.min.z),
      new THREE.Vector3(box.min.x, box.max.y, box.min.z),
      new THREE.Vector3(box.max.x, box.max.y, box.min.z),
      new THREE.Vector3(box.min.x, box.min.y, box.max.z),
      new THREE.Vector3(box.max.x, box.min.y, box.max.z),
      new THREE.Vector3(box.min.x, box.max.y, box.max.z),
      new THREE.Vector3(box.max.x, box.max.y, box.max.z),
    ];
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const w = this.renderer.domElement.clientWidth || window.innerWidth;
    const h = this.renderer.domElement.clientHeight || window.innerHeight;
    for (const corner of corners) {
      const vec = corner.clone().project(this.camera);
      const sx = (vec.x * 0.5 + 0.5) * w;
      const sy = (-vec.y * 0.5 + 0.5) * h;
      if (vec.z < 1) {
        if (sx < minX) minX = sx;
        if (sx > maxX) maxX = sx;
        if (sy < minY) minY = sy;
        if (sy > maxY) maxY = sy;
      }
    }
    if (!Number.isFinite(minX) || !Number.isFinite(maxX) || !Number.isFinite(minY) || !Number.isFinite(maxY)) {
      return { x: window.innerWidth * 0.25, y: 0, width: window.innerWidth * 0.5, height: window.innerHeight * 0.8 };
    }
    const padX = 20;
    const padY = 10;
    return { x: minX - padX, y: minY - padY, width: maxX - minX + padX * 2, height: maxY - minY + padY * 2 };
  }

  getCanvasRect() {
    if (!this.renderer) return new DOMRect(0, 0, window.innerWidth, window.innerHeight);
    return this.renderer.domElement.getBoundingClientRect();
  }

  hitTest(clientX, clientY) {
    if (!this.renderer || !this.vrm) return false;
    const rect = this.renderer.domElement.getBoundingClientRect();
    const nx = ((clientX - rect.left) / rect.width) * 2 - 1;
    const ny = -((clientY - rect.top) / rect.height) * 2 + 1;
    const raycaster = new THREE.Raycaster();
    raycaster.setFromCamera(new THREE.Vector2(nx, ny), this.camera);
    const meshes = [];
    this.vrm.scene.traverse((child) => {
      if (child.isMesh) meshes.push(child);
    });
    const hits = raycaster.intersectObjects(meshes, false);
    return hits.length > 0;
  }

  pointerDown(clientX, clientY) {
    this._pointerDown = true;
    this._pointerStart = { x: clientX, y: clientY };
    this._camStart = { theta: this._camTheta, phi: this._camPhi };
    this._modelStart = this.vrm ? {
      x: this.vrm.scene.position.x,
      y: this._modelBaseY,
    } : { x: 0, y: 0 };
  }

  orbitCamera(clientX, clientY) {
    if (!this._pointerDown) return;
    const dx = (clientX - this._pointerStart.x) * 0.005;
    const dy = (clientY - this._pointerStart.y) * 0.005;
    this._camTheta = this._camStart.theta + dx;
    this._camPhi = Math.max(0.1, Math.min(Math.PI / 2 - 0.05, this._camStart.phi + dy));
    this._updateCameraPosition();
  }

  moveModel(clientX, clientY) {
    if (!this._pointerDown || !this.vrm) return;
    const dx = (clientX - this._pointerStart.x) / window.innerWidth;
    const dy = (clientY - this._pointerStart.y) / window.innerHeight;
    const sensitivity = 2.0;
    this.vrm.scene.position.x = this._modelStart.x + dx * sensitivity;
    this._modelBaseY = this._modelStart.y - dy * sensitivity;
  }

  pointerUp() {
    this._pointerDown = false;
  }

  setScale(s) {
    const clamped = Math.max(0.1, Math.min(2.0, s));
    this.cameraDistance = 5.0 - (clamped - 0.1) * (4.5 / 1.9);
    this._updateCameraPosition();
  }

  getScale() {
    return 0.1 + (5.0 - this.cameraDistance) * (1.9 / 4.5);
  }

  async loadVRM(path) {
    if (this.vrm) {
      this.scene.remove(this.vrm.scene);
      this.vrm = null;
    }
    const response = await fetch(path);
    if (!response.ok) throw new Error(`HTTP ${response.status} loading VRM`);
    const data = await response.arrayBuffer();
    const gltfLoader = new GLTFLoader();
    gltfLoader.register((parser) => new VRMLoaderPlugin(parser));
    const gltf = await new Promise((resolve, reject) => {
      gltfLoader.parse(data, path, (gltf) => resolve(gltf), reject);
    });
    this.vrm = gltf.userData.vrm;
    if (!this.vrm) throw new Error('Failed to load VRM: no VRM data in glTF');
    this.scene.add(this.vrm.scene);
    this.vrm.scene.position.set(0, 0, 0);
    this.vrm.scene.rotation.y = Math.PI;
    this._modelBaseY = 0;
    this._updateCameraPosition();
    this.currentExpression = 'neutral';
    this.breathTime = 0;
    this.expressionTimer = 0;
  }

  getAvailableExpressions() {
    return ['neutral', 'happy', 'angry', 'sad', 'relaxed', 'surprised'];
  }

  setExpression(name) {
    if (!this.vrm || !this.vrm.expressionManager) return false;
    const presetMap = {
      neutral: VRMExpressionPresetName.Neutral,
      happy: VRMExpressionPresetName.Happy,
      angry: VRMExpressionPresetName.Angry,
      sad: VRMExpressionPresetName.Sad,
      relaxed: VRMExpressionPresetName.Relaxed,
      surprised: VRMExpressionPresetName.Surprised,
    };
    const preset = presetMap[name.toLowerCase()];
    if (preset === undefined) return false;
    this.currentExpression = name;
    return true;
  }

  startLipSync(analyser) {
    this.lipSyncActive = true;
    this.lipSyncAnalyser = analyser;
    this.lipSyncData = new Uint8Array(analyser.frequencyBinCount);
    this.lipSyncWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };
    this.prevLipSyncWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };

    this._lipSyncBoundTick = () => this._lipSyncTick();
    const tick = () => {
      if (!this.lipSyncActive) return;
      this._lipSyncTick();
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  stopLipSync() {
    this.lipSyncActive = false;
    this.lipSyncWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };
    this.prevLipSyncWeights = { A: 0, I: 0, U: 0, E: 0, O: 0 };
  }

  _lipSyncTick() {
    if (!this.lipSyncActive || !this.lipSyncAnalyser || !this.lipSyncData || !this.vrm) return;
    this.lipSyncAnalyser.getByteFrequencyData(this.lipSyncData);
    const sampleRate = this.lipSyncAnalyser.context.sampleRate;
    const fftSize = this.lipSyncAnalyser.fftSize;
    const nyquist = sampleRate / 2;
    const binCount = this.lipSyncData.length;
    const binsPerHz = binCount / nyquist;

    for (const key of VISEME_KEYS) {
      const band = VISEME_BANDS.find((b) => b.name === key);
      if (!band) continue;
      const loBin = Math.max(0, Math.floor(band.lo * binsPerHz));
      const hiBin = Math.min(binCount - 1, Math.ceil(band.hi * binsPerHz));
      let sum = 0;
      let count = 0;
      for (let i = loBin; i <= hiBin && i < binCount; i++) {
        sum += this.lipSyncData[i];
        count++;
      }
      const raw = count > 0 ? sum / count / 255 : 0;
      const prev = this.prevLipSyncWeights[key] || 0;
      this.lipSyncWeights[key] = prev + (raw - prev) * this.lipSyncSmoothFactor;
    }

    for (const key of VISEME_KEYS) {
      if (this.vrm.expressionManager) {
        this.vrm.expressionManager.setValue(
          key === 'A' ? VRMExpressionPresetName.Aa :
          key === 'I' ? VRMExpressionPresetName.Ih :
          key === 'U' ? VRMExpressionPresetName.Ou :
          key === 'E' ? VRMExpressionPresetName.Ee :
          key === 'O' ? VRMExpressionPresetName.Oh :
          null,
          Math.min(0.7, this.lipSyncWeights[key] * 1.5),
        );
      }
      this.prevLipSyncWeights[key] = this.lipSyncWeights[key];
    }
  }

  _animate = () => {
    if (this._destroyed) return;
    this.animationId = requestAnimationFrame(this._animate);

    const delta = this.clock.getDelta();

    if (this.vrm) {
      this.breathTime += delta;
      const breath = Math.sin(this.breathTime * 1.5) * 0.005;
      this.vrm.scene.position.y = this._modelBaseY + breath;

      if (this.vrm.expressionManager) {
        this.vrm.expressionManager.setValue(
          VRMExpressionPresetName.Neutral,
          this.currentExpression === 'neutral' ? 1 : 0,
        );
        this.vrm.expressionManager.setValue(
          VRMExpressionPresetName.Happy,
          this.currentExpression === 'happy' ? 1 : 0,
        );
        this.vrm.expressionManager.setValue(
          VRMExpressionPresetName.Angry,
          this.currentExpression === 'angry' ? 1 : 0,
        );
        this.vrm.expressionManager.setValue(
          VRMExpressionPresetName.Sad,
          this.currentExpression === 'sad' ? 1 : 0,
        );
        this.vrm.expressionManager.setValue(
          VRMExpressionPresetName.Relaxed,
          this.currentExpression === 'relaxed' ? 1 : 0,
        );
        this.vrm.expressionManager.setValue(
          VRMExpressionPresetName.Surprised,
          this.currentExpression === 'surprised' ? 1 : 0,
        );
      }

      if (this.vrm.update) {
        this.vrm.update(delta);
      }
    }

    if (this.renderer && this.scene && this.camera) {
      this.renderer.render(this.scene, this.camera);
    }
  }

  destroy() {
    this._destroyed = true;
    this.stopLipSync();
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }
    if (this.vrm) {
      this.scene.remove(this.vrm.scene);
      this.vrm = null;
    }
    if (this.renderer) {
      this.renderer.dispose();
      if (this.renderer.domElement && this.renderer.domElement.parentNode) {
        this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
      }
      this.renderer = null;
    }
    window.removeEventListener('resize', this._onResize);
    this.scene = null;
    this.camera = null;
    this._initialized = false;
  }

  get isActive() {
    return this._initialized && !this._destroyed && this.vrm !== null;
  }
}
