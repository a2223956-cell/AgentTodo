"""Turn dropped files / pasted text into analyzable content blocks."""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

from .const import IMAGE_EXT, DOCX_EXT, BINARY_EXT, MAX_FILE_CHARS


@dataclass
class DropContent:
    """Everything gathered from one drop / submit, ready for the analyzer."""
    label: str = ""                       # short human label ("会议纪要.txt", "2 个文件 + 1 张图片", "输入文字")
    text_blocks: list[tuple[str, str]] = field(default_factory=list)  # (label, text)
    image_paths: list[str] = field(default_factory=list)
    other_paths: list[str] = field(default_factory=list)              # passed to agent by path

    def text_for_prompt(self) -> str:
        return "\n\n".join(f"【{lab}】\n{txt}" for lab, txt in self.text_blocks)


def read_text_file(path: str) -> str | None:
    """Best-effort decode of a small text file (utf-8 / gbk / utf-16)."""
    try:
        if os.path.getsize(path) > 2_000_000:
            return None
        with open(path, "rb") as f:
            raw = f.read(MAX_FILE_CHARS * 4)
    except OSError:
        return None
    if b"\x00" in raw[:1024]:  # binary sniff (utf-16 handled below by decode)
        if not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            return None
    for enc in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        text = text.replace("\x00", "").strip()
        return text[:MAX_FILE_CHARS] if text else None
    return None


def read_docx(path: str) -> str | None:
    """Extract paragraph text from .docx via stdlib zipfile (no extra deps)."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (OSError, zipfile.BadZipFile, KeyError):
        return None
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:MAX_FILE_CHARS] if text else None


def extract_file_text(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in DOCX_EXT:
        return read_docx(path)
    if ext in IMAGE_EXT or ext in BINARY_EXT:
        return None
    return read_text_file(path)


def build_drop(paths: list[str] | None = None, text: str | None = None,
               label: str = "") -> DropContent:
    """Classify dropped items (and/or dropped/typed text) into a DropContent."""
    paths = [p for p in (paths or []) if p and os.path.exists(p)]
    dc = DropContent(label=label)
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        name = os.path.basename(p)
        if ext in IMAGE_EXT:
            dc.image_paths.append(p)
            continue
        content = extract_file_text(p)
        if content:
            dc.text_blocks.append((name, content))
        else:
            dc.other_paths.append(p)  # e.g. pdf/xlsx — the agent reads it by path

    if text and text.strip():
        dc.text_blocks.append((label or "输入文字", text.strip()[:MAX_FILE_CHARS]))

    if not dc.label:
        bits = []
        if dc.text_blocks:
            bits.append(f"{len(dc.text_blocks)} 段文本")
        if dc.image_paths:
            bits.append(f"{len(dc.image_paths)} 张图片")
        if dc.other_paths:
            bits.append(f"{len(dc.other_paths)} 个文件")
        dc.label = " + ".join(bits) or "空内容"
    return dc
