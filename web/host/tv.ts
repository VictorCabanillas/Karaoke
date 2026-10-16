/**
 * TV mode: full screen, big lyrics, controls that hide when the mouse is still, and the room QR
 * on screen between songs. Exiting full screen (Esc) leaves TV mode too.
 */

const IDLE_MS = 3000;
let idleTimer: ReturnType<typeof setTimeout> | undefined;

export function isTv(): boolean {
  return document.body.classList.contains("tv");
}

function wake(): void {
  document.body.classList.add("tv-awake");
  clearTimeout(idleTimer);
  idleTimer = setTimeout(() => document.body.classList.remove("tv-awake"), IDLE_MS);
}

export async function toggleTv(): Promise<void> {
  if (isTv()) {
    document.body.classList.remove("tv", "tv-awake");
    if (document.fullscreenElement) await document.exitFullscreen().catch(() => undefined);
    return;
  }
  document.body.classList.add("tv");
  wake();
  // Full screen needs a click or key press; TV mode still works (bigger text) if it is refused.
  await document.documentElement.requestFullscreen?.().catch(() => undefined);
}

document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement) document.body.classList.remove("tv", "tv-awake");
});
document.addEventListener("mousemove", () => isTv() && wake());
document.addEventListener("keydown", () => isTv() && wake());

export function tvButton(): HTMLButtonElement {
  const button = document.createElement("button");
  button.className = "secondary small";
  button.textContent = "📺 Modo TV";
  button.title = "Pantalla completa para la tele (tecla F)";
  button.addEventListener("click", () => void toggleTv());
  return button;
}
