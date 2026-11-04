// Sidebar: fold to icons (remembered in localStorage), the drawer on phones, the trainee
// dropdown (each option's value is the page to open).
const root = document.documentElement;

document.querySelector("[data-fold]")?.addEventListener("click", () => {
  const folded = root.classList.toggle("folded");
  try { localStorage.setItem("sidebar", folded ? "folded" : "open"); } catch (e) {}
});

document.querySelector("[data-open-menu]")?.addEventListener("click", () => {
  root.classList.add("menu-open");
});
document.querySelector("[data-close-menu]")?.addEventListener("click", () => {
  root.classList.remove("menu-open");
});

const picker = document.querySelector("[data-trainee-picker]");
picker?.addEventListener("change", (event) => {
  if (event.target.value) window.location.href = event.target.value;
});

// A trainee page clicked before a trainee is chosen: point at the dropdown.
for (const item of document.querySelectorAll("nav .disabled")) {
  item.addEventListener("click", () => {
    root.classList.remove("folded");
    picker?.focus();
    picker?.classList.add("attention");
    setTimeout(() => picker?.classList.remove("attention"), 1500);
  });
  item.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      item.click();
    }
  });
}

// Forms with data-confirm="question" ask before they're sent (an empty one doesn't ask).
document.addEventListener("submit", (event) => {
  const question = event.target.dataset?.confirm;
  if (question && !confirm(question)) event.preventDefault();
});
