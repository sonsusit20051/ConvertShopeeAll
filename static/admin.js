const sumTotal = document.getElementById("sum_total");
const sumSuccess = document.getElementById("sum_success");
const sumFailed = document.getElementById("sum_failed");
const sumToday = document.getElementById("sum_today");

const fbTotal = document.getElementById("fb_total");
const fbSuccess = document.getElementById("fb_success");
const fbFailed = document.getElementById("fb_failed");
const fbToday = document.getElementById("fb_today");

const ytTotal = document.getElementById("yt_total");
const ytSuccess = document.getElementById("yt_success");
const ytFailed = document.getElementById("yt_failed");
const ytToday = document.getElementById("yt_today");

const historyBody = document.getElementById("history_body");
const btnReload = document.getElementById("btn_reload");
const btnLogout = document.getElementById("btn_logout");

function escHtml(text) {
  if (text === null || text === undefined) {
    return "";
  }
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function rowCell(value, withEllipsis = false) {
  const cls = withEllipsis ? "ellipsis" : "";
  return `<td class="${cls}" title="${escHtml(value)}">${escHtml(value)}</td>`;
}

function sourcePill(source) {
  const normalized = String(source || "").toLowerCase() === "yt" ? "yt" : "fb";
  const label = normalized === "yt" ? "YT/Shopee" : "Facebook";
  return `<span class="source-pill ${normalized}">${label}</span>`;
}

function toInt(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

async function loadStats() {
  btnReload.disabled = true;
  btnReload.textContent = "Đang tải...";
  try {
    const resp = await fetch("/api/admin/stats", { cache: "no-store" });
    if (resp.status === 401) {
      window.location.href = "/admin/login";
      return;
    }
    const data = await resp.json();
    if (!resp.ok || !data.success) {
      throw new Error(data.detail || "Không tải được dữ liệu admin.");
    }

    const summary = data.summary || {};
    const bySource = summary.by_source || {};
    const fb = bySource.fb || {};
    const yt = bySource.yt || {};

    sumTotal.textContent = toInt(summary.total);
    sumSuccess.textContent = toInt(summary.success);
    sumFailed.textContent = toInt(summary.failed);
    sumToday.textContent = toInt(summary.today);

    fbTotal.textContent = toInt(fb.total);
    fbSuccess.textContent = toInt(fb.success);
    fbFailed.textContent = toInt(fb.failed);
    fbToday.textContent = toInt(fb.today);

    ytTotal.textContent = toInt(yt.total);
    ytSuccess.textContent = toInt(yt.success);
    ytFailed.textContent = toInt(yt.failed);
    ytToday.textContent = toInt(yt.today);

    historyBody.innerHTML = (data.history || [])
      .map((row) => {
        return `
          <tr>
            <td>${toInt(row.id)}</td>
            <td>${escHtml(row.created_at)}</td>
            <td>${sourcePill(row.source)}</td>
            ${rowCell(row.input_url, true)}
            ${rowCell(row.affiliate_link || row.resolved_url || "", true)}
            ${rowCell(row.client_ip || "-", false)}
            <td class="${row.success ? "ok" : "bad"}">${row.success ? "OK" : "FAIL"}</td>
            ${rowCell(row.error_message || "", true)}
          </tr>
        `;
      })
      .join("");
  } catch (err) {
    historyBody.innerHTML = `<tr><td colspan="8">${escHtml(err.message)}</td></tr>`;
  } finally {
    btnReload.disabled = false;
    btnReload.textContent = "Tải lại";
  }
}

async function logout() {
  btnLogout.disabled = true;
  btnLogout.textContent = "Đang thoát...";
  try {
    await fetch("/api/admin/logout", { method: "POST" });
  } finally {
    window.location.href = "/admin/login";
  }
}

btnReload.addEventListener("click", loadStats);
btnLogout.addEventListener("click", logout);
loadStats();
