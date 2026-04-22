#!/usr/bin/env python3
import os
import re
import json
import html as html_lib
from pathlib import Path
from ebooklib import epub

# --- CONFIGURATION ---
BASE_DIR = Path("d:/1. Project/Ranobe")
DATA_DIR = BASE_DIR / "data_infinite"
TRANSLATED_DIR = DATA_DIR / "translated"
OUTPUT_DIR = BASE_DIR / "output"

BOOK_TITLE = "Infinite Save: I Cultivate Immortality Through Reincarnation"
AUTHOR = "独角戏ultra (Dújiǎoxì ultra)"
LANGUAGE = "ru"

def clean_html(text):
    """Simple text to HTML conversion with paragraph wrapping and escaping."""
    if not text:
        return ""
    
    # Escape HTML special characters
    escaped = html_lib.escape(text)
    
    # Replace double newlines with paragraph tags
    paragraphs = escaped.split("\n")
    html_out = ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # Add class for dialogs if they start with dashes or quotes
        if p.startswith("&laquo;") or p.startswith("&mdash;") or p.startswith("—"):
            html_out += f'<p style="text-indent: 0;">{p}</p>\n'
        else:
            html_out += f'<p style="text-indent: 1.5em; margin: 0.5em 0; text-align: justify;">{p}</p>\n'
            
    return html_out

def assemble_book(start_ch=None, end_ch=None):
    print(f"Starting EPUB assembly for: {BOOK_TITLE}")
    if start_ch and end_ch:
        print(f"Range: {start_ch}-{end_ch}")
    
    # Create output directory if not exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Initialize EPUB
    book = epub.EpubBook()
    book.set_identifier(f"infinite-save-{start_ch}-{end_ch}" if start_ch else "infinite-save-v1")
    book.set_title(f"{BOOK_TITLE} ({start_ch}-{end_ch})" if start_ch else BOOK_TITLE)
    book.set_language(LANGUAGE)
    book.add_author(AUTHOR)
    
    # Add CSS
    style = epub.EpubItem(
        uid="style_nav",
        file_name="style/nav.css",
        media_type="text/css",
        content=b"body { font-family: serif; line-height: 1.6; }"
    )
    book.add_item(style)

    # Collect and sort chapters
    translated_files = list(TRANSLATED_DIR.glob("chapter_*.txt"))
    translated_files.sort(key=lambda x: int(re.search(r'(\d+)', x.name).group(1)))

    if not translated_files:
        print("Error: No translated chapters found!")
        return

    # Filter by range
    if start_ch is not None or end_ch is not None:
        filtered = []
        for f in translated_files:
            num = int(re.search(r'(\d+)', f.name).group(1))
            if (start_ch is None or num >= start_ch) and (end_ch is None or num <= end_ch):
                filtered.append(f)
        translated_files = filtered

    if not translated_files:
        print("Error: No translated chapters in the specified range!")
        return

    print(f"Processing {len(translated_files)} chapters.")

    chapters = []
    spine = ['nav']
    
    actual_min = 9999
    actual_max = 0

    for file_path in translated_files:
        ch_num_match = re.search(r'(\d+)', file_path.name)
        if not ch_num_match:
            continue
        
        ch_num = int(ch_num_match.group(1))
        actual_min = min(actual_min, ch_num)
        actual_max = max(actual_max, ch_num)
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Skipping {file_path.name}: {e}")
            continue

        title = data.get("title", f"Глава {ch_num}")
        text = data.get("text", "")
        
        # Create chapter item
        c = epub.EpubHtml(
            title=title,
            file_name=f"chapter_{ch_num:04d}.xhtml",
            lang=LANGUAGE
        )
        
        # Wrap content
        content = f"<h1>{title}</h1>\n{clean_html(text)}"
        c.content = content
        
        book.add_item(c)
        chapters.append(c)
        spine.append(c)
        print(f"  Processed Chapter {ch_num}")

    # Set TOC and Spine
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    # Write file
    output_filename = OUTPUT_DIR / f"Infinite_Save_RU_Chapters_{actual_min}-{actual_max}.epub"

    epub.write_epub(output_filename, book, {})
    
    print(f"\nSUCCESS: Book assembled at {output_filename}")

if __name__ == "__main__":
    import sys
    s = int(sys.argv[1]) if len(sys.argv) > 1 else None
    e = int(sys.argv[2]) if len(sys.argv) > 2 else None
    assemble_book(s, e)
