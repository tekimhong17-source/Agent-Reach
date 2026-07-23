/**
 * Card scanning and validation. OCR runs entirely in the browser via
 * tesseract.js — captured frames never leave the device.
 */
const CardScanner = (() => {
  let stream = null;

  async function startCamera(videoEl) {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment", width: { ideal: 1280 } },
      audio: false,
    });
    videoEl.srcObject = stream;
  }

  function stopCamera(videoEl) {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop());
      stream = null;
    }
    if (videoEl) videoEl.srcObject = null;
  }

  /** Luhn checksum — true for a structurally valid card number. */
  function luhnValid(number) {
    const digits = number.replace(/\D/g, "");
    if (digits.length < 13 || digits.length > 19) return false;
    let sum = 0;
    let double = false;
    for (let i = digits.length - 1; i >= 0; i--) {
      let d = +digits[i];
      if (double) {
        d *= 2;
        if (d > 9) d -= 9;
      }
      sum += d;
      double = !double;
    }
    return sum % 10 === 0;
  }

  function detectBrand(number) {
    const d = number.replace(/\D/g, "");
    if (/^4/.test(d)) return "Visa";
    if (/^(5[1-5]|2[2-7])/.test(d)) return "Mastercard";
    if (/^3[47]/.test(d)) return "Amex";
    if (/^(6011|65|64[4-9])/.test(d)) return "Discover";
    if (/^35/.test(d)) return "JCB";
    if (/^62/.test(d)) return "UnionPay";
    return "Card";
  }

  /** Pull a Luhn-valid card number and an expiry date out of raw OCR text. */
  function parseOcrText(text) {
    const result = { number: null, expiry: null };
    const numberMatches = text.match(/(?:\d[ -]?){13,19}/g) || [];
    for (const m of numberMatches) {
      const digits = m.replace(/\D/g, "");
      if (luhnValid(digits)) {
        result.number = digits;
        break;
      }
    }
    const expiryMatch = text.match(/(0[1-9]|1[0-2])\s*[\/\-]\s*(\d{2})\b/);
    if (expiryMatch) result.expiry = `${expiryMatch[1]}/${expiryMatch[2]}`;
    return result;
  }

  /** Capture the current video frame and OCR it locally. */
  async function scanFrame(videoEl, canvasEl, onStatus) {
    canvasEl.width = videoEl.videoWidth;
    canvasEl.height = videoEl.videoHeight;
    canvasEl.getContext("2d").drawImage(videoEl, 0, 0);
    onStatus("Reading card…");
    const { data } = await Tesseract.recognize(canvasEl, "eng", {
      logger: (m) => {
        if (m.status === "recognizing text") {
          onStatus(`Reading card… ${Math.round(m.progress * 100)}%`);
        }
      },
    });
    return parseOcrText(data.text || "");
  }

  return { startCamera, stopCamera, scanFrame, luhnValid, detectBrand };
})();
