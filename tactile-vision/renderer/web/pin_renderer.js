let THREE;
try {
  THREE = await import("./vendor/three.module.js");
} catch (_localError) {
  THREE = await import("https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js");
}

/*
 * The surface is a digital twin of the physical 80x48 pin plate. The default
 * view is an orthographic top view: no perspective skew and no semantic color
 * encoding. Raised pins are white and fully lowered pins are black so a judge
 * can read the intended physical state immediately. UInt8 height remains real
 * extrusion and is still legible through the rim and neutral cast shadow.
 */
export class PinRenderer {
  constructor(container) {
    this.container = container;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf1f2ef);

    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 500);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    container.appendChild(this.renderer.domElement);

    this.pitch = 1.0;
    this.radius = 0.36;
    this.inactiveHeight = 0.09;
    this.maxHeight = 1.45;
    this.cols = 0;
    this.rows = 0;
    this.levels = 256;
    this.currentLevels = new Float32Array(0);
    this.targetLevels = new Float32Array(0);
    this.motionSpeed = 8.5;
    this.isMoving = false;
    this.lastTick = performance.now();
    this.viewMode = "top";

    this._raisedColor = new THREE.Color(0xffffff);
    this._loweredColor = new THREE.Color(0x050505);

    // Instance colour encodes only the binary physical state (raised/lowered),
    // never a semantic region and never a replacement for the UInt8 height.
    this.pinMaterial = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      metalness: 0.04,
      roughness: 0.48,
    });
    this.rimMaterial = new THREE.MeshStandardMaterial({
      color: 0xffffff,
      metalness: 0.02,
      roughness: 0.52,
      transparent: true,
      opacity: 0.92,
    });
    this.shadowMaterial = new THREE.MeshBasicMaterial({
      color: 0x3b4244,
      transparent: true,
      opacity: 0.68,
      depthWrite: false,
    });
    this.boardMaterial = new THREE.MeshStandardMaterial({
      color: 0x303638,
      metalness: 0.08,
      roughness: 0.58,
    });

    this._addLights();
    this._resizeObserver = new ResizeObserver(() => this.resize());
    this._resizeObserver.observe(container);
    this._raycaster = new THREE.Raycaster();
    this._pointer = new THREE.Vector2();
    this.renderer.domElement.addEventListener("pointerdown", (event) => this._selectPin(event));
    this.resize();
    this.animate();
  }

  _addLights() {
    const hemi = new THREE.HemisphereLight(0xffffff, 0x596164, 1.8);
    this.scene.add(hemi);

    const key = new THREE.DirectionalLight(0xffffff, 3.8);
    key.position.set(-16, 30, 20);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.near = 1;
    key.shadow.camera.far = 120;
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0xdbe8ee, 0.9);
    fill.position.set(18, 14, -10);
    this.scene.add(fill);
  }

  _disposeSurface() {
    for (const obj of [this.shafts, this.caps, this.rims, this.shadows, this.board]) {
      if (!obj) continue;
      this.scene.remove(obj);
      if (obj.geometry) obj.geometry.dispose();
    }
    this.shafts = null;
    this.caps = null;
    this.rims = null;
    this.shadows = null;
    this.board = null;
  }

  _buildSurface(cols, rows) {
    this._disposeSurface();
    this.cols = cols;
    this.rows = rows;
    const count = cols * rows;
    this.currentLevels = new Float32Array(count);
    this.targetLevels = new Float32Array(count);

    const shaftGeometry = new THREE.CylinderGeometry(this.radius, this.radius, 1, 12, 1, false);
    const capGeometry = new THREE.SphereGeometry(this.radius * 1.02, 12, 8);
    const rimGeometry = new THREE.RingGeometry(this.radius * 1.12, this.radius * 1.30, 12);
    rimGeometry.rotateX(-Math.PI / 2);
    const shadowGeometry = new THREE.CircleGeometry(this.radius * 1.28, 12);
    shadowGeometry.rotateX(-Math.PI / 2);

    this.shafts = new THREE.InstancedMesh(shaftGeometry, this.pinMaterial, count);
    this.caps = new THREE.InstancedMesh(capGeometry, this.pinMaterial, count);
    this.rims = new THREE.InstancedMesh(rimGeometry, this.rimMaterial, count);
    this.shadows = new THREE.InstancedMesh(shadowGeometry, this.shadowMaterial, count);
    for (const mesh of [this.shafts, this.caps, this.rims, this.shadows]) {
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      mesh.castShadow = mesh !== this.shadows;
      mesh.receiveShadow = mesh !== this.shadows;
      this.scene.add(mesh);
    }

    // One logical pin cell maps to one exact screen cell. On the 800x480 RDK
    // panel this is 10x10 physical pixels for every one of the 80x48 pins.
    const boardW = Math.max(1, cols * this.pitch);
    const boardD = Math.max(1, rows * this.pitch);
    const boardGeometry = new THREE.BoxGeometry(boardW, 0.26, boardD);
    this.board = new THREE.Mesh(boardGeometry, this.boardMaterial);
    this.board.position.y = -0.16;
    this.board.receiveShadow = true;
    this.scene.add(this.board);

    this.setTopView();
  }

  _applyLevels(values) {
    if (!this.shafts || values.length !== this.cols * this.rows) return;

    const shaftDummy = new THREE.Object3D();
    const capDummy = new THREE.Object3D();
    const rimDummy = new THREE.Object3D();
    const shadowDummy = new THREE.Object3D();
    const originX = -(this.cols * this.pitch) / 2 + this.pitch / 2;
    const originZ = -(this.rows * this.pitch) / 2 + this.pitch / 2;

    for (let i = 0; i < values.length; i++) {
      const xIndex = i % this.cols;
      const yIndex = Math.floor(i / this.cols);
      const level = Math.max(0, Number(values[i]) || 0);
      const normalized = Math.min(1, level / Math.max(1, this.levels - 1));
      const stateColor = level > 0.5 ? this._raisedColor : this._loweredColor;
      const h = this.inactiveHeight + normalized * (this.maxHeight - this.inactiveHeight);
      const x = originX + xIndex * this.pitch;
      const z = originZ + yIndex * this.pitch;

      shaftDummy.position.set(x, h / 2, z);
      shaftDummy.scale.set(1, h, 1);
      shaftDummy.rotation.set(0, 0, 0);
      shaftDummy.updateMatrix();
      this.shafts.setMatrixAt(i, shaftDummy.matrix);

      capDummy.position.set(x, h, z);
      capDummy.scale.set(1, 0.54, 1);
      capDummy.rotation.set(0, 0, 0);
      capDummy.updateMatrix();
      this.caps.setMatrixAt(i, capDummy.matrix);

      // The rim expands subtly with height. It remains the same silver material
      // and makes the difference readable in the true top view.
      rimDummy.position.set(x, 0.012, z);
      rimDummy.scale.set(1 + normalized * 0.90, 1 + normalized * 0.90, 1);
      rimDummy.rotation.set(0, 0, 0);
      rimDummy.updateMatrix();
      this.rims.setMatrixAt(i, rimDummy.matrix);

      // Neutral screen-plane shadow is the 2.5D cue; it is not semantic color.
      shadowDummy.position.set(x + normalized * 1.55, -0.018, z + normalized * 1.05);
      shadowDummy.scale.set(0.90 + normalized * 0.95, 0.90 + normalized * 0.95, 1);
      shadowDummy.rotation.set(0, 0, 0);
      shadowDummy.updateMatrix();
      this.shadows.setMatrixAt(i, shadowDummy.matrix);

      this.shafts.setColorAt(i, stateColor);
      this.caps.setColorAt(i, stateColor);
      this.rims.setColorAt(i, stateColor);
    }

    for (const mesh of [this.shafts, this.caps, this.rims, this.shadows]) {
      mesh.instanceMatrix.needsUpdate = true;
    }
    for (const mesh of [this.shafts, this.caps, this.rims]) {
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
  }

  updateFrame(frame, options = {}) {
    const animate = options.animate !== false;
    if (!this.shafts || frame.cols !== this.cols || frame.rows !== this.rows) {
      this._buildSurface(frame.cols, frame.rows);
    }
    this.levels = 256;
    this.targetLevels = Float32Array.from(frame.pins || [], (value) => Math.max(0, Number(value) || 0));
    if (!animate) {
      this.currentLevels = this.targetLevels.slice();
      this._applyLevels(this.currentLevels);
      this.isMoving = false;
    } else {
      this.isMoving = true;
    }
    this.container.dispatchEvent(new CustomEvent("pinframe", { detail: { frame, animate } }));
  }

  retract() {
    if (!this.targetLevels.length) return;
    this.targetLevels.fill(0);
    this.isMoving = true;
  }

  replay(frame) {
    this.retract();
    window.setTimeout(() => this.updateFrame(frame), 520);
  }

  _selectPin(event) {
    if (!this.caps || !this.cols || !this.rows) return;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this._pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this._pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this._raycaster.setFromCamera(this._pointer, this.camera);
    const hit = this._raycaster.intersectObject(this.caps, false)[0];
    if (!hit || hit.instanceId == null) return;
    const index = hit.instanceId;
    this.container.dispatchEvent(new CustomEvent("pinselect", {
      detail: {
        index,
        x: index % this.cols,
        y: Math.floor(index / this.cols),
        level: Math.round(this.currentLevels[index] || 0),
      },
    }));
  }

  _updateProjection() {
    if (!this.cols || !this.rows) return;
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    const aspect = width / height;
    const gridW = this.cols * this.pitch;
    const gridH = this.rows * this.pitch;
    const gridAspect = gridW / gridH;
    // Contain the exact hardware rectangle. At 800x480 the two aspect ratios
    // are both 5:3, therefore there is zero unused margin and zero cropping.
    const halfW = aspect >= gridAspect ? (gridH * aspect) / 2 : gridW / 2;
    const halfH = aspect >= gridAspect ? gridH / 2 : gridW / (2 * aspect);
    this.camera.left = -halfW;
    this.camera.right = halfW;
    this.camera.top = halfH;
    this.camera.bottom = -halfH;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  // Orthographic top view is the product default and the RDK default.
  setTopView() {
    if (!this.cols || !this.rows) return;
    const span = Math.max(this.cols, this.rows);
    this.viewMode = "top";
    this.camera.position.set(0, span * 1.55, 0.001);
    this.camera.up.set(0, 0, -1);
    this.camera.lookAt(0, 0, 0);
    this._updateProjection();
  }

  // Optional orthographic 2.5D view: still no perspective distortion, only a
  // shallow elevation so the physical extrusion can be inspected.
  setPerspectiveView() {
    if (!this.cols || !this.rows) return;
    const span = Math.max(this.cols, this.rows);
    this.viewMode = "2.5d";
    this.camera.position.set(0, span * 1.55, span * 0.23);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(0, 0, 0);
    this._updateProjection();
  }

  resize() {
    this._updateProjection();
  }

  animate() {
    const tick = (now) => {
      const dt = Math.min(0.05, Math.max(0.001, (now - this.lastTick) / 1000));
      this.lastTick = now;
      if (this.isMoving && this.currentLevels.length === this.targetLevels.length) {
        const blend = 1 - Math.exp(-this.motionSpeed * dt);
        let remaining = false;
        for (let i = 0; i < this.currentLevels.length; i++) {
          const delta = this.targetLevels[i] - this.currentLevels[i];
          if (Math.abs(delta) > 0.006) {
            this.currentLevels[i] += delta * blend;
            remaining = true;
          } else {
            this.currentLevels[i] = this.targetLevels[i];
          }
        }
        this._applyLevels(this.currentLevels);
        this.isMoving = remaining;
        this.container.dataset.motion = remaining ? "moving" : "settled";
      }
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
}
