"""Caption editing operations with snapshot-based undo and redo."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from math import isfinite
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QUndoCommand, QUndoStack

from app.captions.segmenter import CaptionSegmenter
from app.core.models import (
    CaptionStyleConfig,
    SegmentationOptions,
    TranscriptionResult,
    Word,
)
from app.editor.layout import relayout_project
from app.editor.models import CaptionEditorProject, EditableCaption, project_from_dict


class _ProjectCommand(QUndoCommand):
    def __init__(
        self,
        service: CaptionEditorService,
        before: CaptionEditorProject,
        after: CaptionEditorProject,
        merge_token: str | None,
    ) -> None:
        super().__init__("Editar captions")
        self._service = service
        self._before = before
        self._after = after
        self._merge_token = merge_token

    def id(self) -> int:
        return 1 if self._merge_token is not None else -1

    def mergeWith(self, other: QUndoCommand) -> bool:
        if (
            not isinstance(other, _ProjectCommand)
            or self._merge_token is None
            or self._merge_token != other._merge_token
        ):
            return False
        self._after = other._after
        return True

    def undo(self) -> None:
        self._service._apply_project(self._before)

    def redo(self) -> None:
        self._service._apply_project(self._after)


class CaptionEditorService:
    def __init__(self, project: CaptionEditorProject) -> None:
        self._project = project
        self._undo_stack = QUndoStack()
        self._undo_stack.setUndoLimit(100)

    @property
    def project(self) -> CaptionEditorProject:
        return self._project

    @classmethod
    def load(cls, path: Path) -> CaptionEditorService:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") in {2, 3, 4}:
            project = project_from_dict(data)
        elif data.get("version") == 1:
            from app.captions.segmenter import CaptionSegmenter
            from app.core.models import (
                CaptionStyleConfig,
                SegmentationOptions,
                TranscriptionOptions,
            )
            from app.editor.models import transcript_from_dict

            transcript_path = path.with_name("transcript.json")
            if not transcript_path.is_file():
                raise ValueError("No se encontró transcript.json junto al proyecto anterior.")
            transcript = transcript_from_dict(
                json.loads(transcript_path.read_text(encoding="utf-8"))
            )
            project = CaptionEditorProject.create(
                Path(data["source_video"]),
                transcript,
                CaptionSegmenter().segment(transcript, SegmentationOptions()),
                data.get("style_id", "tiktok_pop"),
                CaptionStyleConfig(**data.get("style_config", {})),
                TranscriptionOptions(**data.get("transcription", {})),
            )
        else:
            raise ValueError("La versión del proyecto no es compatible.")
        return cls(project)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(path, self._project.to_dict())

    def undo(self) -> bool:
        if not self._undo_stack.canUndo():
            return False
        self._undo_stack.undo()
        return True

    def redo(self) -> bool:
        if not self._undo_stack.canRedo():
            return False
        self._undo_stack.redo()
        return True

    def update_text(self, caption_id: str, text: str) -> None:
        caption = self._get_caption(caption_id)
        lines = text.splitlines() or [text]
        tokens = [token for line in lines for token in line.split()]
        if not tokens:
            raise ValueError("El caption no puede quedar vacío.")
        line_breaks = []
        token_count = 0
        for line in lines[:-1]:
            token_count += len(line.split())
            if 0 < token_count < len(tokens):
                line_breaks.append(token_count - 1)
        if len(tokens) == len(caption.words):
            words = tuple(replace(word, text=token) for word, token in zip(caption.words, tokens))
        else:
            duration = caption.end - caption.start
            words = tuple(
                Word(
                    token,
                    caption.start + duration * index / len(tokens),
                    caption.start + duration * (index + 1) / len(tokens),
                    caption.words[min(index, len(caption.words) - 1)].confidence,
                )
                for index, token in enumerate(tokens)
            )
        self._replace_caption(
            caption_id,
            replace(
                caption,
                words=words,
                line_breaks=tuple(line_breaks),
                line_breaks_manual=bool(line_breaks),
            ),
        )

    def move_caption(
        self, caption_id: str, x: float, y: float, *, merge_token: str | None = None
    ) -> None:
        if not isfinite(x) or not isfinite(y) or not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError("La posición normalizada debe estar entre 0 y 1.")
        self._replace_caption(
            caption_id,
            replace(
                self._get_caption(caption_id),
                x=x,
                y=y,
                position_override=True,
            ),
            merge_token=merge_token,
        )

    def resize_caption(
        self, caption_id: str, scale: float, *, merge_token: str | None = None
    ) -> None:
        if not isfinite(scale) or not 0.25 <= scale <= 4:
            raise ValueError("La escala debe estar entre 0.25 y 4.")
        self._replace_caption(
            caption_id,
            replace(self._get_caption(caption_id), scale=scale, scale_override=True),
            merge_token=merge_token,
        )

    def set_caption_width(
        self, caption_id: str, width: float, *, merge_token: str | None = None
    ) -> None:
        if not isfinite(width) or not 0.1 <= width <= 1:
            raise ValueError("El ancho máximo debe estar entre 0.1 y 1.")
        self._replace_caption(
            caption_id,
            replace(
                self._get_caption(caption_id),
                max_width=width,
                max_width_override=True,
            ),
            merge_token=merge_token,
        )

    def set_word_spacing(self, caption_id: str, spacing: float) -> None:
        self._replace_caption(
            caption_id, replace(self._get_caption(caption_id), word_spacing=spacing)
        )

    def rotate_caption(
        self, caption_id: str, degrees: float, *, merge_token: str | None = None
    ) -> None:
        if not isfinite(degrees) or not -180 <= degrees <= 180:
            raise ValueError("La rotación debe estar entre -180 y 180 grados.")
        self._replace_caption(
            caption_id,
            replace(
                self._get_caption(caption_id),
                rotation_deg=degrees,
                position_override=True,
            ),
            merge_token=merge_token,
        )

    def reset_caption_geometry(self, caption_id: str) -> None:
        y = {"top": 0.12, "center": 0.5, "bottom": 0.82}[
            self._project.style_config.position
        ]
        self._replace_caption(
            caption_id,
            replace(
                self._get_caption(caption_id),
                x=0.5,
                y=y,
                scale=1.0,
                rotation_deg=0.0,
                max_width=0.9,
                position_override=False,
                scale_override=False,
                max_width_override=False,
                style_id=None,
            ),
        )

    def duplicate_caption(self, caption_id: str) -> str:
        caption = self._get_caption(caption_id)
        duplicate = replace(caption, caption_id=str(uuid4()))
        captions = tuple(
            sorted(
                (*self._project.captions, duplicate),
                key=lambda current: current.start,
            )
        )
        self._commit(replace(self._project, captions=captions, edited=True))
        return duplicate.caption_id

    def set_timing(self, caption_id: str, start: float, end: float) -> None:
        if not isfinite(start) or not isfinite(end) or start < 0 or end <= start:
            raise ValueError("El final del caption debe ser posterior a su inicio.")
        caption = self._get_caption(caption_id)
        self._replace_caption(caption_id, replace(caption, start=start, end=end))

    def set_style(self, style_id: str, config: CaptionStyleConfig) -> None:
        if not style_id:
            raise ValueError("Selecciona un estilo válido.")
        y = {"top": 0.12, "center": 0.5, "bottom": 0.82}[config.position]
        captions = tuple(
            replace(caption, x=0.5, y=y)
            if not caption.position_override
            else caption
            for caption in self._project.captions
        )
        self._commit(
            replace(
                self._project,
                captions=captions,
                style_id=style_id,
                style_config=config,
                edited=True,
            )
        )

    def relayout(self) -> None:
        """Recalcula el ajuste sin cambiar el contenido ni la geometría manual."""
        self._commit(
            replace(relayout_project(self._project), edited=True),
            sort_captions=False,
        )

    def rebuild_from_transcript(
        self, transcript: TranscriptionResult | None = None
    ) -> None:
        """Reemplaza captions por una segmentación nueva del transcript original."""
        project = self._project
        source_transcript = transcript or project.original_transcript
        captions = CaptionSegmenter().segment(
            source_transcript, SegmentationOptions()
        )
        rebuilt = CaptionEditorProject.create(
            project.source_video,
            source_transcript,
            captions,
            project.style_id,
            project.style_config,
            project.transcription_options,
            project.video_metadata,
        )
        self._commit(replace(relayout_project(rebuilt), edited=True))

    def split_caption(self, caption_id: str, word_index: int) -> tuple[str, str]:
        caption = self._get_caption(caption_id)
        if not 0 < word_index < len(caption.words):
            raise ValueError("Selecciona un corte entre dos palabras.")
        left_words, right_words = caption.words[:word_index], caption.words[word_index:]
        left_end = min(caption.end, left_words[-1].end)
        right_start = max(caption.start, right_words[0].start)
        left_breaks = tuple(
            index for index in caption.line_breaks if index < len(left_words) - 1
        )
        right_breaks = tuple(
            index - word_index
            for index in caption.line_breaks
            if index >= word_index
        )
        left = replace(
            caption,
            end=left_end,
            words=left_words,
            line_breaks=left_breaks,
        )
        right = replace(
            caption,
            caption_id=str(uuid4()),
            start=right_start,
            words=right_words,
            line_breaks=right_breaks,
        )
        captions = list(self._project.captions)
        index = next(i for i, current in enumerate(captions) if current.caption_id == caption_id)
        captions[index : index + 1] = [left, right]
        self._commit(replace(self._project, captions=tuple(captions), edited=True))
        return left.caption_id, right.caption_id

    def merge_with_next(self, caption_id: str) -> str:
        captions = list(self._project.captions)
        index = next(i for i, caption in enumerate(captions) if caption.caption_id == caption_id)
        if index + 1 >= len(captions):
            raise ValueError("No hay un caption siguiente para unir.")
        first, second = captions[index], captions[index + 1]
        merged = replace(
            first,
            start=min(first.start, second.start),
            end=max(first.end, second.end),
            words=first.words + second.words,
            line_breaks_manual=(
                first.line_breaks_manual or second.line_breaks_manual
            ),
            line_breaks=tuple(
                sorted(
                    set(first.line_breaks)
                    | {
                        len(first.words) + index
                        for index in second.line_breaks
                    }
                )
            ),
        )
        captions[index : index + 2] = [merged]
        self._commit(replace(self._project, captions=tuple(captions), edited=True))
        return merged.caption_id

    def reorder_word(self, caption_id: str, source_index: int, target_index: int) -> None:
        caption = self._get_caption(caption_id)
        if not 0 <= source_index < len(caption.words) or not 0 <= target_index < len(caption.words):
            raise ValueError("El índice de palabra no es válido.")
        words = list(caption.words)
        words.insert(target_index, words.pop(source_index))
        self._replace_caption(
            caption_id,
            replace(
                caption,
                words=tuple(words),
                line_breaks=(),
                line_breaks_manual=False,
            ),
        )

    def move_word(
        self,
        source_caption_id: str,
        source_index: int,
        target_caption_id: str,
        target_index: int,
    ) -> None:
        source = self._get_caption(source_caption_id)
        target = self._get_caption(target_caption_id)
        if source_caption_id == target_caption_id:
            self.reorder_word(source_caption_id, source_index, target_index)
            return
        if not 0 <= source_index < len(source.words) or not 0 <= target_index <= len(target.words):
            raise ValueError("El índice de palabra no es válido.")
        word = source.words[source_index]
        source_words = source.words[:source_index] + source.words[source_index + 1 :]
        target_words = target.words[:target_index] + (word,) + target.words[target_index:]
        captions = []
        for caption in self._project.captions:
            if caption.caption_id == source_caption_id:
                if source_words:
                    captions.append(
                        replace(
                            caption,
                            start=min(word.start for word in source_words),
                            end=max(word.end for word in source_words),
                            words=source_words,
                            line_breaks=(),
                            line_breaks_manual=False,
                        )
                    )
            elif caption.caption_id == target_caption_id:
                captions.append(
                    replace(
                        caption,
                        start=min(target.start, word.start),
                        end=max(target.end, word.end),
                        words=target_words,
                        line_breaks=(),
                        line_breaks_manual=False,
                    )
                )
            else:
                captions.append(caption)
        self._commit(replace(self._project, captions=tuple(captions), edited=True))

    def delete_word(self, caption_id: str, word_index: int) -> None:
        caption = self._get_caption(caption_id)
        if not 0 <= word_index < len(caption.words):
            raise ValueError("El índice de palabra no es válido.")
        words = caption.words[:word_index] + caption.words[word_index + 1 :]
        captions = []
        for current in self._project.captions:
            if current.caption_id != caption_id:
                captions.append(current)
            elif words:
                start = max(current.start, min(word.start for word in words))
                end = min(current.end, max(word.end for word in words))
                captions.append(
                    replace(
                        current,
                        start=start,
                        end=end,
                        words=words,
                        line_breaks=(),
                        line_breaks_manual=False,
                    )
                )
        self._commit(replace(self._project, captions=tuple(captions), edited=True))

    def delete_caption(self, caption_id: str) -> None:
        self._get_caption(caption_id)
        captions = tuple(
            caption
            for caption in self._project.captions
            if caption.caption_id != caption_id
        )
        self._commit(replace(self._project, captions=captions, edited=True))

    def _get_caption(self, caption_id: str) -> EditableCaption:
        try:
            return next(
                caption
                for caption in self._project.captions
                if caption.caption_id == caption_id
            )
        except StopIteration as exc:
            raise ValueError(f"No existe el caption «{caption_id}».") from exc

    def _replace_caption(
        self,
        caption_id: str,
        updated: EditableCaption,
        *,
        merge_token: str | None = None,
    ) -> None:
        captions = tuple(
            updated if caption.caption_id == caption_id else caption
            for caption in self._project.captions
        )
        self._commit(
            replace(self._project, captions=captions, edited=True),
            merge_token=merge_token,
        )

    def _commit(
        self,
        project: CaptionEditorProject,
        *,
        sort_captions: bool = True,
        merge_token: str | None = None,
    ) -> None:
        if sort_captions:
            project = replace(
                project,
                captions=tuple(sorted(project.captions, key=lambda caption: caption.start)),
            )
        if project == self._project:
            return
        self._undo_stack.push(
            _ProjectCommand(self, self._project, project, merge_token)
        )

    def _apply_project(self, project: CaptionEditorProject) -> None:
        self._project = project


def _write_json_atomic(path: Path, data: dict) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(data, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
