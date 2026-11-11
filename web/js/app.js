import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const canvas = document.getElementById("scene");
const statusEl = document.getElementById("status");
const streamEl = document.getElementById("stream");
const counters = {
  events: document.getElementById("stat-events"),
  memory: document.getElementById("stat-memory"),
  blocked: document.getElementById("stat-blocked"),
  refund: document.getElementById("stat-refund"),
};

const AGENTS = [
  { id: "supervisor", label: "Supervisor", angle: 0 },
  { id: "logistics", label: "Logistics", angle: Math.PI / 2 },
  { id: "aftersales", label: "AfterSales", angle: Math.PI },
  { id: "shopping", label: "Shopping", angle: (3 * Math.PI) / 2 },
];
const COLORS = {
  agent: 0x46e0ff,
  memory: 0x9d7bff,
  blocked: 0xff6b7a,
  tool: 0xffc663,
};

const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x05070f, 0.021);

const camera = new THREE.PerspectiveCamera(46, innerWidth / innerHeight, 0.1, 200);
function fitCamera() {
  const aspect = innerWidth / Math.max(1, innerHeight);
  const distance = aspect < 1.15 ? 23 : aspect < 1.4 ? 19 : 16;
  camera.position.set(0, distance * 0.47, distance);
  camera.lookAt(0, 0, 0);
}
fitCamera();

const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.05;

scene.add(new THREE.AmbientLight(0x6f8cff, 0.55));
const key = new THREE.PointLight(0x46e0ff, 90, 60);
key.position.set(9, 11, 8);
scene.add(key);
const rim = new THREE.PointLight(0x9d7bff, 70, 60);
rim.position.set(-10, -6, -8);
scene.add(rim);
const warm = new THREE.PointLight(0xffc663, 26, 40);
warm.position.set(0, 2, 9);
scene.add(warm);

const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
controls.dampingFactor = 0.06;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.35;
controls.minDistance = 7;
controls.maxDistance = 34;
controls.maxPolarAngle = Math.PI * 0.86;

const world = new THREE.Group();
scene.add(world);

// ---------------------------------------------------------------- core
const coreGeo = new THREE.IcosahedronGeometry(1.7, 2);
const coreMat = new THREE.MeshStandardMaterial({
  color: 0x1a1440,
  emissive: 0x6a4bff,
  emissiveIntensity: 1.25,
  metalness: 0.35,
  roughness: 0.35,
  flatShading: true,
});
const core = new THREE.Mesh(coreGeo, coreMat);
world.add(core);

const shell = new THREE.Mesh(
  new THREE.IcosahedronGeometry(2.15, 1),
  new THREE.MeshBasicMaterial({ color: COLORS.memory, wireframe: true, transparent: true, opacity: 0.22 }),
);
world.add(shell);

// ---------------------------------------------------------------- governance ring
const gate = new THREE.Mesh(
  new THREE.TorusGeometry(3.5, 0.09, 12, 160),
  new THREE.MeshStandardMaterial({
    color: 0x2a1c08,
    emissive: COLORS.tool,
    emissiveIntensity: 1.5,
    metalness: 0.5,
    roughness: 0.3,
  }),
);
gate.rotation.x = Math.PI / 2.35;
world.add(gate);

// ---------------------------------------------------------------- agents
const agentGroup = new THREE.Group();
world.add(agentGroup);
const nodes = [];
const RING = 7.4;

AGENTS.forEach((agent, index) => {
  const angle = agent.angle;
  const position = new THREE.Vector3(Math.cos(angle) * RING, Math.sin(index % 2 === 0 ? 0.9 : -0.9), Math.sin(angle) * RING);

  const mesh = new THREE.Mesh(
    new THREE.OctahedronGeometry(0.62, 0),
    new THREE.MeshStandardMaterial({
      color: 0x071a22,
      emissive: COLORS.agent,
      emissiveIntensity: 1.1,
      metalness: 0.4,
      roughness: 0.3,
    }),
  );
  mesh.position.copy(position);
  agentGroup.add(mesh);

  const halo = new THREE.Mesh(
    new THREE.RingGeometry(0.9, 0.98, 64),
    new THREE.MeshBasicMaterial({ color: COLORS.agent, transparent: true, opacity: 0.18, side: THREE.DoubleSide }),
  );
  halo.position.copy(position);
  halo.lookAt(0, position.y, 0);
  agentGroup.add(halo);

  const sprite = makeLabel(agent.label, "#9fe9ff");
  sprite.position.copy(position).add(new THREE.Vector3(0, 1.15, 0));
  agentGroup.add(sprite);

  const line = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([position, new THREE.Vector3(0, 0, 0)]),
    new THREE.LineBasicMaterial({ color: 0x3a4f7a, transparent: true, opacity: 0.35 }),
  );
  world.add(line);

  nodes.push({ ...agent, position, mesh, halo, index });
});

// ---------------------------------------------------------------- particles
const PARTICLES = 140;
const particles = [];
const particleGeometry = new THREE.SphereGeometry(0.075, 8, 8);
for (let i = 0; i < PARTICLES; i += 1) {
  const mesh = new THREE.Mesh(
    particleGeometry,
    new THREE.MeshBasicMaterial({ color: COLORS.agent, transparent: true, opacity: 0 }),
  );
  world.add(mesh);
  particles.push({ mesh, active: false, t: 0, from: new THREE.Vector3(), to: new THREE.Vector3(), speed: 0.02, color: COLORS.agent });
}

function spawn(kind) {
  const particle = particles.find((p) => !p.active);
  if (!particle) return;
  const node = nodes[Math.floor(Math.random() * nodes.length)];
  particle.active = true;
  particle.t = 0;
  particle.from.copy(node.position);
  particle.color = COLORS[kind] ?? COLORS.agent;
  particle.mesh.material.color.setHex(particle.color);
  particle.mesh.material.opacity = 0.95;
  particle.speed = 0.012 + Math.random() * 0.012;

  if (kind === "memory") {
    particle.to.set(0, 0, 0);
  } else if (kind === "blocked") {
    const angle = Math.random() * Math.PI * 2;
    particle.to.set(Math.cos(angle) * 3.5, Math.sin(angle) * 1.2, Math.sin(angle) * 3.5);
  } else if (kind === "tool") {
    particle.to.copy(node.position).multiplyScalar(0.55).add(new THREE.Vector3(0, 1.4, 0));
  } else {
    particle.to.set(0, 1.9, 0);
  }
  particle.mesh.position.copy(particle.from);
}

// ---------------------------------------------------------------- labels
function makeLabel(text, color) {
  const canvasEl = document.createElement("canvas");
  canvasEl.width = 512;
  canvasEl.height = 128;
  const ctx = canvasEl.getContext("2d");
  ctx.font = "600 46px Inter, Segoe UI, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.shadowColor = color;
  ctx.shadowBlur = 18;
  ctx.fillStyle = color;
  ctx.fillText(text, 256, 66);
  const texture = new THREE.CanvasTexture(canvasEl);
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(3.4, 0.85, 1);
  return sprite;
}

// ---------------------------------------------------------------- HUD
const state = { events: 0, memory: 0, blocked: 0, refund: 0 };

function updateHud(kind, text) {
  state.events += 1;
  if (kind === "memory") state.memory += 1;
  if (kind === "blocked") state.blocked += 1;
  if (kind === "tool" && /refund/i.test(text)) state.refund += 1;
  counters.events.textContent = state.events;
  counters.memory.textContent = state.memory;
  counters.blocked.textContent = state.blocked;
  counters.refund.textContent = state.refund;

  const li = document.createElement("li");
  li.dataset.kind = kind;
  li.textContent = text;
  streamEl.prepend(li);
  while (streamEl.children.length > 6) streamEl.lastElementChild.remove();
}

function setStatus(mode, text) {
  statusEl.dataset.state = mode;
  statusEl.textContent = text;
}

function normalise(event) {
  const type = String(event.event_type || event.type || "agent_event").toLowerCase();
  const agent = String(event.agent || "supervisor");
  const payload = event.payload || {};
  if (type.includes("govern") || type.includes("block") || String(event.status).toLowerCase() === "blocked") {
    return { kind: "blocked", text: `⛔ ${agent} · ${payload.reason_code || type}` };
  }
  if (type.includes("memory")) {
    return { kind: "memory", text: `🧠 ${payload.count ?? "?"} memories · ${agent}` };
  }
  if (type.includes("tool")) {
    return { kind: "tool", text: `🛠 ${payload.tool || payload.name || type} · ${agent}` };
  }
  if (type.includes("refund")) {
    return { kind: "tool", text: `💸 pending_review · ${agent}` };
  }
  return { kind: "agent", text: `→ ${agent} · ${type}` };
}

function handleEvent(event) {
  const { kind, text } = normalise(event);
  spawn(kind);
  updateHud(kind, text);
}

// ---------------------------------------------------------------- input
const DEMO_EVENTS = [
  { event_type: "intent_routed", agent: "supervisor", payload: { intent: "logistics" } },
  { event_type: "memory_retrieved", agent: "logistics", payload: { count: 3 } },
  { event_type: "tool_called", agent: "logistics", payload: { tool: "query_order" } },
  { event_type: "governance_blocked", agent: "aftersales", payload: { reason_code: "refund_exceeds_authoritative_limit" } },
  { event_type: "refund_pending_review", agent: "aftersales", payload: { amount: 399 } },
  { event_type: "memory_retrieved", agent: "shopping", payload: { count: 2 } },
];

let demoTimer = null;
function startDemo(reason) {
  if (demoTimer) return;
  setStatus("demo", `demo · ${reason}`);
  let index = 0;
  demoTimer = setInterval(() => {
    handleEvent(DEMO_EVENTS[index % DEMO_EVENTS.length]);
    index += 1;
  }, 1100);
  handleEvent(DEMO_EVENTS[0]);
}

function connect() {
  if (location.protocol === "file:") {
    startDemo("local preview");
    return;
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let socket;
  try {
    socket = new WebSocket(`${proto}://${location.host}/api/v1/ws/monitor`);
  } catch {
    startDemo("no websocket");
    return;
  }
  socket.onopen = () => setStatus("live", "live · ws");
  socket.onmessage = (message) => {
    try {
      handleEvent(JSON.parse(message.data));
    } catch {
      /* ignore malformed frames */
    }
  };
  socket.onerror = () => startDemo("ws error");
  socket.onclose = () => {
    if (statusEl.dataset.state !== "demo") setStatus("offline", "offline");
    startDemo("ws closed");
  };
}
connect();

// ---------------------------------------------------------------- loop
const clock = new THREE.Clock();

function tick() {
  const delta = clock.getDelta();
  const elapsed = clock.elapsedTime;

  core.rotation.y += delta * 0.25;
  core.rotation.x = Math.sin(elapsed * 0.3) * 0.15;
  shell.rotation.y -= delta * 0.12;
  shell.rotation.z += delta * 0.05;
  gate.rotation.z += delta * 0.22;
  agentGroup.children.forEach((child, i) => {
    if (child.isMesh && child.geometry.type === "OctahedronGeometry") {
      child.rotation.y += delta * 0.6;
      child.position.y = Math.sin(elapsed * 1.1 + i) * 0.18 + (i % 2 === 0 ? 0.9 : -0.9);
    }
  });

  particles.forEach((particle) => {
    if (!particle.active) return;
    particle.t += particle.speed;
    if (particle.t >= 1) {
      particle.active = false;
      particle.mesh.material.opacity = 0;
      return;
    }
    const eased = 1 - Math.pow(1 - particle.t, 3);
    particle.mesh.position.lerpVectors(particle.from, particle.to, eased);
    particle.mesh.position.y += Math.sin(particle.t * Math.PI) * 0.9;
    particle.mesh.material.opacity = 0.95 * (1 - particle.t);
  });

  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(tick);
}

addEventListener("resize", () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

document.body.dataset.ready = "1";
tick();
