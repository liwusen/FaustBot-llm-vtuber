import * as THREE from 'three';
import { VRMLoaderPlugin, VRMExpressionPresetName, VRMHumanBoneName } from '@pixiv/three-vrm';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

const GESTURES = {
  nod: { targets: [{ bone: 'head', x: 0.35 }] },
  shake_head: { targets: [{ bone: 'head', y: 0.5 }] },
  bow: { targets: [{ bone: 'spine', x: 0.6 }] },
  tilt_head: { targets: [{ bone: 'head', z: 0.3 }] },
  wave: { targets: [
    { bone: 'rightUpperArm', z: -0.6 },
    { bone: 'rightLowerArm', x: 0.5 },
  ]},
  point: { targets: [
    { bone: 'rightUpperArm', z: -0.7 },
    { bone: 'rightLowerArm', x: 0.8 },
  ]},
  thumbs_up: { targets: [
    { bone: 'rightUpperArm', z: -0.3 },
    { bone: 'rightLowerArm', z: 0.3 },
    { bone: 'rightThumbMetacarpal', x: -0.5 },
  ]},
  peace: { targets: [
    { bone: 'rightUpperArm', z: -0.4 },
    { bone: 'rightLowerArm', x: 0.3 },
    { bone: 'rightIndexProximal', x: -0.8 },
    { bone: 'rightMiddleProximal', x: -0.8 },
  ]},
};

const GESTURE_BONES = [...new Set(Object.values(GESTURES).flatMap(g => g.targets.map(t => t.bone)))];

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

    this._gestureActive = false;
    this._gestureProgress = 0;
    this._gestureDuration = 1;
    this._gestureAutoReset = true;
    this._gesturePhase = 0;
    this._gestureTargets = [];
    this._gestureCallback = null;
    this._gestureKeyframes = [];
    this._gestureReset = false;
    this._gestureResetProgress = 0;

    this._boneOverrides = {};
    this._savedBoneRotations = {};
    this._initialPoseSaved = false;

    this._lookAtTargetObject = null;

    this._blinkTimer = 2 + Math.random() * 4;
    this._blinkPhase = null;
    this._blinkProgress = 0;
    this._eyeTimer = 3 + Math.random() * 5;
    this._eyePhase = null;
    this._eyeProgress = 0;
    this._eyeTarget = null;
    this._bodySwayTime = Math.random() * 10;
    this._microExpTimer = 8 + Math.random() * 12;
    this._microExpPhase = null;
    this._microExpProgress = 0;
    this._microExpTarget = null;
    this._idleEnabled = true;
    this._idleBaseRotations = {};
    this._mouseX = 0;
    this._mouseY = 0;
    this._mouseLookActive = true;
    this._mouseLastMoveTime = 0;

    this._config = {
      arms: {
        rightUpperArm: { x: 0.1, z: -1.2 },
        rightLowerArm: { x: 0.7 },
        leftUpperArm: { x: 0.1, z: 1.2 },
        leftLowerArm: { x: 0.7 },
        swingAmplitude: 0.06,
        swingSpeed: 0.8,
      },
      hands: {
        right: { thumbCurl: 0, indexCurl: 0, middleCurl: 0, ringCurl: 0, littleCurl: 0 },
        left: { thumbCurl: 0, indexCurl: 0, middleCurl: 0, ringCurl: 0, littleCurl: 0 },
      },
      body: {
        spineSwayX: 0.006,
        spineSwayZ: 0.004,
        swaySpeed: 0.7,
      },
      head: {
        neckZ: 0.008,
        neckY: 0.006,
        speed: 0.3,
      },
      blink: {
        minInterval: 2,
        maxInterval: 4,
        closeDuration: 0.08,
        openDuration: 0.12,
      },
      eye: {
        saccadeRangeX: 0.4,
        saccadeRangeY: 0.2,
        minInterval: 3,
        maxInterval: 6,
        duration: 0.8,
        mouseFovScale: 0.3,
        mouseIdleTimeout: 8,
      },
      microExp: {
        minInterval: 8,
        maxInterval: 12,
        weight: 0.12,
        fadeIn: 0.5,
        hold: 1.5,
        fadeOut: 0.5,
      },
    };

    this._configMode = false;
    this._poseFrozen = false;
    this._poseExpressionOverrides = {};
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
    document.addEventListener('mousemove', this._onMouseMove);
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

  _onMouseMove = (e) => {
    this._mouseX = (e.clientX / window.innerWidth) * 2 - 1;
    this._mouseY = -(e.clientY / window.innerHeight) * 2 + 1;
    this._mouseLastMoveTime = performance.now();
  }

  setConfig(config) {
    if (!config) return;
    this._deepMerge(this._config, config);
  }

  applyConfigValue(path, value) {
    const parts = path.split('.');
    let target = this._config;
    for (let i = 0; i < parts.length - 1; i++) {
      if (!target[parts[i]]) return;
      target = target[parts[i]];
    }
    target[parts[parts.length - 1]] = value;

    if (!this._configMode || !this.vrm || !this.vrm.humanoid) return;
    if (path.startsWith('hands.')) {
      const parts2 = path.split('.');
      const side = parts2[1];
      const key = parts2[2];
      const fmap = { thumbCurl: 'thumb', indexCurl: 'index', middleCurl: 'middle', ringCurl: 'ring', littleCurl: 'little' };
      const fname = fmap[key];
      if (!fname) return;
      const prefix = side === 'right' ? 'right' : 'left';
      const CURL_RATIO = {
        thumb: { metacarpal: 0.35, proximal: 0.40, distal: 0.25 },
        finger: { proximal: 0.55, intermediate: 0.30, distal: 0.15 },
      };
      const isThumb = fname === 'thumb';
      const ratio = isThumb ? CURL_RATIO.thumb : CURL_RATIO.finger;
      const bones = isThumb ? ['Metacarpal', 'Proximal', 'Distal'] : ['Proximal', 'Intermediate', 'Distal'];
      for (const b of bones) {
        const boneName = prefix + (isThumb ? 'Thumb' : fname.charAt(0).toUpperCase() + fname.slice(1)) + b;
        const node = this.vrm.humanoid.getNormalizedBoneNode(boneName);
        const rkey = b.toLowerCase();
        if (node) node.rotation.x = value * (ratio[rkey] || 0.33);
      }
    }
  }

  getConfig() {
    return JSON.parse(JSON.stringify(this._config));
  }

  _deepMerge(target, source) {
    for (const k of Object.keys(source)) {
      if (source[k] && typeof source[k] === 'object' && !Array.isArray(source[k]) && target[k] && typeof target[k] === 'object') {
        this._deepMerge(target[k], source[k]);
      } else {
        target[k] = source[k];
      }
    }
  }

  getModelTransform() {
    if (!this.vrm) {
      return { positionX: 0, positionY: 0, rotation: 0, scale: this.getScale() };
    }
    return {
      positionX: this.vrm.scene.position.x,
      positionY: this._modelBaseY,
      rotation: this.vrm.scene.rotation.y,
      scale: this.getScale(),
    };
  }

  setModelTransform(t) {
    if (!this.vrm || !t) return;
    if (t.positionX !== undefined) this.vrm.scene.position.x = t.positionX;
    if (t.positionY !== undefined) this._modelBaseY = t.positionY;
    if (t.rotation !== undefined) this.vrm.scene.rotation.y = t.rotation;
    if (t.scale !== undefined) this.setScale(t.scale);
  }

  enterConfigMode() {
    this._configMode = true;
  }

  exitConfigMode() {
    this._configMode = false;
  }

  isConfigMode() {
    return this._configMode;
  }

  enterEditMode() {
    this._configMode = true;
    this._poseFrozen = true;
  }

  exitEditMode() {
    this._configMode = false;
    this._poseFrozen = false;
    this._poseExpressionOverrides = {};
  }

  setPoseFrozen(frozen) {
    this._poseFrozen = !!frozen;
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
      rot: this.vrm.scene.rotation.y,
    } : { x: 0, y: 0, rot: 0 };
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
    this.vrm.scene.position.x = this._modelStart.x - dx * sensitivity;
    this._modelBaseY = this._modelStart.y - dy * sensitivity;
  }

  rotateModel(clientX) {
    if (!this._pointerDown || !this.vrm) return;
    const dx = (clientX - this._pointerStart.x) * 0.01;
    this.vrm.scene.rotation.y = this._modelStart.rot + dx;
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

  getGestureNames() {
    return Object.keys(GESTURES);
  }

  getBoneNames() {
    const names = typeof VRMHumanBoneName !== 'undefined'
      ? Object.values(VRMHumanBoneName).filter(v => typeof v === 'string')
      : [];
    return names.length ? names : GESTURE_BONES;
  }

  getCustomExpressionNames() {
    if (!this.vrm || !this.vrm.expressionManager) return [];
    const names = [];
    const map = this.vrm.expressionManager.customExpressionMap || {};
    for (const key in map) {
      if (Object.prototype.hasOwnProperty.call(map, key)) names.push(key);
    }
    const presetMap = this.vrm.expressionManager.presetExpressionMap || {};
    for (const key in presetMap) {
      if (Object.prototype.hasOwnProperty.call(presetMap, key)) names.push(key);
    }
    return [...new Set(names)];
  }

  setBlendShapeWeight(name, weight) {
    if (!this.vrm || !this.vrm.expressionManager) return false;
    const clamped = Math.max(0, Math.min(1, Number(weight) || 0));
    this.vrm.expressionManager.setValue(String(name), clamped);
    return true;
  }

  getCameraState() {
    return { theta: this._camTheta, phi: this._camPhi, distance: this.cameraDistance };
  }

  getExpressionNames() {
    return this.getCustomExpressionNames();
  }

  getExpressionWeights() {
    const out = {};
    if (!this.vrm || !this.vrm.expressionManager) return out;
    const map = this.vrm.expressionManager.customExpressionMap || {};
    for (const key in map) out[key] = map[key].weight;
    const presetMap = this.vrm.expressionManager.presetExpressionMap || {};
    for (const key in presetMap) {
      if (Object.prototype.hasOwnProperty.call(presetMap, key) && !(key in out)) {
        out[key] = presetMap[key].weight;
      }
    }
    return out;
  }

  setExpressionWeight(name, weight) {
    if (!this.vrm || !this.vrm.expressionManager) return false;
    const clamped = Math.max(0, Math.min(1, Number(weight) || 0));
    this.vrm.expressionManager.setValue(String(name), clamped);
    this._poseExpressionOverrides[String(name)] = clamped;
    return true;
  }

  clearExpressions() {
    if (!this.vrm || !this.vrm.expressionManager) return;
    const names = this.getExpressionNames();
    for (const n of names) this.vrm.expressionManager.setValue(n, 0);
    this._poseExpressionOverrides = {};
  }

  _saveIdleBaseRotations() {
    if (!this.vrm || !this.vrm.humanoid) return;
    for (const name of ['spine', 'neck']) {
      const node = this.vrm.humanoid.getNormalizedBoneNode(name);
      if (node) {
        this._idleBaseRotations[name] = { x: node.rotation.x, y: node.rotation.y, z: node.rotation.z };
      }
    }
  }

  _saveInitialPose() {
    if (!this.vrm || !this.vrm.humanoid) return;
    this._savedBoneRotations = {};
    const relevantBones = [...new Set([...GESTURE_BONES, ...Object.keys(this._boneOverrides)])];
    for (const name of relevantBones) {
      const node = this.vrm.humanoid.getNormalizedBoneNode(name);
      if (node) {
        this._savedBoneRotations[name] = { x: node.rotation.x, y: node.rotation.y, z: node.rotation.z };
      }
    }
    this._initialPoseSaved = true;
  }

  rotateBone(boneName, axis, angleDeg) {
    if (!this.vrm || !this.vrm.humanoid) return false;
    const node = this.vrm.humanoid.getNormalizedBoneNode(boneName);
    if (!node) return false;
    if (!this._initialPoseSaved) this._saveInitialPose();
    const rad = THREE.MathUtils.degToRad(Number(angleDeg) || 0);
    const a = String(axis).toLowerCase();
    if (a === 'x') node.rotation.x = rad;
    else if (a === 'y') node.rotation.y = rad;
    else if (a === 'z') node.rotation.z = rad;
    else return false;
    this._boneOverrides[boneName] = { x: node.rotation.x, y: node.rotation.y, z: node.rotation.z };
    return true;
  }

  resetPose() {
    if (!this.vrm || !this.vrm.humanoid) return;
    for (const name of Object.keys(this._savedBoneRotations)) {
      const saved = this._savedBoneRotations[name];
      const node = this.vrm.humanoid.getNormalizedBoneNode(name);
      if (node) {
        node.rotation.x = saved.x;
        node.rotation.y = saved.y;
        node.rotation.z = saved.z;
      }
    }
    for (const name of Object.keys(this._boneOverrides)) {
      const saved = this._savedBoneRotations[name];
      const node = this.vrm.humanoid.getNormalizedBoneNode(name);
      if (node && saved) {
        node.rotation.x = saved.x;
        node.rotation.y = saved.y;
        node.rotation.z = saved.z;
      }
    }
    this._boneOverrides = {};
    this._gestureActive = false;
    this._gestureReset = false;
  }

  getPoseSnapshot() {
    const bones = {};
    if (this.vrm && this.vrm.humanoid) {
      const pose = this.vrm.humanoid.getPose();
      for (const boneName in pose) {
        if (pose[boneName] && pose[boneName].rotation) {
          bones[boneName] = { rotation: pose[boneName].rotation.slice(0, 4) };
        }
      }
    }
    return {
      bones,
      expressions: this.getExpressionWeights(),
      modelState: this.getModelTransform(),
      camera: this.getCameraState(),
    };
  }

  applyPoseSnapshot(snapshot, transitionMs) {
    const target = snapshot || {};
    const dur = Math.max(0, Number(transitionMs) || 0);
    if (dur === 0 || !this.vrm) {
      if (this.vrm && this.vrm.humanoid) this.vrm.humanoid.setPose(target.bones || {});
      const exp = target.expressions || {};
      this.clearExpressions();
      for (const k in exp) this.setExpressionWeight(k, exp[k]);
      const ms = target.modelState;
      if (ms) this.setModelTransform(ms);
      this.setPoseFrozen(true);
      return Promise.resolve();
    }
    const fromBones = this.vrm.humanoid.getPose();
    const toBones = target.bones || {};
    const start = performance.now();
    return new Promise((resolve) => {
      const tick = () => {
        const t = Math.min(1, (performance.now() - start) / dur);
        const eased = 1 - (1 - t) * (1 - t); // easeOutQuad
        for (const name in toBones) {
          const qTo = toBones[name].rotation;
          if (!qTo) continue;
          const qFrom = (fromBones[name] && fromBones[name].rotation) || [0, 0, 0, 1];
          const q = new THREE.Quaternion(qFrom[0], qFrom[1], qFrom[2], qFrom[3]);
          q.slerp(new THREE.Quaternion(qTo[0], qTo[1], qTo[2], qTo[3]), eased);
          const node = this.vrm.humanoid.getNormalizedBoneNode(name);
          if (node) node.quaternion.copy(q);
        }
        const exp = target.expressions || {};
        for (const k in exp) {
          const from = this._poseExpressionOverrides[k] || 0;
          const w = from + (exp[k] - from) * eased;
          this.vrm.expressionManager.setValue(k, w);
          this._poseExpressionOverrides[k] = w;
        }
        const ms = target.modelState;
        if (ms) {
          const cur = this.getModelTransform();
          this.setModelTransform({
            positionX: cur.positionX + (ms.positionX - cur.positionX) * eased,
            positionY: cur.positionY + (ms.positionY - cur.positionY) * eased,
            rotation: cur.rotation + (ms.rotation - cur.rotation) * eased,
            scale: cur.scale + (ms.scale - cur.scale) * eased,
          });
        }
        if (t < 1) {
          requestAnimationFrame(tick);
        } else {
          this.setPoseFrozen(true);
          resolve();
        }
      };
      tick();
    });
  }

  executeGesture(name, duration, autoReset) {
    if (!this.vrm || !this.vrm.humanoid) return false;
    const def = GESTURES[name];
    if (!def) return false;
    if (!this._initialPoseSaved) this._saveInitialPose();

    if (this._gestureActive || this._gestureReset) {
      this._finishGestureImmediate();
    }

    this._gestureTargets = def.targets.map(t => {
      const node = this.vrm.humanoid.getNormalizedBoneNode(t.bone);
      return {
        bone: t.bone,
        node,
        start: node ? { x: node.rotation.x, y: node.rotation.y, z: node.rotation.z } : { x: 0, y: 0, z: 0 },
        end: { x: t.x || 0, y: t.y || 0, z: t.z || 0 },
      };
    });
    this._gestureActive = true;
    this._gestureProgress = 0;
    this._gestureDuration = Math.max(0.3, Number(duration) || 1.5);
    this._gestureAutoReset = autoReset !== undefined ? !!autoReset : true;
    this._gestureReset = false;
    return true;
  }

  _finishGestureImmediate() {
    if (this._gestureReset) {
      for (const t of this._gestureKeyframes) {
        if (t.node) {
          t.node.rotation.x = t.start.x;
          t.node.rotation.y = t.start.y;
          t.node.rotation.z = t.start.z;
        }
      }
    }
    this._gestureActive = false;
    this._gestureReset = false;
    this._gestureKeyframes = [];
  }

  _idleUpdate(delta) {
    if (!this.vrm || !this.vrm.humanoid || !this._idleEnabled) return;
    if (!this.vrm.expressionManager) return;

    // Frozen pose (edit mode / applied preset): keep blink + breath, drop bone sway,
    // eye saccades and micro-expressions so the posed bones are not overwritten.
    if (this._poseFrozen) {
      this._blinkTimer -= delta;
      if (this._blinkTimer <= 0 && !this._blinkPhase) {
        this._blinkTimer = this._config.blink.minInterval + Math.random() * (this._config.blink.maxInterval - this._config.blink.minInterval);
        this._blinkPhase = 'closing';
        this._blinkProgress = 0;
      }
      if (this._blinkPhase === 'closing') {
        this._blinkProgress += delta / this._config.blink.closeDuration;
        this.vrm.expressionManager.setValue(VRMExpressionPresetName.Blink, Math.min(1, this._blinkProgress));
        if (this._blinkProgress >= 1) {
          this._blinkPhase = 'opening';
          this._blinkProgress = 0;
        }
      } else if (this._blinkPhase === 'opening') {
        this._blinkProgress += delta / this._config.blink.openDuration;
        this.vrm.expressionManager.setValue(VRMExpressionPresetName.Blink, Math.max(0, 1 - this._blinkProgress));
        if (this._blinkProgress >= 1) {
          this._blinkPhase = null;
          this.vrm.expressionManager.setValue(VRMExpressionPresetName.Blink, 0);
        }
      }
      return;
    }

    this._bodySwayTime += delta;
    const baseSpine = this._idleBaseRotations.spine || { x: 0, y: 0, z: 0 };
    const baseNeck = this._idleBaseRotations.neck || { x: 0, y: 0, z: 0 };

    const armsCfg = this._config.arms;
    const bodyCfg = this._config.body;
    const headCfg = this._config.head;
    const blinkCfg = this._config.blink;
    const eyeCfg = this._config.eye;
    const microExpCfg = this._config.microExp;

    if (!this._gestureActive && !this._gestureReset) {
      const swayX = Math.sin(this._bodySwayTime * bodyCfg.swaySpeed) * bodyCfg.spineSwayX;
      const swayZ = Math.sin(this._bodySwayTime * bodyCfg.swaySpeed * 0.7 + 1.3) * bodyCfg.spineSwayZ;
      const headZ = Math.sin(this._bodySwayTime * headCfg.speed + 0.8) * headCfg.neckZ;
      const headY = Math.sin(this._bodySwayTime * headCfg.speed * 0.7 + 2.1) * headCfg.neckY;
      const armSwing = Math.sin(this._bodySwayTime * armsCfg.swingSpeed) * armsCfg.swingAmplitude;

      const spine = this.vrm.humanoid.getNormalizedBoneNode('spine');
      const neck = this.vrm.humanoid.getNormalizedBoneNode('neck');
      if (spine) {
        spine.rotation.x = baseSpine.x + swayX;
        spine.rotation.z = baseSpine.z + swayZ;
      }
      if (neck) {
        neck.rotation.y = baseNeck.y + headY;
        neck.rotation.z = baseNeck.z + headZ;
      }

      {
        const rUpper = this.vrm.humanoid.getNormalizedBoneNode('rightUpperArm');
        const rLower = this.vrm.humanoid.getNormalizedBoneNode('rightLowerArm');
        const lUpper = this.vrm.humanoid.getNormalizedBoneNode('leftUpperArm');
        const lLower = this.vrm.humanoid.getNormalizedBoneNode('leftLowerArm');
        const ra = armsCfg.rightUpperArm;
        const rl = armsCfg.rightLowerArm;
        const la = armsCfg.leftUpperArm;
        const ll = armsCfg.leftLowerArm;
        if (rUpper) { rUpper.rotation.x = ra.x; rUpper.rotation.z = ra.z; }
        if (rLower) { rLower.rotation.x = rl.x + armSwing; }
        if (lUpper) { lUpper.rotation.x = la.x; lUpper.rotation.z = la.z; }
        if (lLower) { lLower.rotation.x = ll.x - armSwing; }

        const handCfg = this._config.hands;
        if (handCfg) {
          const CURL_RATIO = {
            thumb: { metacarpal: 0.35, proximal: 0.40, distal: 0.25 },
            finger: { proximal: 0.55, intermediate: 0.30, distal: 0.15 },
          };
          for (const side of ['right', 'left']) {
            const prefix = side === 'right' ? 'right' : 'left';
            const h = handCfg[side];
            const fingers = [
              { key: 'thumb', name: 'Thumb', bones: ['Metacarpal', 'Proximal', 'Distal'], ratio: CURL_RATIO.thumb },
              { key: 'index', name: 'Index', bones: ['Proximal', 'Intermediate', 'Distal'], ratio: CURL_RATIO.finger },
              { key: 'middle', name: 'Middle', bones: ['Proximal', 'Intermediate', 'Distal'], ratio: CURL_RATIO.finger },
              { key: 'ring', name: 'Ring', bones: ['Proximal', 'Intermediate', 'Distal'], ratio: CURL_RATIO.finger },
              { key: 'little', name: 'Little', bones: ['Proximal', 'Intermediate', 'Distal'], ratio: CURL_RATIO.finger },
            ];
            for (const f of fingers) {
              const curl = h[f.key + 'Curl'] || 0;
              for (let i = 0; i < f.bones.length; i++) {
                const boneName = prefix + f.name + f.bones[i];
                const node = this.vrm.humanoid.getNormalizedBoneNode(boneName);
                if (node) {
                  node.rotation.x = curl * f.ratio[f.bones[i].toLowerCase()] || curl * (f.ratio[i] || 0.33);
                }
              }
            }
          }
        }
      }
    }

    this._blinkTimer -= delta;
    if (this._blinkTimer <= 0 && !this._blinkPhase) {
      this._blinkTimer = blinkCfg.minInterval + Math.random() * (blinkCfg.maxInterval - blinkCfg.minInterval);
      this._blinkPhase = 'closing';
      this._blinkProgress = 0;
    }
    if (this._blinkPhase === 'closing') {
      this._blinkProgress += delta / blinkCfg.closeDuration;
      this.vrm.expressionManager.setValue(VRMExpressionPresetName.Blink, Math.min(1, this._blinkProgress));
      if (this._blinkProgress >= 1) {
        this._blinkPhase = 'opening';
        this._blinkProgress = 0;
      }
    } else if (this._blinkPhase === 'opening') {
      this._blinkProgress += delta / blinkCfg.openDuration;
      this.vrm.expressionManager.setValue(VRMExpressionPresetName.Blink, Math.max(0, 1 - this._blinkProgress));
      if (this._blinkProgress >= 1) {
        this._blinkPhase = null;
        this.vrm.expressionManager.setValue(VRMExpressionPresetName.Blink, 0);
      }
    }

    const mouseIdleMs = (performance.now() - this._mouseLastMoveTime) / 1000;
    this._mouseLookActive = mouseIdleMs < eyeCfg.mouseIdleTimeout;

    this._eyeTimer -= delta;
    if (!this._mouseLookActive) {
      if (this._eyeTimer <= 0 && !this._eyePhase) {
        this._eyeTimer = eyeCfg.minInterval + Math.random() * (eyeCfg.maxInterval - eyeCfg.minInterval);
        this._eyePhase = 'looking';
        this._eyeProgress = 0;
        this._eyeTarget = {
          x: (Math.random() - 0.5) * eyeCfg.saccadeRangeX * 2,
          y: (Math.random() - 0.5) * eyeCfg.saccadeRangeY * 2,
        };
      }
      if (this._eyePhase === 'looking') {
        this._eyeProgress += delta;
        if (this._eyeProgress >= eyeCfg.duration) {
          this._eyePhase = null;
          this._eyeTarget = null;
          this._lookAtCenter();
        } else if (this._eyeTarget && this.vrm.lookAt) {
          const headNode = this.vrm.humanoid.getNormalizedBoneNode('head');
          if (headNode) {
            const origin = new THREE.Vector3();
            headNode.getWorldPosition(origin);
            this.vrm.lookAt.lookAt(new THREE.Vector3(
              origin.x + this._eyeTarget.x,
              origin.y + this._eyeTarget.y,
              origin.z + 1,
            ));
          }
        }
      }
    } else {
      const headNode = this.vrm.humanoid.getNormalizedBoneNode('head');
      if (headNode && this.vrm.lookAt && (this._configMode || Math.abs(this._mouseX) > 0.05 || Math.abs(this._mouseY) > 0.05)) {
        const origin = new THREE.Vector3();
        headNode.getWorldPosition(origin);
        this.vrm.lookAt.lookAt(new THREE.Vector3(
          origin.x - this._mouseX * eyeCfg.mouseFovScale,
          origin.y + this._mouseY * eyeCfg.mouseFovScale,
          origin.z + 1,
        ));
      }
    }

    this._microExpTimer -= delta;
    if (this._microExpTimer <= 0 && !this._microExpPhase) {
      this._microExpTimer = microExpCfg.minInterval + Math.random() * (microExpCfg.maxInterval - microExpCfg.minInterval);
      const exps = ['happy', 'relaxed', 'surprised'];
      this._microExpTarget = exps[Math.floor(Math.random() * exps.length)];
      this._microExpPhase = 'fadeIn';
      this._microExpProgress = 0;
    }
    if (this._microExpPhase === 'fadeIn') {
      this._microExpProgress += delta / microExpCfg.fadeIn;
      this.vrm.expressionManager.setValue(this._microExpTarget, Math.min(microExpCfg.weight, this._microExpProgress * microExpCfg.weight));
      if (this._microExpProgress >= 1) {
        this._microExpPhase = 'hold';
        this._microExpProgress = 0;
      }
    } else if (this._microExpPhase === 'hold') {
      this._microExpProgress += delta;
      if (this._microExpProgress >= microExpCfg.hold) {
        this._microExpPhase = 'fadeOut';
        this._microExpProgress = 0;
      }
    } else if (this._microExpPhase === 'fadeOut') {
      this._microExpProgress += delta / microExpCfg.fadeOut;
      const val = Math.max(0, microExpCfg.weight * (1 - this._microExpProgress));
      this.vrm.expressionManager.setValue(this._microExpTarget, val);
      if (this._microExpProgress >= 1) {
        this._microExpPhase = null;
        this.vrm.expressionManager.setValue(this._microExpTarget, 0);
        this._microExpTarget = null;
      }
    }
  }

  _lookAtCenter() {
    if (!this.vrm || !this.vrm.lookAt) return;
    const headNode = this.vrm.humanoid ? this.vrm.humanoid.getNormalizedBoneNode('head') : null;
    if (headNode) {
      const origin = new THREE.Vector3();
      headNode.getWorldPosition(origin);
      this.vrm.lookAt.lookAt(new THREE.Vector3(origin.x, origin.y, origin.z + 1));
    }
  }

  setLookAtTarget(xOrDir, y, z) {
    if (!this.vrm || !this.vrm.lookAt) return false;
    if (arguments.length === 1 && typeof xOrDir === 'string') {
      const dir = String(xOrDir).toLowerCase().replace(/[^a-z]/g, '');
      const dx = dir.includes('left') ? -0.5 : dir.includes('right') ? 0.5 : 0;
      const dy = dir.includes('up') ? 0.3 : dir.includes('down') ? -0.3 : 0;
      const dz = 1;
      const headNode = this.vrm.humanoid ? this.vrm.humanoid.getNormalizedBoneNode('head') : null;
      if (headNode) {
        const origin = new THREE.Vector3();
        headNode.getWorldPosition(origin);
        const target = new THREE.Vector3(origin.x + dx, origin.y + dy, origin.z + dz);
        this.vrm.lookAt.lookAt(target);
        return true;
      }
    }
    if (arguments.length === 3) {
      const target = new THREE.Vector3(Number(xOrDir) || 0, Number(y) || 0, Number(z) || 0);
      this.vrm.lookAt.lookAt(target);
      return true;
    }
    return false;
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
    this._initialPoseSaved = false;
    this._boneOverrides = {};
    this._gestureActive = false;
    this._gestureReset = false;
    this._saveIdleBaseRotations();
    requestAnimationFrame(() => this._saveInitialPose());
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
          Math.min(0.4, this.lipSyncWeights[key] * 0.8),
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
        if (!Object.keys(this._poseExpressionOverrides).length) {
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
      }

      if (this._gestureActive) {
        this._gestureProgress += delta / this._gestureDuration;
        if (this._gestureProgress >= 1) {
          this._gestureProgress = 1;
          for (const t of this._gestureTargets) {
            if (t.node) {
              t.node.rotation.x = t.end.x;
              t.node.rotation.y = t.end.y;
              t.node.rotation.z = t.end.z;
              if (!this._boneOverrides[t.bone]) this._boneOverrides[t.bone] = {};
              this._boneOverrides[t.bone] = { x: t.end.x, y: t.end.y, z: t.end.z };
            }
          }
          if (this._gestureAutoReset) {
            this._gestureKeyframes = this._gestureTargets.map(t => ({
              bone: t.bone, node: t.node,
              start: { x: t.end.x, y: t.end.y, z: t.end.z },
              end: this._savedBoneRotations[t.bone] || { x: 0, y: 0, z: 0 },
            }));
            this._gestureActive = false;
            this._gestureReset = true;
            this._gestureResetProgress = 0;
            this._gestureDuration = Math.max(0.3, this._gestureDuration * 0.5);
          } else {
            this._gestureActive = false;
          }
        } else {
          const t = this._gestureProgress;
          for (const g of this._gestureTargets) {
            if (g.node) {
              g.node.rotation.x = g.start.x + (g.end.x - g.start.x) * t;
              g.node.rotation.y = g.start.y + (g.end.y - g.start.y) * t;
              g.node.rotation.z = g.start.z + (g.end.z - g.start.z) * t;
              if (!this._boneOverrides[g.bone]) this._boneOverrides[g.bone] = {};
              this._boneOverrides[g.bone] = { x: g.node.rotation.x, y: g.node.rotation.y, z: g.node.rotation.z };
            }
          }
        }
      }

      if (this._gestureReset) {
        this._gestureResetProgress += delta / this._gestureDuration;
        if (this._gestureResetProgress >= 1) {
          this._gestureResetProgress = 1;
          for (const g of this._gestureKeyframes) {
            if (g.node) {
              g.node.rotation.x = g.end.x;
              g.node.rotation.y = g.end.y;
              g.node.rotation.z = g.end.z;
            }
          }
          this._boneOverrides = {};
          this._gestureReset = false;
          this._gestureKeyframes = [];
        } else {
          const t = this._gestureResetProgress;
          for (const g of this._gestureKeyframes) {
            if (g.node) {
              g.node.rotation.x = g.start.x + (g.end.x - g.start.x) * t;
              g.node.rotation.y = g.start.y + (g.end.y - g.start.y) * t;
              g.node.rotation.z = g.start.z + (g.end.z - g.start.z) * t;
            }
          }
        }
      }

      if (this.vrm.update) {
        this._idleUpdate(delta);
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
    this._gestureActive = false;
    this._gestureReset = false;
    this._gestureKeyframes = [];
    this._boneOverrides = {};
    this._lookAtTargetObject = null;
    this._idleEnabled = false;
    this._blinkPhase = null;
    this._eyePhase = null;
    this._microExpPhase = null;
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
