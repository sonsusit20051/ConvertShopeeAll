const sumTotal = document.getElementById("sum_total");
const sumSuccess = document.getElementById("sum_success");
const sumFailed = document.getElementById("sum_failed");
const sumToday = document.getElementById("sum_today");
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

async function loadStats() {
    btnReload.disabled = true;
    btnReload.textContent = "Đang tải...";
    try {
        const resp = await fetch("/api/admin/stats");
        if (resp.status === 401) {
            window.location.href = "/admin/login";
            return;
        }
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.detail || "Không tải được dữ liệu admin.");
        }

        sumTotal.textContent = data.summary.total;
        sumSuccess.textContent = data.summary.success;
        sumFailed.textContent = data.summary.failed;
        sumToday.textContent = data.summary.today;

        historyBody.innerHTML = data.history
            .map((row) => {
                return `
                    <tr>
                        <td>${row.id}</td>
                        <td>${escHtml(row.created_at)}</td>
                        ${rowCell(row.input_url, true)}
                        ${rowCell(row.resolved_url, true)}
                        ${rowCell(row.affiliate_link, true)}
                        <td class="${row.success ? "ok" : "bad"}">${row.success ? "OK" : "FAIL"}</td>
                        ${rowCell(row.error_message || "", true)}
                    </tr>
                `;
            })
            .join("");
    } catch (err) {
        historyBody.innerHTML = `<tr><td colspan="7">${escHtml(err.message)}</td></tr>`;
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
