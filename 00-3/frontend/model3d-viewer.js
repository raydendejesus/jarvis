import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// Separate ES module (rather than folded into app.js) because three.js is
// only distributed as ES modules now - app.js stays a plain classic script,
// this exposes window.Model3DViewer.load() as the one thing it needs to call.

let scene, camera, renderer, controls, currentModel;

function init() {
  const canvas = document.getElementById('model3dCanvas');
  if (!canvas || renderer) return;
  const wrap = canvas.parentElement;

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0a0f12);

  camera = new THREE.PerspectiveCamera(45, wrap.clientWidth / wrap.clientHeight || 1, 0.1, 1000);
  camera.position.set(2, 2, 2);

  renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(wrap.clientWidth, wrap.clientHeight);

  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.AmbientLight(0xffffff, 0.7));
  const dirLight = new THREE.DirectionalLight(0xffffff, 1.2);
  dirLight.position.set(5, 10, 7);
  scene.add(dirLight);

  window.addEventListener('resize', onResize);
  animate();
}

function onResize() {
  const canvas = document.getElementById('model3dCanvas');
  if (!canvas || !renderer) return;
  const wrap = canvas.parentElement;
  if (!wrap.clientWidth || !wrap.clientHeight) return;
  camera.aspect = wrap.clientWidth / wrap.clientHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(wrap.clientWidth, wrap.clientHeight);
}

function animate() {
  requestAnimationFrame(animate);
  if (controls) controls.update();
  if (renderer && scene && camera) renderer.render(scene, camera);
}

function load(url, title) {
  init();
  onResize();

  if (currentModel) {
    scene.remove(currentModel);
    currentModel = null;
  }

  new GLTFLoader().load(
    url,
    (gltf) => {
      currentModel = gltf.scene;
      // Normalize every model to roughly the same on-screen size/position,
      // regardless of what scale/origin it was actually generated at.
      const box = new THREE.Box3().setFromObject(currentModel);
      const size = box.getSize(new THREE.Vector3()).length() || 1;
      const center = box.getCenter(new THREE.Vector3());
      currentModel.position.sub(center);
      currentModel.scale.setScalar(2 / size);
      scene.add(currentModel);
    },
    undefined,
    (err) => console.error('[model3d-viewer] failed to load model:', err)
  );

  const titleEl = document.getElementById('model3dTitle');
  if (titleEl) titleEl.textContent = title ? `- ${title}` : '';
  const panel = document.getElementById('model3dPanel');
  if (panel) panel.hidden = false;
}

window.Model3DViewer = { load };
