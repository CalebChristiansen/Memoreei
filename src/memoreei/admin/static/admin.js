// Copy buttons: <button data-copy="element-id">.
// navigator.clipboard only exists in a secure context (https or localhost), so the dashboard
// opened over plain http from a phone on the LAN falls back to execCommand, and failing that
// selects the text and asks the person to copy it themselves.
function selectText(el) {
  const range = document.createRange();
  range.selectNodeContents(el);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function legacyCopy(el) {
  selectText(el);
  try { return document.execCommand("copy"); } catch (_) { return false; }
}

function say(button, text) {
  const label = button.dataset.label || button.textContent;
  button.dataset.label = label;
  button.textContent = text;
  setTimeout(() => { button.textContent = label; }, 2000);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const el = document.getElementById(button.dataset.copy);
  if (!el) return;
  const text = el.textContent.trim();
  const fallback = () => say(button, legacyCopy(el) ? "Copied" : "Selected: copy it");
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => say(button, "Copied"), fallback);
  } else {
    fallback();
  }
});
