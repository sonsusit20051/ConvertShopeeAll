const dom = {
  body: document.body,
  inp: document.getElementById("inp"),
  btnPaste: document.getElementById("btnPaste"),
  btnConvert: document.getElementById("btnConvert"),
  btnCopy: document.getElementById("btnCopy"),
  btnOpen: document.getElementById("btnOpen"),
  resultPreview: document.getElementById("resultPreview"),
  status: document.getElementById("status"),
  modeFb: document.getElementById("modeFb"),
  modeYt: document.getElementById("modeYt"),
};

const defaults = {
  fb: {
    affiliateId: String(dom.body?.dataset?.defaultAffiliateIdFb || "17322940169").trim() || "17322940169",
    subId: String(dom.body?.dataset?.defaultSubIdFb || "cvweb").trim() || "cvweb",
  },
  yt: {
    affiliateId: String(dom.body?.dataset?.defaultAffiliateIdYt || "17391540096").trim() || "17391540096",
    subId: String(dom.body?.dataset?.defaultSubIdYt || "YT3").trim() || "YT3",
  },
};

const CREATE_COOLDOWN_SEC = 5;

const state = {
  activeSource: "fb",
  busy: false,
  currentAffiliateLink: "",
  cooldownUntilMs: 0,
  cooldownTimer: 0,
};

function setStatus(message, type = "") {
  dom.status.textContent = String(message || "");
  dom.status.classList.remove("ok", "err", "source-fb", "source-yt");
  if (dom.status) dom.status.style.color = "";
  if (type === "ok") dom.status.classList.add("ok");
  if (type === "err") dom.status.classList.add("err");
  if (type === "source-fb") {
    dom.status.classList.add("source-fb");
    if (dom.status) dom.status.style.color = "#2f59e8";
  }
  if (type === "source-yt") {
    dom.status.classList.add("source-yt");
    if (dom.status) dom.status.style.color = "#df1818";
  }
}

function setResultPreview(text, ready = false) {
  dom.resultPreview.textContent = String(text || "Kết quả sẽ hiển thị ở đây...");
  dom.resultPreview.classList.toggle("ready", Boolean(ready));
  dom.resultPreview.classList.remove("masked");
}

function setMaskedResultPreview(text) {
  dom.resultPreview.textContent = String(text || "Link đã tạo, bấm copy ngay");
  dom.resultPreview.classList.add("ready", "masked");
}

function clearCooldownTimer() {
  if (state.cooldownTimer) {
    window.clearInterval(state.cooldownTimer);
    state.cooldownTimer = 0;
  }
}

function getCooldownRemainSec() {
  const remainMs = Math.max(0, state.cooldownUntilMs - Date.now());
  return Math.ceil(remainMs / 1000);
}

function syncCooldownUi() {
  if (state.busy) return;

  const remain = getCooldownRemainSec();
  if (remain <= 0) {
    dom.btnConvert.disabled = false;
    dom.btnConvert.textContent = "Nhận voucher";
    return;
  }

  dom.btnConvert.disabled = true;
  dom.btnConvert.textContent = `Chờ ${remain}s`;
}

function startCooldown(seconds = CREATE_COOLDOWN_SEC) {
  clearCooldownTimer();
  state.cooldownUntilMs = Date.now() + Math.max(0, Number(seconds) || 0) * 1000;
  syncCooldownUi();
  state.cooldownTimer = window.setInterval(() => {
    const remain = getCooldownRemainSec();
    if (remain <= 0) {
      state.cooldownUntilMs = 0;
      clearCooldownTimer();
      syncCooldownUi();
      setStatus("Đã hết thời gian chờ. Bạn có thể convert link tiếp theo.");
      return;
    }
    syncCooldownUi();
  }, 200);
}

function setActionReady(isReady) {
  const ready = Boolean(isReady && state.currentAffiliateLink);
  const isFb = state.activeSource === "fb";
  dom.btnCopy.classList.toggle("is-ready", ready && isFb);
  dom.btnOpen.classList.toggle("is-ready", ready && !isFb);
}

function setBusy(nextBusy) {
  state.busy = Boolean(nextBusy);
  dom.btnPaste.disabled = state.busy;
  dom.btnConvert.disabled = state.busy;
  dom.btnConvert.textContent = state.busy ? "Đang xử lý..." : "Nhận voucher";
  dom.btnCopy.disabled = state.busy || !state.currentAffiliateLink;
  dom.btnOpen.disabled = state.busy || !state.currentAffiliateLink;

  if (state.busy) {
    setActionReady(false);
  } else {
    setActionReady(Boolean(state.currentAffiliateLink));
    syncCooldownUi();
  }
}

function resetOutput() {
  state.currentAffiliateLink = "";
  dom.btnOpen.disabled = true;
  dom.btnCopy.disabled = true;
  setActionReady(false);
  setResultPreview("Kết quả sẽ hiển thị ở đây...", false);
}

function applySourceUi() {
  const isFb = state.activeSource === "fb";
  dom.modeFb.classList.toggle("is-active", isFb);
  dom.modeYt.classList.toggle("is-active", !isFb);
  resetOutput();
  setStatus(
    isFb ? "Đang ở chế độ đổi mã Facebook." : "Đang ở chế độ đổi mã Youtube.",
    isFb ? "source-fb" : "source-yt",
  );
}

function setSource(nextSource) {
  const normalized = String(nextSource || "").toLowerCase() === "yt" ? "yt" : "fb";
  if (state.activeSource === normalized) return;
  state.activeSource = normalized;
  applySourceUi();
}

function normalizeInput(raw) {
  const text = String(raw || "").trim();
  if (!text) throw new Error("Bạn chưa nhập link.");

  const withProtocol = /^https?:\/\//i.test(text) ? text : `https://${text}`;

  let parsed;
  try {
    parsed = new URL(withProtocol);
  } catch {
    throw new Error("Link không hợp lệ.");
  }

  if (!parsed.host) throw new Error("Link không hợp lệ.");
  return parsed.toString();
}

async function callSyncConvertApi(inputUrl) {
  const source = state.activeSource;
  const sourceDefaults = defaults[source] || defaults.fb;

  const query = new URLSearchParams({
    url: inputUrl,
    source,
    yt: source === "yt" ? "1" : "0",
    affiliate_id: sourceDefaults.affiliateId,
    sub_id: sourceDefaults.subId,
  });

  const resp = await fetch(`/?${query.toString()}`, {
    method: "GET",
    cache: "no-store",
  });

  const payload = await resp.json().catch(() => ({}));
  if (!resp.ok || !payload?.success) {
    throw new Error(payload?.message || `HTTP ${resp.status}`);
  }

  return payload;
}

async function handlePaste() {
  if (state.busy) return;

  setStatus("");
  try {
    const text = await navigator.clipboard.readText();
    if (!text || !text.trim()) {
      setStatus("Clipboard trống. Hãy copy link Shopee trước.", "err");
      return;
    }
    dom.inp.value = text.trim();
    resetOutput();
    setStatus("Đã dán link từ clipboard.", "ok");
  } catch {
    dom.inp.focus();
    setStatus("Trình duyệt chặn đọc clipboard. Hãy nhấn Ctrl/Cmd+V để dán.", "err");
  }
}

async function handleConvert(event) {
  event?.preventDefault?.();
  if (state.busy) return;

  const remain = getCooldownRemainSec();
  if (remain > 0) {
    setStatus(`Vui lòng chờ ${remain}s rồi tạo lại.`, "err");
    syncCooldownUi();
    return;
  }

  setStatus("");
  resetOutput();

  let cleaned = "";
  try {
    cleaned = normalizeInput(dom.inp.value);
  } catch (error) {
    setStatus(error?.message || "Link không hợp lệ.", "err");
    return;
  }

  setBusy(true);
  let requestSent = false;
  try {
    requestSent = true;
    setStatus("Đang gửi yêu cầu convert...");

    const payload = await callSyncConvertApi(cleaned);
    const link = String(payload?.affiliateLink || payload?.longAffiliateLink || "").trim();
    if (!link) throw new Error("Không nhận được affiliate link.");

    state.currentAffiliateLink = link;
    dom.btnOpen.disabled = false;
    dom.btnCopy.disabled = false;
    setActionReady(true);

    if (state.activeSource === "fb") {
      setMaskedResultPreview("Link của sếp Done, bấm sao chép ngay!!!");
      setStatus("Link FB đã chuyển đổi xong.", "ok");
    } else {
      setResultPreview("Link của sếp Done, bấm Mua ngay nhé!!!", true);
      setStatus("Link YT đã sẵn sàng. Bấm Sao chép hoặc Mua ngay.", "ok");
    }

  } catch (error) {
    setStatus(`Tạo link thất bại: ${String(error?.message || "Lỗi không xác định")}`, "err");
    setResultPreview(`Lỗi: ${String(error?.message || "Không chuyển đổi được")}`);
  } finally {
    setBusy(false);
    if (requestSent) startCooldown(CREATE_COOLDOWN_SEC);
  }
}

async function handleCopy() {
  if (!state.currentAffiliateLink) {
    setStatus("Chưa có link. Hãy bấm Nhận voucher trước.", "err");
    return;
  }

  try {
    await navigator.clipboard.writeText(state.currentAffiliateLink);
    setStatus("Đã copy link vào clipboard.", "ok");
  } catch {
    setStatus("Không copy được do trình duyệt chặn. Hãy copy thủ công.", "err");
  }
}

function handleOpen(event) {
  event?.preventDefault?.();
  if (state.busy || !state.currentAffiliateLink) return;

  const popup = window.open(state.currentAffiliateLink, "_blank", "noopener,noreferrer");
  if (!popup) {
    setStatus("Trình duyệt chặn popup. Hãy cho phép mở tab mới rồi thử lại.", "err");
  }
}

function bindEvents() {
  dom.modeFb.addEventListener("click", () => setSource("fb"));
  dom.modeYt.addEventListener("click", () => setSource("yt"));

  dom.btnPaste.addEventListener("click", handlePaste);
  dom.btnConvert.addEventListener("click", handleConvert);
  dom.btnCopy.addEventListener("click", handleCopy);
  dom.btnOpen.addEventListener("click", handleOpen);

  dom.inp.addEventListener("keydown", (event) => {
    if (event.key === "Enter") handleConvert(event);
  });

  dom.inp.addEventListener("input", () => {
    setStatus("");
    resetOutput();
  });
}

function init() {
  applySourceUi();
  bindEvents();
}

init();
