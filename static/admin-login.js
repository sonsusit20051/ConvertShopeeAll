const inputKey = document.getElementById("admin_key");
const btnLogin = document.getElementById("btn_login");
const msg = document.getElementById("msg");

function setMsg(text, isError = true) {
    msg.textContent = text;
    msg.style.color = isError ? "#a52c2f" : "#1b6a34";
}

async function loginAdmin() {
    const key = inputKey.value.trim();
    if (!key) {
        setMsg("Vui lòng nhập admin key.");
        return;
    }

    btnLogin.disabled = true;
    btnLogin.textContent = "Đang đăng nhập...";
    setMsg("");

    try {
        const resp = await fetch("/api/admin/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.detail || "Đăng nhập thất bại.");
        }
        setMsg("Đăng nhập thành công.", false);
        window.location.href = "/admin";
    } catch (err) {
        setMsg(err.message || "Đăng nhập thất bại.");
    } finally {
        btnLogin.disabled = false;
        btnLogin.textContent = "Đăng nhập";
    }
}

btnLogin.addEventListener("click", loginAdmin);
inputKey.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        loginAdmin();
    }
});
