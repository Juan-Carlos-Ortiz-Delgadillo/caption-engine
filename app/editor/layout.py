"""Layout recalculable para captions sin reemplazar las ediciones del usuario."""

from dataclasses import replace

from app.captions.balance_text import fit_manual_caption, layout_captions
from app.core.exceptions import CaptionLayoutError
from app.editor.models import CaptionEditorProject, EditableCaption


def relayout_project(project: CaptionEditorProject) -> CaptionEditorProject:
    metadata = project.video_metadata
    if metadata is None:
        raise ValueError("El proyecto no contiene las dimensiones del video.")

    captions: list[EditableCaption] = []
    for caption in project.captions:
        bold_style = (
            caption.style_id or project.style_id
        ) == "bold_highlight"
        if caption.line_breaks_manual:
            font_size, warning = fit_manual_caption(
                caption.to_caption(),
                project.style_config,
                metadata.width,
                metadata.height,
                bold_style=bold_style,
            )
            captions.append(
                replace(
                    caption,
                    layout_font_size=font_size,
                    layout_warning=warning,
                )
            )
            continue
        try:
            laid_out, font_sizes = layout_captions(
                (caption.to_caption(),),
                project.style_config,
                metadata.width,
                metadata.height,
                bold_style=bold_style,
            )
        except CaptionLayoutError as exc:
            captions.append(
                replace(
                    caption,
                    layout_font_size=None,
                    layout_warning=str(exc),
                )
            )
            continue

        if laid_out[0].words != caption.words:
            captions.append(
                replace(
                    caption,
                    layout_font_size=None,
                    layout_warning=(
                        "El texto solo cabe al dividir una palabra; ajusta el caption "
                        "manualmente para conservar su texto."
                    ),
                )
            )
            continue

        captions.append(
            replace(
                caption,
                line_breaks=(
                    caption.line_breaks
                    if caption.line_breaks_manual
                    else laid_out[0].line_breaks
                ),
                layout_font_size=font_sizes[0],
                layout_warning=None,
            )
        )

    return replace(project, captions=tuple(captions))
