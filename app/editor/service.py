"""Caption editing operations with snapshot-based undo and redo."""

from __future__ import annotations

import json
from dataclasses import replace
from math import isfinite
from pathlib import Path
from uuid import uuid4

from app.core.models import CaptionStyleConfig, Word
from app.editor.models import CaptionEditorProject, EditableCaption, project_from_dict


class CaptionEditorService:
    def __init__(self, project: CaptionEditorProject) -> None:
        self._project = project
        self._undo: list[CaptionEditorProject] = []
        self._redo: list[CaptionEditorProject] = []

    @property
    def project(self) -> CaptionEditorProject:
        return self._project

    @classmethod
    def load(cls, path: Path) -> CaptionEditorService:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") == 2:
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
        path.write_text(
            json.dumps(self._project.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._project)
        self._project = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._project)
        self._project = self._redo.pop()
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
            replace(caption, words=words, line_breaks=tuple(line_breaks)),
        )

    def move_caption(self, caption_id: str, x: float, y: float) -> None:
        if not isfinite(x) or not isfinite(y) or not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError("La posición normalizada debe estar entre 0 y 1.")
        self._replace_caption(caption_id, replace(self._get_caption(caption_id), x=x, y=y))

    def resize_caption(self, caption_id: str, scale: float) -> None:
        self._replace_caption(
            caption_id, replace(self._get_caption(caption_id), scale=scale)
        )

    def set_word_spacing(self, caption_id: str, spacing: float) -> None:
        self._replace_caption(
            caption_id, replace(self._get_caption(caption_id), word_spacing=spacing)
        )

    def rotate_caption(self, caption_id: str, degrees: float) -> None:
        self._replace_caption(
            caption_id,
            replace(self._get_caption(caption_id), rotation_deg=degrees),
        )

    def set_timing(self, caption_id: str, start: float, end: float) -> None:
        if not isfinite(start) or not isfinite(end) or start < 0 or end <= start:
            raise ValueError("El final del caption debe ser posterior a su inicio.")
        caption = self._get_caption(caption_id)
        self._replace_caption(caption_id, replace(caption, start=start, end=end))

    def set_style(self, style_id: str, config: CaptionStyleConfig) -> None:
        if not style_id:
            raise ValueError("Selecciona un estilo válido.")
        self._commit(replace(self._project, style_id=style_id, style_config=config, edited=True))

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
            caption_id, replace(caption, words=tuple(words), line_breaks=())
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
                    replace(current, start=start, end=end, words=words, line_breaks=())
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

    def _replace_caption(self, caption_id: str, updated: EditableCaption) -> None:
        captions = tuple(
            updated if caption.caption_id == caption_id else caption
            for caption in self._project.captions
        )
        self._commit(replace(self._project, captions=captions, edited=True))

    def _commit(self, project: CaptionEditorProject) -> None:
        project = replace(
            project,
            captions=tuple(sorted(project.captions, key=lambda caption: caption.start)),
        )
        if project == self._project:
            return
        self._undo.append(self._project)
        if len(self._undo) > 100:
            del self._undo[0]
        self._project = project
        self._redo.clear()
