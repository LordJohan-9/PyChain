"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const state = { baseUrl: localStorage.getItem("pychain-node") || window.location.origin, data: null, blockIndex: null };
const viewTitles = { dashboard: "Dashboard", blocks: "Blocks", wallet: "Wallet & Send", pending: "Pending", peers: "Peers", mining: "Mining & Validation" };
let toastTimeout;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function shortHash(hash, length = 16) {
  return hash.length > length ? `${hash.slice(0, length)}…${hash.slice(-8)}` : hash;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString();
}

function formatRate(value) {
  const rate = Number(value || 0);
  if (rate >= 1_000_000) return `${(rate / 1_000_000).toFixed(2)} MH/s`;
  if (rate >= 1_000) return `${(rate / 1_000).toFixed(1)} kH/s`;
  return `${rate.toFixed(0)} H/s`;
}

function formatTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString();
}

async function deriveAddress(publicKey) {
  if (!publicKey || !window.crypto?.subtle) return null;
  const digest = await window.crypto.subtle.digest("SHA-256", new TextEncoder().encode(publicKey));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, 40);
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimeout);
  toastTimeout = window.setTimeout(() => toast.classList.remove("show"), 2600);
}

function showResult(target, message, isError = false) {
  target.textContent = message;
  target.classList.toggle("error", isError);
  target.hidden = false;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${state.baseUrl}${path}`, {
      ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
  } catch (error) {
    throw new Error(`Could not reach ${state.baseUrl}. Is the node running? (${error.message})`);
  }
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && body.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status} ${response.statusText})`);
  }
  return body;
}

function setOnline(online, message = "") {
  $("#sidebar-dot").classList.toggle("online", online);
  $("#sidebar-dot").classList.toggle("offline", !online);
  $("#status-dot").classList.toggle("online", online);
  $("#status-dot").classList.toggle("offline", !online);
  $("#sidebar-status").textContent = online ? "Node connected" : "Node offline";
  $("#node-status-heading").textContent = online ? "Operational" : "Offline";
  const error = $("#global-error");
  error.hidden = online;
  if (!online) error.textContent = message || "Node is unavailable. Check the node URL and make sure the node is running.";
}

function switchView(name) {
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
  $$(".nav-link").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $("#page-title").textContent = viewTitles[name] || "Dashboard";
  if (name === "blocks" && state.data) renderBlockDetail();
}

function addCell(row, text, className = "") {
  const cell = element("td", className, text);
  row.append(cell);
  return cell;
}

function makeHashButton(hash, label = shortHash(hash)) {
  const button = element("button", "copy-hash", label);
  button.type = "button";
  button.title = `Copy ${hash}`;
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(hash);
      showToast("Hash copied to clipboard");
    } catch {
      showToast("Clipboard access is unavailable in this browser");
    }
  });
  return button;
}

function blockRow(block, compact = false) {
  const row = element("tr");
  const heightCell = addCell(row, "");
  const height = element("button", "table-link", `#${block.index}`);
  height.addEventListener("click", () => {
    state.blockIndex = block.index;
    switchView("blocks");
    renderBlockDetail();
  });
  heightCell.append(height);
  const hashCell = addCell(row, "");
  hashCell.append(makeHashButton(block.hash));
  addCell(row, String(block.transactions.length));
  if (!compact) addCell(row, `${block.difficulty_bits} bits`);
  if (compact) addCell(row, `${block.difficulty_bits} bits`);
  if (compact) addCell(row, formatTime(block.timestamp));
  return row;
}

function renderDashboard() {
  const { status, chain } = state.data;
  $("#metric-height").textContent = formatNumber(status.height);
  $("#metric-peers").textContent = formatNumber(status.peer_count);
  $("#metric-pending").textContent = formatNumber(status.pending_count);
  $("#metric-hashrate").textContent = formatRate(status.estimated_hash_rate);
  $("#status-url").textContent = status.url;
  $("#status-difficulty").textContent = `${status.difficulty_bits} bits`;
  $("#status-work").textContent = formatNumber(status.total_work);
  $("#status-tip").textContent = status.tip_hash;
  $("#node-id-short").textContent = status.node_id.slice(0, 18);
  const tip = chain.blocks.at(-1);
  const latest = $("#latest-block");
  latest.className = "latest-block";
  latest.replaceChildren();
  if (tip) {
    const top = element("div", "latest-top");
    top.append(element("span", "height-badge", `BLOCK #${tip.index}`), element("span", "latest-label", formatTime(tip.timestamp)));
    latest.append(top, makeHashButton(tip.hash, tip.hash));
    const hash = latest.lastChild;
    hash.classList.add("latest-hash");
    const stats = element("div", "latest-stats");
    [["Transactions", tip.transactions.length], ["Difficulty", `${tip.difficulty_bits} bits`], ["Nonce", formatNumber(tip.nonce)]].forEach(([label, value]) => {
      const item = element("div");
      item.append(element("span", "", label), element("strong", "", String(value)));
      stats.append(item);
    });
    latest.append(stats);
  }
  const recent = $("#recent-blocks");
  recent.replaceChildren();
  [...chain.blocks].reverse().slice(0, 5).forEach((block) => recent.append(blockRow(block, true)));
}

function renderBlocks() {
  const blocks = state.data.chain.blocks;
  $("#blocks-count").textContent = `${blocks.length} ${blocks.length === 1 ? "block" : "blocks"}`;
  const table = $("#blocks-table");
  table.replaceChildren();
  [...blocks].reverse().forEach((block) => {
    const row = element("tr");
    const heightCell = addCell(row, "");
    const button = element("button", "table-link", `#${block.index}`);
    button.addEventListener("click", () => { state.blockIndex = block.index; renderBlockDetail(); });
    heightCell.append(button);
    const hashCell = addCell(row, "");
    hashCell.append(makeHashButton(block.hash));
    addCell(row, String(block.transactions.length));
    addCell(row, `${block.difficulty_bits} bits`);
    table.append(row);
  });
  $("#blocks-empty").hidden = blocks.length > 0;
  if (state.blockIndex === null || !blocks.some((block) => block.index === state.blockIndex)) {
    state.blockIndex = blocks.at(-1)?.index ?? null;
  }
  renderBlockDetail();
}

function renderBlockDetail() {
  const detail = $("#block-detail");
  const block = state.data?.chain.blocks.find((item) => item.index === state.blockIndex);
  detail.replaceChildren();
  if (!block) {
    $("#block-detail-title").textContent = "Select a block";
    detail.className = "empty-state";
    detail.textContent = "Choose a block to inspect its header and transactions.";
    return;
  }
  $("#block-detail-title").textContent = `Block #${block.index}`;
  detail.className = "";
  const fields = [
    ["Block hash", block.hash], ["Previous hash", block.previous_hash], ["Merkle root", block.merkle_root],
    ["Timestamp", formatTime(block.timestamp)], ["Difficulty", `${block.difficulty_bits} bits`],
    ["Nonce", formatNumber(block.nonce)], ["Transactions", String(block.transactions.length)],
  ];
  const list = element("dl", "detail-grid");
  fields.forEach(([label, value]) => {
    const item = element("div");
    item.append(element("dt", "", label));
    const content = element("dd", label.toLowerCase().includes("hash") || label === "Merkle root" ? "mono break-all" : "", value);
    if (label === "Previous hash" && block.index > 0) {
      const previous = element("button", "table-link", value);
      previous.addEventListener("click", () => { state.blockIndex = block.index - 1; renderBlockDetail(); });
      content.replaceChildren(previous);
    } else if (label.toLowerCase().includes("hash") || label === "Merkle root") {
      content.replaceChildren(makeHashButton(value, value));
    }
    item.append(content);
    list.append(item);
  });
  detail.append(list);
  const txList = element("div", "tx-list");
  txList.append(element("p", "eyebrow", "BLOCK TRANSACTIONS"));
  if (!block.transactions.length) txList.append(element("div", "empty-state", "Genesis block has no transactions."));
  block.transactions.forEach((tx) => {
    const card = element("div", "tx-card");
    card.append(element("strong", "", tx.tx_id || "Coinbase transaction"));
    const sender = tx.sender_address || (tx.sender_public_key ? shortHash(tx.sender_public_key, 12) : "Mining reward");
    card.append(element("span", "", `${formatNumber(tx.amount || 0)} PY  ·  ${sender} → ${tx.recipient || "—"}`));
    txList.append(card);
  });
  detail.append(txList);
}

async function renderWallet() {
  const { wallet, chain } = state.data;
  $("#wallet-balance").replaceChildren(document.createTextNode(formatNumber(wallet.balance)), element("small", "", "PY"));
  $("#wallet-address").textContent = wallet.address;
  const history = [];
  for (const block of [...chain.blocks].reverse()) {
    for (const tx of [...block.transactions].reverse()) {
      let sender = tx.sender_address;
      if (!sender && tx.sender_public_key) {
        try { sender = await deriveAddress(tx.sender_public_key); } catch { sender = null; }
      }
      if (tx.recipient === wallet.address || sender === wallet.address) {
        history.push({ ...tx, sender_address: sender, block_index: block.index });
        if (history.length === 10) break;
      }
    }
    if (history.length === 10) break;
  }
  const tbody = $("#wallet-history");
  tbody.replaceChildren();
  history.forEach((tx) => {
    const row = element("tr");
    const id = addCell(row, "");
    id.append(makeHashButton(tx.tx_id || "", shortHash(tx.tx_id || "coinbase")));
    addCell(row, tx.sender_address === wallet.address ? "Sent" : "Received");
    addCell(row, `${formatNumber(tx.amount || 0)} PY`);
    addCell(row, `#${tx.block_index}`);
    tbody.append(row);
  });
  $("#history-empty").hidden = history.length > 0;
}

async function renderPending() {
  const { pending } = state.data;
  $("#pending-count").textContent = `${pending.length} ${pending.length === 1 ? "pending" : "pending"}`;
  const tbody = $("#pending-table");
  tbody.replaceChildren();
  for (const tx of pending) {
    const row = element("tr");
    const txId = addCell(row, "");
    txId.append(makeHashButton(tx.tx_id, shortHash(tx.tx_id)));
    let sender = tx.sender_address;
    if (!sender) {
      try { sender = await deriveAddress(tx.sender_public_key); } catch { sender = null; }
    }
    addCell(row, shortHash(sender || tx.sender_public_key, 12));
    addCell(row, shortHash(tx.recipient, 12));
    addCell(row, `${formatNumber(tx.amount)} PY`);
    addCell(row, String(tx.nonce));
    tbody.append(row);
  }
  $("#pending-empty").hidden = pending.length > 0;
}

function renderPeers() {
  const { peers } = state.data;
  $("#peer-heading").textContent = `${peers.length} ${peers.length === 1 ? "peer" : "peers"}`;
  const tbody = $("#peers-table");
  tbody.replaceChildren();
  peers.forEach((peer) => {
    const row = element("tr");
    addCell(row, peer.node_id.slice(0, 12), "mono");
    addCell(row, peer.url);
    addCell(row, `#${peer.height}`);
    const health = peer.failures ? `${peer.failures} failures` : "Healthy";
    addCell(row, health);
    const actionCell = addCell(row, "");
    const remove = element("button", "text-button", "Remove");
    remove.addEventListener("click", async () => {
      remove.disabled = true;
      try {
        await request(`/peers?url=${encodeURIComponent(peer.url)}`, { method: "DELETE" });
        showToast("Peer removed");
        await refresh();
      } catch (error) {
        showResult($("#sync-result"), error.message, true);
      } finally {
        remove.disabled = false;
      }
    });
    actionCell.append(remove);
  });
  $("#peers-empty").hidden = peers.length > 0;
}

function renderMining() {
  const { status } = state.data;
  $("#mining-state").textContent = status.mining ? "MINING" : "IDLE";
  $("#mining-state").classList.toggle("mining", status.mining);
  $("#mining-nonce").textContent = status.mining ? formatNumber(status.mining_nonce) : "—";
  $("#mining-attempts").textContent = status.mining ? formatNumber(status.mining_attempts) : "—";
  $("#mining-rate").textContent = formatRate(status.estimated_hash_rate);
  $("#auto-mine").textContent = status.mining ? "Stop mining" : "Start auto-mining";
  $("#auto-mine").dataset.action = status.mining ? "auto-stop" : "auto-start";
}

async function refresh() {
  try {
    const [status, chain, wallet, pending, peerResponse] = await Promise.all([
      request("/status"), request("/chain"), request("/wallet"), request("/transactions/pending"), request("/peers"),
    ]);
    state.data = { status, chain, wallet, pending, peers: peerResponse.peers };
    setOnline(true);
    renderDashboard();
    renderBlocks();
    await renderWallet();
    await renderPending();
    renderPeers();
    renderMining();
  } catch (error) {
    setOnline(false, error.message);
  }
}

async function validate() {
  const targets = [$("#validation-result"), $("#mining-validation")];
  targets.forEach((target) => { target.hidden = true; });
  try {
    const result = await request("/validate");
    const message = result.valid
      ? "✓ Chain is valid. All blocks and transactions pass validation."
      : `✕ Invalid chain at block #${result.block_index} · rule: ${result.rule} · ${result.message}`;
    targets.forEach((target) => showResult(target, message, !result.valid));
  } catch (error) {
    targets.forEach((target) => showResult(target, error.message, true));
  }
}

async function runAction(button, action) {
  const original = button.textContent;
  button.disabled = true;
  if (action === "mine") button.textContent = "Mining…";
  try {
    if (action === "validate") return await validate();
    if (action === "sync") {
      const result = await request("/sync", { method: "POST" });
      showResult($("#sync-result"), `Sync complete · adopted ${result.adopted_chains} chain(s) · current height #${result.height}`);
    } else if (action === "mine") {
      const block = await request("/mine", { method: "POST" });
      showResult($("#mining-result"), `Block #${block.index} mined · ${shortHash(block.hash)} · ${block.transactions.length} transaction(s)`);
      showToast(`Block #${block.index} mined`);
    } else if (action === "auto-start" || action === "auto-stop") {
      await request(`/mine/${action === "auto-start" ? "start" : "stop"}`, { method: "POST" });
      showResult($("#mining-result"), action === "auto-start" ? "Auto-mining started." : "Auto-mining stopped.");
    }
    await refresh();
  } catch (error) {
    const target = action === "sync" ? $("#sync-result") : $("#mining-result");
    showResult(target, error.message, true);
  } finally {
    button.disabled = false;
    if (action === "mine") button.textContent = original;
  }
}

$("#send-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = $("button[type=submit]", form);
  const target = $("#send-result");
  target.hidden = true;
  button.disabled = true;
  try {
    const result = await request("/wallet/send", {
      method: "POST",
      body: JSON.stringify({ recipient: $("#recipient").value.trim(), amount: Number($("#amount").value) }),
    });
    showResult(target, `Transaction accepted into the mempool · ${result.tx_id}`);
    form.reset();
    await refresh();
  } catch (error) {
    showResult(target, error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#peer-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type=submit]", event.currentTarget);
  button.disabled = true;
  try {
    const peerUrl = $("#peer-url").value.trim().replace(/\/+$/, "");
    await request("/peers", { method: "POST", body: JSON.stringify({ url: peerUrl }) });
    $("#peer-form").reset();
    showToast("Peer connected");
    await refresh();
  } catch (error) {
    showResult($("#sync-result"), error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#connect-node").addEventListener("click", async () => {
  const value = $("#node-url").value.trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^/]+$/i.test(value)) {
    setOnline(false, "Enter a valid node origin, such as http://localhost:8001.");
    return;
  }
  state.baseUrl = value;
  localStorage.setItem("pychain-node", value);
  await refresh();
});

$$(".nav-link").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
$$("[data-go]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.go)));
$$("[data-action]").forEach((button) => button.addEventListener("click", () => runAction(button, button.dataset.action)));
$$("[data-copy]").forEach((button) => button.addEventListener("click", async () => {
  const text = $(`#${button.dataset.copy}`).textContent;
  try { await navigator.clipboard.writeText(text); showToast("Address copied to clipboard"); }
  catch { showToast("Clipboard access is unavailable in this browser"); }
}));

$("#node-url").value = state.baseUrl;
refresh();
window.setInterval(refresh, 3000);
