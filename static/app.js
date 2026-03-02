const inputEl = document.getElementById("input_url");
const resultEl = document.getElementById("result_link");
const statusEl = document.getElementById("status_text");
const btnPaste = document.getElementById("btn_paste");
const btnConvert = document.getElementById("btn_convert");
const btnCopy = document.getElementById("btn_copy");
const btnOpen = document.getElementById("btn_open");

let latestAffiliateLink = "";
let cooldownUntil = 0;
let cooldownTimer = null;

function setStatus(message, isError = false) {
    statusEl.textContent = message;
    statusEl.style.color = isError ? "#9c2729" : "#60708f";
}

function updateButtonsState() {
    const now = Date.now();
    const inCooldown = now < cooldownUntil;
    btnConvert.disabled = inCooldown;
    if (inCooldown) {
        const remainSec = Math.ceil((cooldownUntil - now) / 1000);
        btnConvert.textContent = `Chờ ${remainSec}s`;
    } else {
        btnConvert.textContent = "Nhận voucher";
    }
}

function startCooldown(seconds) {
    cooldownUntil = Date.now() + seconds * 1000;
    updateButtonsState();
    if (cooldownTimer) {
        clearInterval(cooldownTimer);
    }
    cooldownTimer = setInterval(() => {
        updateButtonsState();
        if (Date.now() >= cooldownUntil) {
            clearInterval(cooldownTimer);
            cooldownTimer = null;
            setStatus("Đã hết thời gian chờ. Bạn có thể convert link tiếp theo.");
        }
    }, 250);
}

async function convertLink() {
    const input = inputEl.value.trim();
    if (!input) {
        setStatus("Vui lòng nhập link Shopee.", true);
        return;
    }

    btnConvert.disabled = true;
    btnConvert.textContent = "Đang xử lý...";
    setStatus("Đang convert link...");

    try {
        const resp = await fetch("/api/convert", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ input_url: input })
        });

        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.detail || data.message || "Convert thất bại.");
        }

        latestAffiliateLink = data.affiliate_link;
        resultEl.textContent = latestAffiliateLink;
        setStatus("Convert thành công. Bạn có thể sao chép hoặc mở link.");
        startCooldown(10);
    } catch (err) {
        setStatus(err.message || "Có lỗi xảy ra khi convert.", true);
    } finally {
        updateButtonsState();
    }
}

async function pasteClipboard() {
    try {
        const text = await navigator.clipboard.readText();
        inputEl.value = (text || "").trim();
        if (inputEl.value) {
            setStatus("Đã dán link từ clipboard.");
        }
    } catch (err) {
        setStatus("Không đọc được clipboard. Hãy dán thủ công.", true);
    }
}

async function copyResult() {
    if (!latestAffiliateLink) {
        setStatus("Chưa có link để sao chép.", true);
        return;
    }
    try {
        await navigator.clipboard.writeText(latestAffiliateLink);
        setStatus("Đã sao chép link kết quả.");
    } catch (err) {
        setStatus("Sao chép thất bại.", true);
    }
}

function openResult() {
    if (!latestAffiliateLink) {
        setStatus("Chưa có link để mở.", true);
        return;
    }
    window.open(latestAffiliateLink, "_blank", "noopener,noreferrer");
}

btnPaste.addEventListener("click", pasteClipboard);
btnConvert.addEventListener("click", convertLink);
btnCopy.addEventListener("click", copyResult);
btnOpen.addEventListener("click", openResult);

inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        if (!btnConvert.disabled) {
            convertLink();
        }
    }
});

updateButtonsState();
