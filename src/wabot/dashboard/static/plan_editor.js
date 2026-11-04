// The training plan editor (plan_edit.html): add, remove and reorder days and exercises, add
// replacements as chips. On submit the whole plan goes to the server as one JSON field, `plan`
// (dashboard/plan_editor.py checks it). Leaving the page with unsaved changes asks first.
(() => {
  const form = document.querySelector("[data-plan-editor]");
  if (!form) return;
  const days = form.querySelector("[data-days]");
  const clone = (name) => form.querySelector(`template[data-${name}-template]`).content.firstElementChild.cloneNode(true);
  // Shown again after a failed save: what's in the form isn't saved yet.
  let dirty = form.hasAttribute("data-unsaved");

  // The notes box grows with its text (up to about 25 lines, then it scrolls).
  const notes = form.querySelector("[data-field=notes]");
  const fit = () => {
    notes.style.height = "auto";
    notes.style.height = `${Math.min(notes.scrollHeight + 2, 560)}px`;
  };
  notes.addEventListener("input", fit);
  fit();

  form.addEventListener("input", () => { dirty = true; });
  form.addEventListener("change", () => { dirty = true; });
  window.addEventListener("beforeunload", (event) => {
    if (dirty) event.preventDefault();
  });

  form.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button.type === "submit") return;

    if (button.matches("[data-add-day]")) {
      const day = clone("day");
      days.appendChild(day);
      day.querySelector("[data-field=name]").focus();
    } else if (button.matches("[data-add-exercise]")) {
      const row = clone("exercise");
      button.closest("[data-day]").querySelector("[data-exercises]").appendChild(row);
      row.querySelector("[data-field=id]").focus();
    } else if (button.matches("[data-move]")) {
      const item = button.closest("[data-exercise], [data-day]");
      const sibling = button.dataset.move === "-1" ? item.previousElementSibling : item.nextElementSibling;
      if (!sibling) return;
      if (button.dataset.move === "-1") sibling.before(item); else sibling.after(item);
      button.focus();
    } else if (button.matches("[data-remove]")) {
      const item = button.closest("[data-exercise], [data-day]");
      if (item.matches("[data-day]")) {
        const name = item.querySelector("[data-field=name]").value.trim();
        if (!confirm(name ? `Usunąć dzień „${name}” z planu?` : "Usunąć ten dzień z planu?")) return;
      }
      item.remove();
    } else if (button.matches("[data-remove-replacement]")) {
      button.closest("[data-replacement]").remove();
    } else {
      return;
    }
    dirty = true;
  });

  // "+ dodaj zamiennik": the chosen exercise becomes a chip, unless it's already there or it's
  // the exercise itself.
  form.addEventListener("change", (event) => {
    const select = event.target.closest("[data-add-replacement]");
    if (!select || !select.value) return;
    const row = select.closest("[data-exercise]");
    const chips = row.querySelector("[data-replacements]");
    const id = select.value;
    const taken = [...chips.querySelectorAll("[data-replacement]")].some((c) => c.dataset.replacement === id);
    if (!taken && id !== row.querySelector("[data-field=id]").value) {
      const chip = clone("chip");
      chip.dataset.replacement = id;
      chip.querySelector("[data-chip-label]").textContent = select.selectedOptions[0].textContent;
      chips.appendChild(chip);
    }
    select.value = "";
  });

  form.addEventListener("submit", () => {
    const value = (root, field) => root.querySelector(`[data-field=${field}]`).value;
    const plan = {
      notes: value(form, "notes"),
      days: [...days.querySelectorAll(":scope > [data-day]")].map((day) => ({
        name: value(day.querySelector(".plan-day-head"), "name"),
        exercises: [...day.querySelectorAll("[data-exercise]")].map((row) => ({
          id: value(row, "id"),
          sets: value(row, "sets"),
          reps: value(row, "reps"),
          weight: value(row, "weight"),
          note: value(row, "note"),
          replacements: [...row.querySelectorAll("[data-replacement]")].map((c) => c.dataset.replacement),
        })),
      })),
    };
    form.elements.plan.value = JSON.stringify(plan);
    dirty = false;
  });
})();
