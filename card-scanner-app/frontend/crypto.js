/**
 * Client-side encryption for CardVault.
 * AES-256-GCM with a key derived from the user's vault passphrase via
 * PBKDF2-SHA256 (310k iterations). Each card gets a fresh salt and IV, so the
 * server only ever stores ciphertext it cannot decrypt.
 */
const VaultCrypto = (() => {
  const PBKDF2_ITERATIONS = 310000;

  const bufToB64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
  const b64ToBuf = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

  async function deriveKey(passphrase, saltBytes) {
    const material = await crypto.subtle.importKey(
      "raw", new TextEncoder().encode(passphrase), "PBKDF2", false, ["deriveKey"]
    );
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt: saltBytes, iterations: PBKDF2_ITERATIONS, hash: "SHA-256" },
      material,
      { name: "AES-GCM", length: 256 },
      false,
      ["encrypt", "decrypt"]
    );
  }

  /** Encrypt an object; returns { ciphertext, iv, salt } as base64 strings. */
  async function encrypt(passphrase, data) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const key = await deriveKey(passphrase, salt);
    const plaintext = new TextEncoder().encode(JSON.stringify(data));
    const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext);
    return { ciphertext: bufToB64(ciphertext), iv: bufToB64(iv), salt: bufToB64(salt) };
  }

  /** Decrypt { ciphertext, iv, salt } back into the original object. Throws on wrong passphrase. */
  async function decrypt(passphrase, blob) {
    const key = await deriveKey(passphrase, b64ToBuf(blob.salt));
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: b64ToBuf(blob.iv) }, key, b64ToBuf(blob.ciphertext)
    );
    return JSON.parse(new TextDecoder().decode(plaintext));
  }

  return { encrypt, decrypt };
})();
