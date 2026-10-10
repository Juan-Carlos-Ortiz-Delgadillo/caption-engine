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
- Modelos de faster-whisper descargados previamente en la caché local. La
  aplicación fuerza el modo sin red; prepara cada modelo en este equipo antes de
  desconectarte.

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

El asistente guía el trabajo en cuatro pasos: video, transcripción, estilo y
generación del proyecto. La transcripción se guarda como `transcript.json` con
marcas de tiempo por palabra; si ya existe para ese video, se puede reutilizar.
La selección del estilo muestra palabras reales de la transcripción. La
generación segmenta y ajusta el layout, pero no exporta subtítulos ni renderiza.
Al terminar se abre Caption Editor y se guarda `project.acs` junto al transcript
en `~/Movies/Animated Caption Studio/<nombre-del-video>/`. Los proyectos
recientes se pueden abrir desde la ventana inicial.

La pestaña «Exportar» del editor crea `captions.ass` y `captions.srt`;
«Renderizar MP4» abre un diálogo para nombre, carpeta y calidad y produce el
MP4 en el destino elegido. El video se escribe
primero a un archivo temporal en la carpeta de destino y solo reemplaza el MP4
anterior cuando el render termina correctamente.
El SRT es para edición en herramientas como CapCut; la apariencia avanzada ASS
se conserva al renderizar con FFmpeg y no se garantiza que CapCut importe todas
las animaciones ASS.
El asistente abre el editor para reproducir el video fuente con captions
sincronizados. La imagen del preview se regenera en un hilo de
trabajo con el mismo archivo ASS/libass que se usa para el MP4; los errores de
FFmpeg se notifican y el preview recurre a la vista de reproducción nativa.
El preview permite seleccionar captions con el mouse, moverlos (Shift limita el
movimiento a un eje), escalar desde la esquina, ajustar ancho desde las asas
superior/inferior y rotar desde el asa inferior derecha. El botón central del
mouse desplaza el encuadre; «Ajustar» lo restablece. Tab cicla captions
superpuestos y el doble clic abre edición de texto (Enter confirma, Esc cancela).
El centrado se ajusta a guías visibles y se muestra una zona segura. X/Y en el
inspector son píxeles de la resolución del video, no píxeles de pantalla. La
posición, escala y ancho modificados son sobrescrituras por caption; «Restablecer
posición y escala» vuelve a los valores del estilo global.
Con un caption seleccionado, las flechas mueven 1 píxel de video (Shift: 10);
sin selección, Izquierda/Derecha siguen buscando un frame (±0,04 s). Así se
resuelve el conflicto entre navegación y edición. La edición en el canvas se
guarda como una sola acción de deshacer por arrastre.
El editor también permite dividir/unir y reordenar palabras, ajustar posición,
espaciado y timing, y cambiar fuente, tamaño, negrita,
cursiva, subrayado, caso, color, burbuja, contorno, sombra, blur y animación.
Los saltos de línea y el control de interlineado posicionan líneas separadas
en las coordenadas PlayRes del ASS. libass no ofrece esquinas redondeadas para
BorderStyle=3, por lo que las burbujas son cuadradas. También incluye controles
de encuadre/zoom del preview, timeline, undo/redo y guardado automático `.acs`.
La segmentación temporal limita la duración y el tamaño de cada caption. Antes de
exportar, el ajuste visual mide cada línea con las métricas Qt de la familia y el
peso tipográfico seleccionados, incorpora contorno, sombra, blur y burbuja, y
equilibra palabras completas con programación dinámica. El ancho seguro lateral
se escala con la resolución del video; si no cabe, intenta tamaños de fuente
desde el configurado hasta 56 px. Si aún no cabe, permite hasta seis líneas y
parte con guion las palabras que exceden el ancho, con una advertencia en el
registro. Si no existe una distribución válida, informa la palabra que supera
el ancho y las dimensiones disponibles en lugar de exportar texto recortado.
Whisper normaliza Unicode y, cuando detecta español, descarta caracteres fuera
del alfabeto español con una advertencia. Los saltos de línea se comparten entre
ASS y SRT. Los saltos y posiciones editados manualmente en el editor siguen
siendo manuales.
«Verificar frame exacto» muestra un fotograma generado por FFmpeg/libass con el
mismo ASS de exportación para compararlo visualmente con el preview. La pestaña
Tracking conserva su estado informativo actual; esta edición no añade ni altera
seguimiento de objetos.
Los presets de estilo se pueden guardar como JSON. «Recalcular layout» en el
editor actualiza saltos y tamaños sin cambiar texto, tiempos, divisiones,
uniones ni posiciones; la acción se puede deshacer. Los saltos de línea
introducidos manualmente se conservan. Si un caption no cabe, se mantiene y se
marca con una advertencia en el inspector y la timeline. «Reconstruir desde la
transcripción» vuelve a segmentar desde `transcript.json`, requiere confirmación
y también se puede deshacer. Un proyecto `.acs` guardado se puede volver a abrir
sin repetir la transcripción; los formatos anteriores se migran al guardarse.

La app no accede a la red: faster-whisper se configura con `local_files_only`.
Prepara en la caché local el modelo seleccionado y las fuentes del sistema antes
de trabajar sin conexión. Los controles de relación de aspecto y zoom sólo cambian
el encuadre del monitor de preview; no recortan el video exportado.

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
faster-whisper sólo usa modelos que ya existan en la caché local (por defecto,
`~/.cache/huggingface/hub`); la aplicación no intenta descargarlos ni acceder a
la red.

## Arquitectura

`app/core` contiene modelos puros; `transcription` adapta motores locales;
`captions`, `ass` y `srt` implementan el dominio/exportación; `rendering` encapsula
FFmpeg; `services/pipeline.py` orquesta el flujo; `ui` contiene la interfaz Qt y
ejecuta tareas largas en hilos separados. La vista previa solicita fotogramas
con el mismo ASS/libass usado en la exportación final.
