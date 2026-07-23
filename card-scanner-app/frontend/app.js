/** CardVault UI: auth, vault listing, scanning flow, and the Pro paywall. */
(() => {
  const $ = (id) => document.getElementById(id);
  let token = sessionStorage.getItem("cardvault_token");
  let me = null;

  // ---------- api ----------
  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(path, { ...options, headers });
    const body = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) {
      const detail = body && body.detail ? body.detail : `Request failed (${res.status})`;
      const err = new Error(typeof detail === "string" ? detail : "Request failed");
      err.status = res.status;
      throw err;
    }
    return body;
  }

  // ---------- auth ----------
  $("auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const mode = e.submitter && e.submitter.dataset.mode === "register" ? "register" : "login";
    $("auth-error").textContent = "";
    try {
      const body = JSON.stringify({
        email: $("auth-email").value,
        password: $("auth-password").value,
      });
      const data = await api(`/api/${mode}`, { method: "POST", body });
      token = data.token;
      sessionStorage.setItem("cardvault_token", token);
      await enterVault();
    } catch (err) {
      $("auth-error").textContent = err.message;
    }
  });

  $("logout-btn").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST" }); } catch (_) { /* best effort */ }
    token = null;
    sessionStorage.removeItem("cardvault_token");
    location.reload();
  });

  async function enterVault() {
    me = await api("/api/me");
    $("auth-view").classList.add("hidden");
    $("vault-view").classList.remove("hidden");
    $("user-box").classList.remove("hidden");
    $("user-email").textContent = me.email;
    $("plan-badge").textContent = me.plan === "pro" ? "PRO" : "FREE";
    $("plan-badge").className = `badge ${me.plan}`;
    $("free-limit").textContent = me.free_limit;
    await refreshCards();
  }

  // ---------- vault ----------
  async function refreshCards() {
    const cards = await api("/api/cards");
    const list = $("card-list");
    list.innerHTML = "";
    for (const card of cards) {
      const li = document.createElement("li");
      li.className = "card-item";
      li.innerHTML = `
        <span class="card-brand">${card.brand}</span>
        <span class="card-label"></span>
        <span class="card-last4">•••• ${card.last4}</span>
        <button class="ghost reveal">Reveal</button>
        <button class="ghost danger delete">Delete</button>`;
      li.querySelector(".card-label").textContent = card.label;
      li.querySelector(".reveal").addEventListener("click", () => openReveal(card));
      li.querySelector(".delete").addEventListener("click", async () => {
        if (!confirm(`Delete "${card.label}"? This cannot be undone.`)) return;
        await api(`/api/cards/${card.id}`, { method: "DELETE" });
        await enterVault();
      });
      list.appendChild(li);
    }

    const atLimit = me.plan !== "pro" && cards.length >= me.free_limit;
    $("paywall").classList.toggle("hidden", !atLimit);
    $("quota-note").textContent =
      me.plan === "pro"
        ? "Pro plan — unlimited cards."
        : `${cards.length}/${me.free_limit} cards used on the free plan.`;
  }

  // ---------- paywall ----------
  $("upgrade-btn").addEventListener("click", async () => {
    $("billing-error").textContent = "";
    try {
      const { url } = await api("/api/billing/checkout", { method: "POST" });
      location.href = url;
    } catch (err) {
      $("billing-error").textContent = err.message;
    }
  });

  // ---------- scanning ----------
  const modal = $("scan-modal");
  let scanned = { number: null };

  $("scan-btn").addEventListener("click", async () => {
    if (me.plan !== "pro" && me.cards >= me.free_limit) {
      $("paywall").classList.remove("hidden");
      $("paywall").scrollIntoView({ behavior: "smooth" });
      return;
    }
    modal.showModal();
    $("card-form").classList.add("hidden");
    $("scan-status").textContent = "";
    try {
      await CardScanner.startCamera($("camera"));
    } catch (err) {
      $("scan-status").textContent = `Camera unavailable (${err.message}). You can enter the card manually.`;
    }
  });

  $("capture-btn").addEventListener("click", async () => {
    try {
      const result = await CardScanner.scanFrame($("camera"), $("capture-canvas"),
        (msg) => ($("scan-status").textContent = msg));
      if (result.number) {
        $("card-number").value = result.number;
        if (result.expiry) $("card-expiry").value = result.expiry;
        $("scan-status").textContent = "Card detected — check the details below.";
        showCardForm();
      } else {
        $("scan-status").textContent = "No valid card number found. Try again with better lighting, or enter manually.";
      }
    } catch (err) {
      $("scan-status").textContent = `Scan failed: ${err.message}`;
    }
  });

  $("manual-btn").addEventListener("click", showCardForm);

  function showCardForm() {
    $("card-form").classList.remove("hidden");
    updateCardMeta();
  }

  $("card-number").addEventListener("input", updateCardMeta);
  function updateCardMeta() {
    const number = $("card-number").value;
    if (!number) { $("card-meta").textContent = ""; return; }
    const valid = CardScanner.luhnValid(number);
    const brand = CardScanner.detectBrand(number);
    $("card-meta").textContent = valid ? `✓ Valid ${brand}` : "✗ Not a valid card number";
  }

  $("close-scan-btn").addEventListener("click", closeScanModal);
  function closeScanModal() {
    CardScanner.stopCamera($("camera"));
    $("card-form").reset();
    $("card-error").textContent = "";
    modal.close();
  }

  $("card-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("card-error").textContent = "";
    const number = $("card-number").value.replace(/\D/g, "");
    if (!CardScanner.luhnValid(number)) {
      $("card-error").textContent = "That card number is not valid.";
      return;
    }
    try {
      // Encrypt the sensitive fields client-side; only ciphertext + last4 go up.
      const blob = await VaultCrypto.encrypt($("vault-passphrase").value, {
        number,
        expiry: $("card-expiry").value || null,
        holder: $("card-holder").value || null,
      });
      await api("/api/cards", {
        method: "POST",
        body: JSON.stringify({
          label: $("card-label").value,
          brand: CardScanner.detectBrand(number),
          last4: number.slice(-4),
          ...blob,
        }),
      });
      closeScanModal();
      await enterVault();
    } catch (err) {
      $("card-error").textContent = err.message;
    }
  });

  // ---------- reveal ----------
  let revealCard = null;
  function openReveal(card) {
    revealCard = card;
    $("reveal-title").textContent = `${card.label} (•••• ${card.last4})`;
    $("reveal-output").classList.add("hidden");
    $("reveal-output").textContent = "";
    $("reveal-error").textContent = "";
    $("reveal-form").reset();
    $("reveal-modal").showModal();
  }

  $("reveal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("reveal-error").textContent = "";
    try {
      const data = await VaultCrypto.decrypt($("reveal-passphrase").value, revealCard);
      const pretty = [
        `Number: ${data.number.replace(/(\d{4})(?=\d)/g, "$1 ")}`,
        data.expiry ? `Expiry: ${data.expiry}` : null,
        data.holder ? `Holder: ${data.holder}` : null,
      ].filter(Boolean).join("\n");
      $("reveal-output").textContent = pretty;
      $("reveal-output").classList.remove("hidden");
    } catch (_) {
      $("reveal-error").textContent = "Wrong passphrase (or corrupted data).";
    }
  });

  $("close-reveal-btn").addEventListener("click", () => $("reveal-modal").close());

  // ---------- boot ----------
  if (new URLSearchParams(location.search).get("upgraded") === "1") {
    history.replaceState(null, "", "/");
  }
  if (token) {
    enterVault().catch(() => {
      token = null;
      sessionStorage.removeItem("cardvault_token");
    });
  }
})();
