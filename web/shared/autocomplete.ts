import { debounce, h } from "./dom";

export interface AutocompleteOptions {
  /** Suggestions for the typed text (may reject: no suggestions are shown then). */
  fetch: (query: string) => Promise<string[]>;
  /** Called when a suggestion is chosen (click or Enter on a highlighted one). */
  onPick: (value: string) => void;
  delayMs?: number;
  minChars?: number;
}

/**
 * Dropdown of suggestions under a text input: ↑/↓ to move, Enter to pick, Esc to close.
 * Responses that arrive after the user typed something else are ignored.
 */
export function attachAutocomplete(input: HTMLInputElement, options: AutocompleteOptions): HTMLElement {
  const list = h("ul", { class: "suggestions", role: "listbox", hidden: true });
  const wrapper = h("div", { class: "autocomplete" });
  input.replaceWith(wrapper);
  wrapper.append(input, list);
  input.setAttribute("autocomplete", "off");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");

  let items: string[] = [];
  let active = -1;
  let request = 0;

  const close = () => {
    list.hidden = true;
    active = -1;
    input.setAttribute("aria-expanded", "false");
  };

  const highlight = (index: number) => {
    active = index;
    [...list.children].forEach((el, i) => el.classList.toggle("active", i === index));
  };

  const pick = (value: string) => {
    input.value = value;
    close();
    options.onPick(value);
  };

  const render = () => {
    list.replaceChildren(
      ...items.map((text, i) =>
        h(
          "li",
          {
            role: "option",
            // mousedown fires before the input's blur: pick before the list closes.
            onmousedown: (e: MouseEvent) => {
              e.preventDefault();
              pick(text);
            },
            onmouseenter: () => highlight(i),
          },
          text,
        ),
      ),
    );
    active = -1;
    list.hidden = items.length === 0 || document.activeElement !== input;
    input.setAttribute("aria-expanded", String(!list.hidden));
  };

  const load = debounce(async (query: string) => {
    const id = ++request;
    let result: string[] = [];
    try {
      result = await options.fetch(query);
    } catch {
      result = [];
    }
    if (id !== request) return; // the user kept typing
    items = result.filter((s) => s.toLowerCase() !== query.toLowerCase());
    render();
  }, options.delayMs ?? 200);

  input.addEventListener("input", () => {
    const query = input.value.trim();
    if (query.length < (options.minChars ?? 2)) {
      request++;
      items = [];
      close();
      return;
    }
    load(query);
  });
  input.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      highlight((active + step + items.length) % items.length);
      input.value = items[active];
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault();
      pick(items[active]);
    } else if (e.key === "Escape") {
      close();
    }
  });
  input.addEventListener("blur", close);
  input.addEventListener("focus", () => {
    if (items.length) render();
  });
  // A normal Enter (no highlighted suggestion) submits the form: the list is not needed any more.
  input.form?.addEventListener("submit", () => {
    request++;
    close();
  });
  return wrapper;
}
