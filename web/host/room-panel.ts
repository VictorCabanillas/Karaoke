import { h } from "../shared/dom";
import type { Player, RoomMsg } from "../shared/types";
import { hostSocket } from "./connection";

/** QR code + room code + connected players. */
export class RoomPanel {
  readonly el: HTMLElement;
  private qr = h("img", { class: "qr", alt: "Código QR para unirse" });
  private code = h("strong", { class: "room-code" });
  private url = h("small", { class: "hint url" });
  private warning = h("p", { class: "warning", hidden: true });
  private players = h("ul", { class: "players" });

  constructor(compact = false) {
    this.el = h(
      "aside",
      { class: `room-panel${compact ? " compact" : ""}` },
      h("h2", null, "Sala ", this.code),
      this.qr,
      h("p", { class: "hint" }, "Escanea el QR con el móvil (misma Wi-Fi) para usarlo de micrófono."),
      this.url,
      this.warning,
      this.players,
    );
    hostSocket.on("room", (msg) => this.render(msg));
  }

  render(room: RoomMsg): void {
    this.code.textContent = room.code;
    this.url.textContent = room.mic_url;
    const src = `/api/room/qr.svg?u=${encodeURIComponent(room.mic_url)}`;
    if (this.qr.getAttribute("src") !== src) this.qr.src = src;
    this.warning.hidden = room.https;
    this.warning.textContent =
      "Sin HTTPS los móviles no pueden usar el micrófono. Genera certificados (ver README) y reinicia el servidor.";
    this.players.replaceChildren(
      ...(room.players.length
        ? room.players.map((p) => this.player(p))
        : [h("li", { class: "hint" }, "Todavía no hay nadie conectado.")]),
    );
  }

  private player(p: Player): HTMLElement {
    const state = !p.connected ? "desconectado" : p.calibrated ? "listo" : "sin calibrar";
    return h(
      "li",
      { class: p.connected ? "" : "offline" },
      h("span", { class: "dot", style: `background:${p.color}` }),
      h("span", { class: "name" }, p.name),
      h("small", { class: "hint" }, state),
      h(
        "button",
        {
          class: "icon",
          title: `Quitar a ${p.name}`,
          onclick: () => hostSocket.send({ type: "remove_player", player_id: p.id }),
        },
        "✕",
      ),
    );
  }
}
