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

// The exercise choice on Treningi → Ćwiczenia: type to filter the list (ignoring case and Polish
// accents), and "Wszystkie ćwiczenia", which clears the others (nothing checked = all).
const fold = (text) => text.normalize("NFD").replace(/\p{M}/gu, "").replace(/ł/g, "l").toLowerCase();

document.querySelectorAll("[data-multi-select]").forEach((select) => {
  const search = select.querySelector("[data-multi-search]");
  const all = select.querySelector("[data-multi-all]");
  const empty = select.querySelector("[data-multi-empty]");
  const options = [...select.querySelectorAll(".multi-select-options .multi-option")];
  const boxes = options.map((option) => option.querySelector("input"));
  const summary = select.querySelector("summary");

  const showChoice = () => {
    const chosen = options.filter((o) => o.querySelector("input").checked).map((o) => o.textContent.trim());
    summary.textContent = chosen.length ? chosen.join(", ") : "Wszystkie ćwiczenia";
    all.checked = chosen.length === 0;
  };
  all.addEventListener("change", () => {
    if (all.checked) boxes.forEach((box) => { box.checked = false; });
    showChoice();
  });
  boxes.forEach((box) => box.addEventListener("change", showChoice));

  search.hidden = false;
  search.addEventListener("input", () => {
    const wanted = fold(search.value.trim());
    let shown = 0;
    options.forEach((option) => {
      const match = !wanted || fold(option.textContent).includes(wanted);
      option.hidden = !match;
      shown += match;
    });
    all.closest("label").hidden = Boolean(wanted);
    empty.hidden = shown > 0;
  });
  // Enter in the search box doesn't send the form: it ticks the first exercise shown.
  search.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const first = options.find((option) => !option.hidden);
    if (first) {
      const box = first.querySelector("input");
      box.checked = !box.checked;
      showChoice();
    }
  });
  select.addEventListener("toggle", () => {
    if (select.open) search.focus();
  });
  // A click outside closes the list.
  document.addEventListener("click", (event) => {
    if (select.open && !select.contains(event.target)) select.open = false;
  });
});

// Treningi: fold the calendar away and back, remembered in the browser like the sidebar.
const calendarToggle = document.querySelector("[data-calendar-toggle]");
if (calendarToggle) {
  const showState = () => {
    calendarToggle.setAttribute("aria-expanded", String(!root.classList.contains("calendar-folded")));
  };
  calendarToggle.hidden = false;
  showState();
  calendarToggle.addEventListener("click", () => {
    const folded = root.classList.toggle("calendar-folded");
    try { localStorage.setItem("calendar", folded ? "folded" : "open"); } catch (e) {}
    showState();
  });
}
