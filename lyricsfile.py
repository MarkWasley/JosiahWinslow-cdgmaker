"""Lyricsfile 1.0 YAML reader for CDG Maker.

Lyricsfile timestamps are milliseconds; CDG Maker uses centiseconds.
"""
from pathlib import Path
from typing import Any

import yaml

from .config import Settings, SettingsLyric, SettingsSinger


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _unique_mapping(loader: UniqueKeySafeLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_mapping,
)


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"{name} must be a YAML mapping")
    return value


def _require_int(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _word_text_for_cdg(words: list[dict[str, Any]]) -> str:
    # CDG Maker treats '/' as a syllable boundary and '_' as a literal space
    # within a syllable. A slash between segments therefore makes each
    # Lyricsfile word one karaoke highlight segment.
    segments: list[str] = []
    for word in words:
        text = word["text"]
        # Lyricsfile stores spaces in word.text (usually trailing spaces).
        # The source renderer uses underscores for spaces inside a syllable.
        segments.append(text.replace(" ", "_").replace("/", "_"))
    return "/".join(segments)


def settings_from_lyricsfile(path: str | Path) -> Settings:
    """Load a Lyricsfile 1.0 document and convert it to CDG Maker settings."""
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as stream:
            document = yaml.load(stream, Loader=UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid Lyricsfile YAML: {exc}") from exc

    data = _require_mapping(document, "Lyricsfile")
    if data.get("version") != "1.0":
        raise ValueError("Unsupported Lyricsfile version; expected '1.0'")

    metadata = _require_mapping(data.get("metadata"), "metadata")
    title = metadata.get("title")
    artist = metadata.get("artist")
    if not isinstance(title, str) or not isinstance(artist, str):
        raise ValueError("metadata.title and metadata.artist must be strings")
    instrumental = metadata.get("instrumental", False)
    if not isinstance(instrumental, bool):
        raise ValueError("metadata.instrumental must be a boolean")
    if instrumental:
        raise ValueError("Instrumental Lyricsfiles do not contain lyrics to render")

    lines = data.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("CDG Maker needs synchronized 'lines'; plain-only Lyricsfiles cannot be rendered")

    text_lines: list[str] = []
    sync: list[int] = []
    for line_index, raw_line in enumerate(lines, start=1):
        line = _require_mapping(raw_line, f"lines[{line_index - 1}]")
        line_text = line.get("text")
        if not isinstance(line_text, str):
            raise ValueError(f"lines[{line_index - 1}].text must be a string")
        _require_int(line.get("start_ms"), f"lines[{line_index - 1}].start_ms")
        raw_words = line.get("words")
        if raw_words:
            if not isinstance(raw_words, list):
                raise ValueError(f"lines[{line_index - 1}].words must be a sequence")
            words: list[dict[str, Any]] = []
            for word_index, raw_word in enumerate(raw_words):
                word = _require_mapping(raw_word, f"lines[{line_index - 1}].words[{word_index}]")
                word_text = word.get("text")
                if not isinstance(word_text, str):
                    raise ValueError(f"lines[{line_index - 1}].words[{word_index}].text must be a string")
                start_ms = _require_int(
                    word.get("start_ms"),
                    f"lines[{line_index - 1}].words[{word_index}].start_ms",
                )
                if "end_ms" in word:
                    end_ms = _require_int(word["end_ms"], f"lines[{line_index - 1}].words[{word_index}].end_ms")
                    if end_ms < start_ms:
                        raise ValueError("A word end_ms cannot be earlier than its start_ms")
                words.append({"text": word_text, "start_ms": start_ms})
            if not words:
                continue
        else:
            # A line-synced Lyricsfile remains usable: the whole line is one
            # highlight segment when word-level timing is absent.
            words = [{"text": line_text, "start_ms": line["start_ms"]}]

        if "end_ms" in line:
            end_ms = _require_int(line["end_ms"], f"lines[{line_index - 1}].end_ms")
            if end_ms < line["start_ms"]:
                raise ValueError("A line end_ms cannot be earlier than its start_ms")

        text_lines.append(_word_text_for_cdg(words))
        sync.extend(round(word["start_ms"] / 10) for word in words)

    if not text_lines or not sync:
        raise ValueError("Lyricsfile contains no synchronized lyric segments")
    if any(current < previous for previous, current in zip(sync, sync[1:])):
        raise ValueError("CDG Maker requires lyric word start times to be in chronological order")

    audio_file = (
        source.name[:-len(".lyricsfile.yaml")] + ".mp3"
        if source.name.lower().endswith(".lyricsfile.yaml")
        else source.with_suffix(".mp3").name
    )
    font = Path("fonts/DMSans-VariableFont_opsz,wght.ttf")
    return Settings(
        title=title,
        artist=artist,
        file=Path(audio_file),
        font=font,
        singers=[SettingsSinger()],
        lyrics=[SettingsLyric(
            text="\n".join(text_lines),
            sync=sync,
            line_tile_height=2,
            lines_per_page=4,
        )],
    )
