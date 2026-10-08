# Animated Caption Studio

Aplicación de escritorio para macOS que transcribe videos localmente con
`faster-whisper`, conserva marcas de tiempo por palabra y exporta ASS, SRT,
JSON y un MP4 renderizado con FFmpeg/libass.
El proyecto restringe PyAV a versiones compatibles con el cargador de audio de
faster-whisper y usa `int8` por defecto para inferencia en CPU.

## Requisitos

- macOS 12 o posterior recomendado.
- Python 3.11+ (verificado durante el desarrollo con Python 3.12).
- FFmpeg con el filtro `ass`/libass disponible; verifica con
  `ffmpeg -filters | grep -w ass`. Instala una compilación de FFmpeg con libass
  con `brew install ffmpeg-full` si falta. La aplicación prioriza esa variante
  completa de Homebrew sin quitar el FFmpeg estándar.
- Espacio en disco para los modelos Whisper. Se descargan cuando faster-whisper
  carga por primera vez el modelo seleccionado; el usuario lo inicia al generar.

## Instalación y ejecución

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/check_environment.py
python -m app.main
```

Actualmente no se requieren variables de entorno ni un archivo `.env`.
`.env.example` se mantiene como plantilla vacía para futuras opciones de
configuración; no contiene credenciales.

El render crea `video_captioned.mp4`, `captions.ass`, `captions.srt`,
`transcript.json` (con marcas de tiempo por palabra) y `project.acs` (opciones
del proyecto) en `~/Movies/Animated Caption Studio/<nombre-del-video>/`.
El SRT es para edición en herramientas como CapCut; la apariencia avanzada ASS
se conserva al renderizar con FFmpeg y no se garantiza que CapCut importe todas
las animaciones ASS.
Después de generar, selecciona «Abrir editor» para reproducir el video fuente
con captions sincronizados. El editor permite seleccionar y arrastrar captions
en el preview, cambiar texto, dividir/unir y reordenar palabras, ajustar posición,
escala, espaciado y timing, y modificar el preset y la tipografía. Incluye
timeline con selección, desplazamiento y ajuste de extremos, undo/redo y guardado
automático del proyecto `.acs`. Usa «Exportar ASS/SRT» para actualizar los
subtítulos o «Renderizar MP4» para producir el video con la configuración editada.
Un proyecto `.acs` guardado se puede volver a abrir con «Abrir proyecto…» sin
repetir la transcripción.

## Pruebas y empaquetado

```bash
pytest
python scripts/build_macos.py
```

El diagnóstico comprueba el intérprete Python activo, faster-whisper, FFmpeg
con el filtro ASS/libass y ffprobe. La interfaz también muestra esos estados y
no habilita la generación si falta alguno.

El empaquetado produce `dist/Animated Caption Studio.app`. FFmpeg no se
redistribuye dentro de la app: debe estar instalado en el Mac de destino;
asegúrate de instalar `ffmpeg-full` para disponer del filtro ASS/libass.
faster-whisper obtiene los modelos localmente al utilizarlos; su caché permanece
en el directorio estándar de Hugging Face.

## Arquitectura

`app/core` contiene modelos puros; `transcription` adapta motores locales;
`captions`, `ass` y `srt` implementan el dominio/exportación; `rendering` encapsula
FFmpeg; `services/pipeline.py` orquesta el flujo; `ui` contiene la interfaz Qt y
ejecuta tareas largas en un hilo separado. Los estilos se resuelven mediante un
registro sin acoplar el motor ASS a la GUI.
