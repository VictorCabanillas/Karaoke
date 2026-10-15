# Karaoke entre amigos

Karaoke para jugar en casa: la pantalla principal se abre en el navegador del portátil (conectable a la tele) y
los móviles de los jugadores hacen de micrófono. El diseño completo está en [DISENO.md](DISENO.md).

## Requisitos

- Python 3.11+ y [uv](https://docs.astral.sh/uv/)
- Node.js LTS y npm
- `ffmpeg` (opcional pero recomendado: normaliza el volumen y permite formatos como `.wma`)
- [`mkcert`](https://github.com/FiloSottile/mkcert) para usar los móviles como micrófono (necesitan HTTPS)

## Instalación

```sh
uv sync          # dependencias de Python
npm install      # dependencias del frontend
```

### Procesado de canciones (opcional, recomendado)

Separar la voz (Demucs), sacar la melodía de referencia (CREPE) y alinear/transcribir la letra (Whisper) necesita
dependencias pesadas (PyTorch, varios GB):

```sh
uv sync --extra processing
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

Atajo: barra espaciadora = reproducir / pausar.

### Móviles como micrófono

Portátil y móviles tienen que estar en la misma Wi-Fi.

1. En la pantalla principal aparece la **sala** con un código QR. Cada jugador lo escanea, escribe su nombre, elige
   un color y pulsa **Activar micrófono**. El móvil solo envía datos (tono y volumen), nunca audio.
2. Antes de empezar, en **Calibración**: pedid silencio y pulsad **Calibrar ruido** (3 s). Si notáis que la voz va
   retrasada respecto a la música, ajustad el **retraso del micrófono** (100 ms por defecto).
3. Mientras suena una canción, la pantalla muestra un carril por jugador con su tono en vivo sobre la melodía de
   referencia (si la canción está procesada) y el móvil muestra la línea que toca cantar.

Consejos: cantad con el móvil cerca de la boca y con la música a un volumen moderado (cada móvil oye también los
altavoces y a los demás).

### Modos de juego

- **Cantar** (karaoke libre): sin puntuación.
- **Competir**: todos cantan a la vez y cada uno se puntúa por separado, hasta 10 000 puntos por canción.
  - Canción **procesada**: afinación (70 %, sin tener en cuenta la octava, así que voces graves y agudas puntúan
    igual) + presencia y ritmo (30 %). Cantar en los silencios resta un poco.
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
| `KARAOKE_WHISPER_MODEL` | automático | Modelo de Whisper (`tiny`, `base`, `small`, `medium`, `large-v3`) |
| `KARAOKE_PUBLIC_PORT` | `KARAOKE_PORT` | Puerto que aparece en el QR (p. ej. `5173` con `npm run dev`) |
| `KARAOKE_MIC_LATENCY_MS` | `100` | Retraso del micrófono por defecto |

## Tests

```sh
uv run pytest       # Python
npm test            # TypeScript (vitest)
npm run typecheck
```

Los tipos de TypeScript de `web/shared/types.ts` se generan desde los modelos Pydantic: `npm run gen:types`.
