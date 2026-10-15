# Karaoke entre amigos

Karaoke para jugar en casa: la pantalla principal se abre en el navegador del portátil (conectable a la tele) y
los móviles de los jugadores hacen de micrófono. El diseño completo está en [DISENO.md](DISENO.md).

## Requisitos

- Python 3.11+ y [uv](https://docs.astral.sh/uv/)
- Node.js LTS y npm
- `ffmpeg` (opcional pero recomendado: normaliza el volumen y permite formatos como `.wma`)

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

## Tests

```sh
uv run pytest       # Python
npm test            # TypeScript (vitest)
npm run typecheck
```

Los tipos de TypeScript de `web/shared/types.ts` se generan desde los modelos Pydantic: `npm run gen:types`.
