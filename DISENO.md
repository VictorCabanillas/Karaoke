# Karaoke entre amigos — Documento de diseño

> Documento de referencia del proyecto. Recoge todas las decisiones tomadas antes de empezar a programar.
> Pensado para ponerlo en la raíz del repositorio y dárselo como contexto al asistente de código.

---

## 0. Reglas de trabajo (para el asistente de código)

- **No aplicar ningún cambio de código sin aprobación explícita del usuario.** Antes de crear o modificar archivos: explicar qué se va a hacer, mostrar el código o el diff, y esperar un "ok".
- Trabajar por fases (sección 10). No adelantar trabajo de fases futuras sin pedirlo.
- Cambios pequeños y revisables: un bloque lógico cada vez.
- Idioma de la interfaz: español. Código, nombres de variables y commits: inglés.
- No incluir letras de canciones en el repositorio (ni en tests ni en fixtures). Para pruebas, usar textos inventados o canciones de dominio público.

---

## 1. Objetivo

Una aplicación de karaoke para jugar con amigos en casa:

- Se le da cualquier canción (archivo de audio propio o una canción de Spotify).
- Busca la letra sincronizada en internet; si no existe, la obtiene transcribiendo el audio.
- Reproduce la canción y muestra qué parte toca cantar, ajustado al ritmo real.
- Los móviles de los jugadores hacen de micrófono.
- Dos modos de juego:
  - **Karaoke libre:** cantar tranquilo, sin puntuación.
  - **Competición simultánea:** varias personas cantan **a la vez**, cada una con su móvil, y se puntúa a todas en tiempo real.

---

## 2. Decisiones tomadas

| Tema | Decisión |
|---|---|
| Formato | App web servida desde el portátil. La pantalla principal se abre en el navegador del portátil (conectable a la tele). Empaquetado como app de escritorio (Tauri/Electron): posible más adelante, no ahora. |
| Micrófono | El móvil de cada jugador, vía navegador, conectándose con un código QR. Sin instalar nada. |
| Fuentes de música | **Archivos de audio** (prioritario, funcionalidad completa) y **Spotify** (el usuario tiene Premium; funcionalidad reducida). |
| Voz del artista | **Por defecto suena la canción original con voz.** Deslizador de volumen de voz (100% por defecto; 0% = solo instrumental) y casilla rápida "Solo instrumental". |
| Lenguajes | **Python** para servidor y procesado. **TypeScript** (sin frameworks) para lo que corre en el navegador. |
| GPU | No garantizada. El procesado detecta automáticamente CUDA → Apple MPS → CPU. |

---

## 3. Stack técnico

### Python (gestionado con `uv`)
- **FastAPI** + **uvicorn**: servidor HTTP, WebSockets, sirve el frontend compilado.
- **Pydantic**: modelos de datos y de mensajes WebSocket (fuente única de verdad de los tipos).
- **SQLite** (vía `sqlite3` o SQLModel): biblioteca y caché de canciones procesadas.
- **httpx**: llamadas a LRCLIB y a la API de Spotify.
- **mutagen**: leer metadatos (título, artista, álbum, duración) de los archivos de audio.
- **Demucs** (modelo `htdemucs`): separación voz / instrumental.
- **torchcrepe** (o `librosa.pyin` como alternativa ligera en CPU): extracción de la melodía de referencia desde la pista de voz.
- **faster-whisper** (o WhisperX): transcripción de respaldo y alineación por palabra.
- **segno** o **qrcode**: generar el QR de conexión.

### TypeScript (gestionado con `npm` + **Vite**)
- Sin React ni frameworks. TypeScript directo + Canvas/DOM.
- **Web Audio API**: reproducción de pistas sincronizadas y mezcla de voz.
- **AudioWorklet**: detección de tono en el móvil.
- **Spotify Web Playback SDK**: reproducción de Spotify en la pantalla principal (fase 5).
- Tipos de mensajes generados automáticamente desde los modelos Pydantic (p. ej. con `pydantic2ts` o exportando JSON Schema → `json-schema-to-typescript`).

### Utilidades
- **mkcert**: certificados HTTPS locales (obligatorio: los navegadores móviles solo permiten usar el micrófono en HTTPS).
- **ffmpeg**: conversión/normalización de audio (dependencia de sistema).

---

## 4. Estructura del repositorio

```
karaoke/
├── DISENO.md               # este documento
├── README.md               # cómo instalar y arrancar
├── pyproject.toml          # Python (uv)
├── package.json            # TypeScript (Vite)
├── vite.config.ts
├── tsconfig.json
├── server/                 # FastAPI
│   ├── main.py             # arranque, rutas, estáticos
│   ├── config.py
│   ├── models.py           # Pydantic: canciones, mensajes WS
│   ├── library.py          # biblioteca / SQLite
│   ├── session.py          # sala de juego, jugadores conectados
│   ├── ws_host.py          # WebSocket de la pantalla principal
│   ├── ws_mic.py           # WebSocket de los móviles
│   ├── scoring.py          # puntuación
│   └── spotify.py          # OAuth + búsqueda (fase 5)
├── processing/             # pipeline de procesado de canciones
│   ├── device.py           # detección CUDA / MPS / CPU
│   ├── metadata.py         # mutagen
│   ├── lrclib.py           # cliente LRCLIB
│   ├── lrc.py              # parser de formato LRC
│   ├── separate.py         # Demucs
│   ├── melody.py           # melodía de referencia
│   ├── transcribe.py       # Whisper: respaldo y alineación
│   └── queue.py            # cola de procesado en segundo plano
├── web/
│   ├── shared/             # tipos generados desde Python + utilidades comunes
│   ├── host/               # pantalla principal (index.html + ts)
│   └── mic/                # página del móvil (index.html + ts)
├── scripts/                # generar tipos, certificados, etc.
├── tests/
├── data/                   # (gitignored) canciones, pistas separadas, caché
└── certs/                  # (gitignored) certificados mkcert
```

En desarrollo: Vite sirve el frontend con recarga en vivo y hace proxy a FastAPI. En "modo fiesta": `vite build` y FastAPI sirve los estáticos; se arranca un solo programa.

---

## 5. Letras

### 5.1 Fuente principal: LRCLIB
Base de datos abierta y gratuita de letras, con versiones simples y sincronizadas (formato LRC, marca de tiempo por línea).

- `GET https://lrclib.net/api/get?track_name=…&artist_name=…&album_name=…&duration=…` → coincidencia exacta (la duración en segundos ayuda mucho).
- `GET https://lrclib.net/api/search?q=…` (o `track_name`, `artist_name`) → búsqueda flexible, devuelve varias candidatas.
- Campos relevantes en la respuesta: `syncedLyrics`, `plainLyrics`, `instrumental`, `duration`.
- Enviar una cabecera `User-Agent` identificando la app (p. ej. `karaoke-amigos/0.1`).
- Si hay varias candidatas: elegir la de duración más cercana (tolerancia ±2 s) y que tenga `syncedLyrics`. Si hay dudas, dejar que el usuario elija en la interfaz.

### 5.2 Cascada de obtención de letra
1. LRCLIB con `syncedLyrics` → **sincronizada por línea**.
2. LRCLIB solo con `plainLyrics` → se alinea con el audio usando Whisper (solo archivos) → **sincronizada**.
3. Nada en LRCLIB → transcripción con Whisper sobre la pista de voz separada (solo archivos) → **letra + tiempos**. Marcar en la interfaz como "letra transcrita automáticamente (puede contener errores)".
4. Spotify sin resultado en LRCLIB → no hay letra; avisar y permitir buscar manualmente con otro título/artista.

La app **nunca inventa** letra: o la encuentra o la transcribe del audio real.

### 5.3 Precisión por palabra
- **Archivos procesados:** alineación forzada con Whisper (marcas de tiempo por palabra) sobre la pista de voz separada → relleno de color palabra a palabra exacto.
- **Spotify / archivos aún sin procesar:** solo hay tiempos por línea. El relleno por palabra se **interpola**: se reparte la duración de la línea entre las palabras proporcionalmente a su número de sílabas (aproximación por vocales). Suficiente para guiar.

### 5.4 Modelo de datos de la letra
```
Lyrics
  source: "lrclib_synced" | "lrclib_aligned" | "whisper" | "lrclib_lines_only"
  lines: [ Line ]
Line
  start_ms, end_ms
  words: [ Word ]
Word
  text, start_ms, end_ms
  estimated: bool        # true si viene de interpolación
```

### 5.5 Ajuste manual de desfase
Deslizador en la pantalla principal (±2 s, pasos de 50 ms) para corregir letras ligeramente adelantadas/atrasadas. Se guarda por canción.

---

## 6. Procesado de canciones (solo archivos)

### 6.1 Pipeline
Al importar un archivo:
1. Leer metadatos (mutagen) y normalizar audio con ffmpeg.
2. Buscar letra en LRCLIB → la canción **ya se puede cantar** en modo karaoke libre.
3. En segundo plano (cola):
   1. Demucs → `vocals.wav` + `instrumental.wav` (instrumental = suma de las demás pistas).
   2. Melodía de referencia desde `vocals.wav` → curva de tono (Hz o MIDI) cada ~10–20 ms + máscara de "aquí se canta".
   3. Whisper → alineación por palabra (si hay letra) o transcripción completa (si no la hay).
4. Guardar todo en `data/songs/<id>/` y marcar la canción como "procesada".

### 6.2 Estados de una canción
`importada` → `letra_lista` → `procesando` → `procesada` (o `error` con mensaje).

Mientras no esté `procesada`:
- Disponible: karaoke libre con letra por línea.
- No disponible: modo competición con afinación, deslizador de voz.

### 6.3 Rendimiento sin GPU
- Detección automática de dispositivo (`processing/device.py`).
- En CPU, cada canción tarda varios minutos la primera vez; el resultado se cachea para siempre.
- **Cola de preparación:** el usuario puede seleccionar varias canciones antes de la quedada para que se procesen en orden.
- Mostrar progreso en la interfaz.
- Usar modelos de Whisper pequeños en CPU (`small` o `base`, int8) y más grandes con GPU.

---

## 7. Reproducción

### 7.1 Archivos
- Si la canción está procesada: se cargan `vocals` e `instrumental` como dos `AudioBufferSourceNode` arrancados en el **mismo instante** del `AudioContext`, cada uno con su `GainNode`.
  - Deslizador "Volumen de la voz": 0–100%, por defecto **100%** (canción original).
  - Casilla "Solo instrumental": atajo que pone la voz a 0%.
- Si no está procesada: se reproduce el archivo original; los controles de voz aparecen desactivados con el texto "disponible cuando termine el procesado".
- El reloj maestro es `audioContext.currentTime` (+ desfase manual + latencia de salida `outputLatency`).

### 7.2 Spotify (fase 5)
- **Web Playback SDK** en la pantalla principal: el portátil se convierte en un dispositivo Spotify.
- Autenticación OAuth con **PKCE** (sin secreto en el cliente).
- Posición: el SDK da la posición en eventos de estado; entre eventos se **extrapola** con `performance.now()` y se corrige en cada actualización (consultar estado cada ~500 ms).
- Requisitos y limitaciones (cambios de Spotify en 2026):
  - El modo desarrollo exige que el dueño de la app tenga **Spotify Premium** (el usuario lo tiene).
  - Máximo **5 usuarios** autorizados por app en modo desarrollo; se añaden a mano en el Developer Dashboard. Solo hace falta la cuenta del anfitrión: los amigos no usan Spotify.
  - Búsqueda (`GET /search`) limitada a 10 resultados por página.
  - No hay acceso al audio (protegido): **sin separación de voz, sin melodía de referencia, sin transcripción**.
  - Sus condiciones para desarrolladores son restrictivas con sincronizar su música con contenido visual. Uso privado entre amigos; la app no debe depender de Spotify para funcionar.

### 7.3 Comparativa
| | Archivos | Spotify |
|---|---|---|
| Letra sincronizada | LRCLIB + alineación por palabra | Solo LRCLIB, por línea |
| Sin letra en LRCLIB | Transcripción con Whisper | No hay alternativa |
| Volumen de voz / instrumental | Sí | No |
| Puntuación por afinación | Sí | No (solo ritmo/volumen) |

---

## 8. Móviles como micrófono

### 8.1 Conexión
1. La pantalla principal muestra un QR con `https://<ip-local-del-portátil>:<puerto>/mic?room=<código>`.
2. El jugador lo escanea, escribe su nombre y elige un color.
3. Pulsa "Activar micrófono" (los navegadores exigen un gesto del usuario).
4. Acepta el certificado local si el navegador lo pide (documentar en el README cómo instalar la CA de mkcert en el móvil para evitar el aviso).

Requisitos: portátil y móviles en la misma red Wi-Fi.

### 8.2 Captura y análisis en el móvil
- `getUserMedia` con `echoCancellation`, `noiseSuppression` y `autoGainControl` **desactivados** para la detección de tono (alteran la señal), salvo que las pruebas digan lo contrario.
- **AudioWorklet** con algoritmo **YIN** (o MPM) → frecuencia fundamental + confianza.
- Volumen RMS + **puerta de ruido** calibrable (ver 8.4).
- Envía **solo datos, no audio**, unas 30–50 veces por segundo:
  ```
  PitchFrame { t_client_ms, f0_hz | null, confidence, rms }
  ```
  (agrupar varios frames por mensaje para reducir sobrecarga).
- La pantalla del móvil muestra: nombre, medidor de volumen, nota actual, puntuación propia y la línea de letra actual (opcional).
- Mantener la pantalla encendida con la **Wake Lock API**.

### 8.3 Sincronización de relojes
Cada móvil tiene su propio reloj. Al conectar (y cada ~10 s):
- Intercambio tipo NTP por WebSocket: el móvil envía `t0`, el servidor responde con `t_server`, el móvil recibe en `t1`.
- `offset = t_server - (t0 + t1) / 2`; se toma la mediana de varias muestras con menor RTT.
- El servidor convierte `t_client_ms` a tiempo de canción: `t_cancion = (t_client + offset) - t_inicio_reproduccion - latencia_entrada_mic`.

### 8.4 Calibración (antes de cada partida)
- **Ruido ambiente:** 3 s en silencio → umbral de la puerta de ruido.
- **Latencia:** opcional, prueba de "canta cuando veas la señal" para estimar el retraso del micro; por defecto un valor fijo configurable (~100 ms).

### 8.5 Limitación conocida
Cantando todos en la misma sala, cada móvil capta también la música de los altavoces y a los demás jugadores. Mitigaciones: puerta de ruido, umbral de confianza del tono, pedir cantar con el móvil cerca de la boca, volumen de música moderado. No se elimina del todo; asumirlo en el diseño de la puntuación (ser tolerante).

---

## 9. Puntuación (modo competición)

Todos los jugadores cantan a la vez; cada uno se puntúa por separado contra la misma referencia.

### 9.1 Con melodía de referencia (archivos procesados)
Por cada ventana de ~100 ms en la que la referencia indica que se canta:
- **Afinación:** diferencia en semitonos entre el jugador y la referencia, **ignorando la octava** (para que hombres y mujeres puntúen igual cantando una octava más grave/aguda). Tolerancia: ≤1 semitono = acierto total, decae hasta 0 a ~3 semitonos.
- **Presencia/ritmo:** ¿hay voz (RMS > umbral y confianza > umbral) cuando toca? ¿y silencio cuando no toca? (penalización leve por cantar en silencios, para no premiar gritar todo el rato).
- Puntuación final de la ventana = combinación ponderada (p. ej. 70% afinación, 30% presencia). Acumulado sobre 10 000 puntos por canción.
- Bonus opcional: rachas ("combo") por varias frases seguidas bien cantadas.

### 9.2 Sin melodía (Spotify o canción sin procesar)
Solo **presencia/ritmo**: cantar durante las líneas, callar entre ellas, con algo de estabilidad de tono como factor menor. Indicar en la interfaz que es una "puntuación simplificada".

### 9.3 Interfaz de competición
- Barra/carril de tono por jugador (con su color) bajo la letra.
- Marcador en vivo.
- Pantalla final con ranking y algún premio divertido ("más afinado", "más constante", etc.).

### 9.4 Dónde se calcula
En el servidor (`server/scoring.py`), al recibir los frames de cada móvil. Envía a la pantalla principal actualizaciones agregadas ~10 veces por segundo.

---

## 10. Fases y criterios de aceptación

### Fase 1 — Base
- Esqueleto del proyecto: `pyproject.toml`, `package.json`, Vite, `.gitignore`, README.
- FastAPI sirviendo la pantalla principal.
- Subir un archivo de audio → leer metadatos → buscar en LRCLIB → guardar en la biblioteca.
- Pantalla de karaoke: reproducción del archivo, línea actual resaltada, siguiente línea en vista previa, relleno por palabra interpolado.
- Deslizador de desfase.
- **Hecho cuando:** se puede subir un MP3 con letra en LRCLIB y cantar siguiendo la pantalla con buena sincronía.

### Fase 2 — Procesado
- Detección de dispositivo, cola en segundo plano con progreso.
- Demucs → voz + instrumental; deslizador de voz (100% por defecto) y casilla "Solo instrumental".
- Melodía de referencia guardada.
- Whisper: alineación por palabra y transcripción de respaldo.
- **Hecho cuando:** una canción procesada muestra relleno por palabra exacto y permite bajar la voz; una canción sin letra en LRCLIB obtiene letra transcrita.

### Fase 3 — Móviles
- HTTPS con mkcert, QR, sala con código, unión de jugadores.
- Captura de micro + YIN en AudioWorklet, envío de frames, sincronización de relojes, calibración.
- La pantalla principal muestra el tono en vivo de cada jugador.
- **Hecho cuando:** 3+ móviles conectados a la vez muestran su tono en la pantalla sin retrasos apreciables.

### Fase 4 — Competición
- Puntuación simultánea (9.1 y 9.2), marcador en vivo, pantalla final.
- Selector de modo: karaoke libre / competición.
- **Hecho cuando:** varios jugadores cantan a la vez y obtienen puntuaciones coherentes (cantar bien > cantar mal > no cantar).

### Fase 5 — Spotify
- OAuth PKCE, búsqueda de canciones, Web Playback SDK, sincronía por extrapolación.
- Letra de LRCLIB por línea; puntuación simplificada; controles de voz desactivados.
- **Hecho cuando:** se puede elegir una canción de Spotify y cantarla con la letra sincronizada.

### Ideas para más adelante
- Empaquetar como app de escritorio.
- Lista de reproducción / cola de turnos.
- Duetos (asignar líneas a jugadores).
- Historial de puntuaciones.

---

## 11. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Letra de LRCLIB desfasada respecto a la versión del archivo | Filtrar por duración; deslizador de desfase guardado por canción. |
| Navegador móvil bloquea el micro | HTTPS obligatorio con mkcert; instrucciones para instalar la CA en el móvil. |
| Latencia/variación de la Wi-Fi | Los móviles mandan marcas de tiempo propias + sincronización de relojes; el servidor no depende del momento de llegada. |
| Interferencias entre jugadores en la misma sala | Puerta de ruido, umbral de confianza, puntuación tolerante. |
| Procesado lento sin GPU | Jugar inmediatamente en karaoke libre; cola de preparación previa; caché. |
| Cambios en la API de Spotify | Spotify es opcional y aislado en su módulo; los archivos son la vía principal. |
| Errores de transcripción de Whisper | Marcar la letra como automática; permitir editarla a mano (mejora futura). |

---

## 12. Dependencias de sistema a instalar
- Python 3.11+ y `uv`
- Node.js LTS y `npm`
- `ffmpeg`
- `mkcert`
- (Opcional) drivers CUDA si el portátil tiene GPU NVIDIA
