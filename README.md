# Karaoke entre amigos

Karaoke para jugar en casa: la pantalla principal se abre en el navegador del portátil (conectable a la tele) y
los móviles de los jugadores hacen de micrófono.

## Instalación rápida

**Windows:** doble clic en `setup.cmd` (o `.\setup.cmd` en una terminal). **macOS / Linux:** `./setup.sh`.

El script instala lo que falte (uv, Node.js, ffmpeg y mkcert; en Windows con `winget`), las dependencias de Python
con el procesado de canciones, compila el frontend y crea los certificados HTTPS. Se puede repetir sin problema.
Opciones:

| Windows | macOS / Linux | Para qué |
|---|---|---|
| `-NoProcessing` | `--no-processing` | Sin PyTorch/Demucs/Whisper (varios GB menos; sin separar voz ni puntuar afinación) |
| `-NoCerts` | `--no-certs` | Sin HTTPS (los móviles no podrán usar el micrófono) |
| `-DryRun` | `--dry-run` | Solo muestra lo que haría |

Después: doble clic en `start.cmd` (o `uv run python -m server`) y abre `https://127.0.0.1:8000/`. Las secciones siguientes explican cada paso por
si prefieres hacerlo a mano.

### Instalar en otro ordenador (Windows)

1. **Consigue el código.** En GitHub: *Code → Download ZIP* (si el repositorio es privado, da acceso a tu amigo o
   pásale un ZIP hecho con `git archive --format zip -o karaoke.zip HEAD`). Descomprímelo en una **ruta corta**, por
   ejemplo `C:\Karaoke` (PyTorch tiene rutas muy largas y Windows limita a 260 caracteres).
2. **Doble clic en `setup.cmd`.** Necesita internet y tarda un rato (descarga varios GB con el procesado). Windows
   pedirá confirmar las instalaciones y la autoridad de certificados de mkcert. Sin GPU NVIDIA, o con poco espacio,
   usa `setup.cmd -NoProcessing` (el karaoke funciona igual, pero sin separar la voz ni puntuar la afinación).
3. **Doble clic en `start.cmd`.** Arranca el servidor y abre la pantalla principal. La primera vez **Windows
   preguntará por el firewall: pulsa «Permitir acceso»**. La Wi-Fi debe estar como red *Privada* (Configuración →
   Red e Internet → la Wi-Fi → Tipo de perfil de red); si está como *Pública*, Windows puede bloquear a los móviles.
4. **Si algo no va: `start.cmd --check`.** Revisa página compilada, certificado (incluida la IP actual), procesado,
   ffmpeg, puerto, red y firewall, y dice cómo arreglar cada cosa.
5. Los móviles, en la misma Wi-Fi, escanean el QR y aceptan el aviso del certificado (o instalan la CA, ver arriba).

Antes de la quedada conviene procesar las canciones (sin GPU tarda varios minutos cada una) y probar con un móvil.
Si el portátil cambia de IP (otra red, otro día), `start.cmd` avisa: vuelve a ejecutar
`uv run python scripts/make_certs.py`.

### Desinstalar

**Windows:** `uninstall.cmd`. **macOS / Linux:** `./uninstall.sh`.

Quita solo lo que no tenías antes: el script de instalación apunta en `.setup-state` qué herramientas instaló, si
creó la autoridad de certificados de mkcert y qué cachés de modelos no existían, y el desinstalador deshace
exactamente eso (incluidas las cachés de uv y npm si fue setup quien instaló uv o Node). Además borra `.venv`,
`node_modules`, `web/dist` y `certs`. Antes de borrar enseña la lista y pide confirmación.

| Windows | macOS / Linux | Para qué |
|---|---|---|
| `-RemoveData` | `--remove-data` | Borra también `data/` (canciones importadas, pistas procesadas, sesión de Spotify) |
| `-Yes` | `--yes` | No pide confirmación |
| `-DryRun` | `--dry-run` | Solo muestra lo que haría |

## Requisitos

- Python 3.11+ y [uv](https://docs.astral.sh/uv/)
- Node.js LTS y npm
- `ffmpeg` (opcional pero recomendado: normaliza el volumen y permite formatos como `.wma`)
- [`mkcert`](https://github.com/FiloSottile/mkcert) para usar los móviles como micrófono (necesitan HTTPS)

## Instalación manual

```sh
uv sync          # dependencias de Python
npm install      # dependencias del frontend
```

### Procesado de canciones (opcional, recomendado)

Separar la voz (Demucs), sacar la melodía de referencia (CREPE) y alinear/transcribir la letra (Whisper) necesita
dependencias pesadas (PyTorch, varios GB):

Se instalan con `uv sync` (vienen incluidas por defecto). Si no las quieres:

```sh
uv sync --no-group processing
```

En Windows y Linux se instala PyTorch con CUDA (funciona también sin GPU). La primera vez que se procesa una canción
se descargan los modelos (Demucs ~80 MB, Whisper `medium` ~1,5 GB con GPU o `small` ~500 MB en CPU).

### HTTPS para los móviles

Los navegadores de los móviles solo dejan usar el micrófono en páginas HTTPS. Una vez:

```sh
mkcert -install                          # el portátil confía en la CA local
uv run python scripts/make_certs.py      # crea certs/cert.pem y certs/key.pem
```

Con certificados en `certs/` el servidor arranca en HTTPS automáticamente. Si cambia la IP del portátil en la red,
vuelve a ejecutar `make_certs.py`.

Para que los móviles no muestren el aviso de «conexión no segura», instala en ellos la CA de mkcert (el archivo
`rootCA.pem` de la carpeta que indica `mkcert -CAROOT`; pásalo por correo, AirDrop, cable...):

- **Android:** Ajustes → Seguridad → Más ajustes de seguridad → Cifrado y credenciales → Instalar un certificado →
  Certificado de CA → elige `rootCA.pem` (el nombre exacto del menú cambia según el fabricante).
- **iPhone:** abre el archivo y acepta instalar el perfil (Ajustes → Perfil descargado → Instalar). Después:
  Ajustes → General → Información → Ajustes de confianza de certificados → activa la CA de mkcert.

Si no la instalas, también vale con aceptar el aviso del navegador la primera vez («Avanzado» → «Continuar»).

### Spotify (opcional)

Para reproducir canciones de Spotify en la pantalla principal hace falta una cuenta **Premium** (solo la del
anfitrión: los amigos no usan Spotify).

1. Crea una app en el [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) marcando
   «Web API» y «Web Playback SDK».
2. Añade como *Redirect URI* `https://127.0.0.1:8000/api/spotify/callback` (o `http://...` si no usas
   certificados; la URI exacta aparece en el panel de Spotify de la biblioteca).
3. En modo desarrollo, añade tu cuenta en «User Management» (máximo 5 usuarios).
4. Arranca el servidor con el *Client ID* de la app (no hace falta el secreto: se usa OAuth con PKCE):

   ```sh
   KARAOKE_SPOTIFY_CLIENT_ID=tu_client_id uv run python -m server
   ```

   (En PowerShell: `$env:KARAOKE_SPOTIFY_CLIENT_ID="tu_client_id"; uv run python -m server`.)

5. Abre la pantalla principal en `127.0.0.1` (la misma dirección que la *Redirect URI*), despliega **Spotify** en
   la biblioteca y pulsa **Conectar con Spotify**.

Limitaciones (Spotify no da acceso al audio): letra solo por líneas (LRCLIB), sin control de voz, sin melodía de
referencia (puntuación simplificada) y sin transcripción si LRCLIB no tiene la letra. Los archivos de audio son la
vía principal; Spotify es un extra.

## Arrancar

### Modo fiesta (un solo programa)

```sh
npm run build              # compila el frontend en web/dist
uv run python -m server    # sirve todo en el puerto 8000
```

Abre `http://127.0.0.1:8000/` en el portátil.

### Desarrollo

```sh
uv run python -m server --reload   # API en el puerto 8000
npm run dev                        # Vite con recarga en vivo (proxy de /api y /ws al servidor)
```

Abre la URL que muestra Vite (`/host/`).

## Uso

1. Arrastra archivos de audio a la biblioteca. Se leen sus metadatos (título, artista, álbum, duración) y se busca la
   letra sincronizada en [LRCLIB](https://lrclib.net).
2. Si no se encuentra o no es la correcta, pulsa **Buscar letra** y elige otra.
3. Pulsa **Cantar**. La línea actual se rellena palabra a palabra y debajo se ve la siguiente.
4. Si la letra va adelantada o atrasada, corrígela con **Desfase de la letra** (±2 s, se guarda por canción).

Atajos: barra espaciadora = reproducir / pausar; **F** = modo TV.

### Modo TV

**📺 Modo TV** (o la tecla F) pone la pantalla completa con la letra en grande. Los controles se esconden si no
mueves el ratón y, mientras la canción está en pausa o entre canciones, aparece el QR con el código de la sala para
que se una más gente. Esc lo quita.

### Ranking e historial

Cada partida de competición se guarda (`data/history.sqlite3`). En **🏆 Ranking** están las victorias, la mejor
puntuación y la media de cada jugador (se reconocen por el nombre), el récord de cada canción y el historial de
partidas. Al terminar una partida se avisa si alguien ha batido el récord de la canción.

### Ajustes

En **⚙ Ajustes**: el modelo de Whisper para las letras (de `tiny` a `large-v3`; se guarda en `data/settings.json`),
**Reprocesar todas** las canciones (por ejemplo tras cambiar de modelo) y borrar el historial.

### Buscar y descargar canciones

En **Buscar canciones en internet** (biblioteca) escribe artista y canción: busca en YouTube y **Descargar** baja
solo el audio a la biblioteca, con su progreso, y sigue el mismo camino que un archivo subido (letra de LRCLIB y
procesado). Usa [yt-dlp](https://github.com/yt-dlp/yt-dlp), que se instala con las dependencias (`uv sync`). Si
YouTube cambia algo y deja de funcionar, actualízalo con `uv lock --upgrade-package yt-dlp && uv sync`.
Úsalo solo para uso privado y con música que tengas derecho a usar.

### Corregir la letra

**Editar letra** (en la biblioteca o con **✎ Letra** mientras se canta) abre el editor con la canción sonando:

- Cambia el texto de cada línea. Si la línea mantiene el número de palabras, conserva sus tiempos exactos.
- Ajusta el inicio de cada línea con **−/+** (décimas) o **⏱** (momento actual); añade o borra líneas.
- **Modo sincronizar**: reproduce y pulsa **Enter** justo cuando empieza cada línea.
- **Pegar letra completa**: pega la letra buena y, si la canción se puede procesar, se sincroniza sola con la voz
  (Whisper la alinea en unos minutos). Si no, quedará sin tiempos: sincronízala con Enter y guarda.

### Móviles como micrófono

Portátil y móviles tienen que estar en la misma Wi-Fi.

1. En la pantalla principal aparece la **sala** con un código QR. Cada jugador lo escanea, escribe su nombre, elige
   un color y pulsa **Activar micrófono**. El móvil solo envía datos (tono y volumen), nunca audio.
2. Antes de empezar, en **Calibración** (cada móvil la recuerda: al volver a conectarse se aplica sola):
   - **Calibrar ruido**: con la sala en silencio, 3 s para medir el ruido de fondo.
   - **Medir retraso del micrófono**: aparece un círculo que late; canta «ta» con cada latido siguiendo el ritmo.
     Mide el retraso propio de ese móvil (100 ms por defecto) para que la puntuación no te penalice por él.
     También se puede ajustar a mano.
3. Mientras suena una canción, la pantalla muestra un carril por jugador con su tono en vivo sobre la melodía de
   referencia (si la canción está procesada) y el móvil muestra la línea que toca cantar.

Consejos: cantad con el móvil cerca de la boca y con la música a un volumen moderado (cada móvil oye también los
altavoces y a los demás).

### Modos de juego

- **Cantar** (karaoke libre): sin puntuación.
- **Competir**: todos cantan a la vez y cada uno se puntúa por separado, hasta 10 000 puntos por canción.
  - Canción **procesada**: afinación (70 %, sin tener en cuenta la octava, así que voces graves y agudas puntúan
    igual) + presencia y ritmo (30 %). Cantar donde no hay letra no suma, pero tampoco resta.
  - Canción **sin procesar** (o de Spotify): *puntuación simplificada*, solo presencia y ritmo según la letra.
  - Marcador en vivo, racha 🔥, y al terminar la canción (o con **Terminar y ver resultados**) ranking con premios
    («Más afinado», «Más constante», «Pulmones de acero»...). Cada móvil ve su puesto y su puntuación.

### Procesado

Con el procesado instalado, cada canción importada entra en la cola y se procesa en segundo plano, de una en una
(también se puede lanzar con **Procesar** / **Procesar todas**). Se puede cantar en karaoke libre mientras tanto.
Al terminar:

- **Volumen de la voz** (100 % = canción original) y **Solo instrumental**.
- Relleno palabra a palabra con los tiempos reales (Whisper alineado con la letra de LRCLIB).
- Si LRCLIB no tenía letra, se transcribe del audio y se marca como «letra transcrita automáticamente».
- Melodía de referencia para puntuar la afinación en el modo competición.
- **Comprobación de la letra**: se compara con lo que realmente se canta. Si va adelantada o retrasada (otra
  versión, una intro más larga...) se corrige sola; si los tiempos no encajan se recalculan a partir de la voz; y si
  la letra no parece de esta canción, la biblioteca lo avisa para que la cambies o la edites. Para revisar canciones
  procesadas antes de esto, pulsa **Reprocesar**.

Todo se guarda en `data/songs/<id>/` y no se vuelve a calcular. En CPU cada canción tarda varios minutos: conviene
dejarlas procesando antes de la quedada.

## Configuración

Variables de entorno opcionales:

| Variable | Por defecto | Para qué |
|---|---|---|
| `KARAOKE_PORT` | `8000` | Puerto del servidor |
| `KARAOKE_HOST` | `0.0.0.0` | Interfaz en la que escucha |
| `KARAOKE_DATA_DIR` | `data/` | Biblioteca, canciones y caché |
| `KARAOKE_AUTO_PROCESS` | `1` | Encolar automáticamente cada canción importada |
| `KARAOKE_WHISPER_MODEL` | automático | Modelo de Whisper por defecto (en la app se cambia en **Ajustes**) |
| `KARAOKE_PUBLIC_PORT` | `KARAOKE_PORT` | Puerto que aparece en el QR (p. ej. `5173` con `npm run dev`) |
| `KARAOKE_MIC_LATENCY_MS` | `100` | Retraso del micrófono por defecto |
| `KARAOKE_SPOTIFY_CLIENT_ID` | — | Client ID de la app de Spotify |
| `KARAOKE_SPOTIFY_REDIRECT_URI` | `http(s)://127.0.0.1:<puerto>/api/spotify/callback` | Redirect URI registrada en Spotify |

## Tests

```sh
uv run pytest       # Python
npm test            # TypeScript (vitest)
npm run typecheck
```

Los tipos de TypeScript de `web/shared/types.ts` se generan desde los modelos Pydantic: `npm run gen:types`.
