#!/usr/bin/env python3
"""
migrar_media.py
1) Extrae todos los data:image/...;base64,... de data.json a archivos reales en media/
2) Reemplaza esos base64 por URLs/rutas a los archivos extraídos
3) Guarda data.json actualizado (con backup data.json.bak)
4) Lista archivos de media/ sin referencia en el proyecto
5) Con --delete-unused, borra esos archivos no referenciados
"""

import argparse
import base64
import json
import os
import re
import shutil
from pathlib import Path

EXT_BY_MIME = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/avif": "avif",
}

DATA_URI_RE = re.compile(
    r"^data:(image/[^;,]+)(?:;[^,]*)?;base64,(.*)$",
    re.DOTALL | re.IGNORECASE,
)

MEDIA_TOKEN_RE = re.compile(
    r"[A-Za-z0-9._%+-]+\.(?:jpg|jpeg|png|gif|webp|svg|avif|ico|bmp)",
    re.IGNORECASE,
)

TEXT_EXTS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".cjs",
    ".ts", ".tsx", ".jsx", ".json", ".md", ".txt",
    ".xml", ".svg", ".vue", ".svelte", ".php",
    ".py", ".rb", ".java", ".cs", ".go", ".rs",
}

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", ".next", ".nuxt",
    "coverage", "vendor", "__pycache__", "media.bak",
}

SKIP_FILE_SUFFIXES = {".bak", ".backup", ".orig"}


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:120] or "embedded"


def ext_from_mime(mime: str) -> str:
    return EXT_BY_MIME.get(mime.lower().strip(), "bin")


def base_name_from_parts(parts) -> str:
    parts_str = [str(p) for p in parts]

    if "heroImages" in parts_str:
        i = parts_str.index("heroImages")
        if i + 1 < len(parts):
            try:
                idx = int(parts[i + 1])
                return f"hero-{idx + 1:03d}"
            except ValueError:
                pass

    return slugify("-".join(parts_str))


def unique_name(media_dir: Path, base: str, ext: str, raw: bytes) -> str:
    name = f"{base}.{ext}"
    i = 2
    while True:
        p = media_dir / name
        if not p.exists():
            return name
        if p.read_bytes() == raw:
            return name
        name = f"{base}-{i}.{ext}"
        i += 1


def walk_and_extract(node, parts, media_dir: Path, base_url: str, stats: dict):
    if isinstance(node, dict):
        for k, v in list(node.items()):
            node[k] = walk_and_extract(v, parts + (k,), media_dir, base_url, stats)
        return node

    if isinstance(node, list):
        for i, v in enumerate(list(node)):
            node[i] = walk_and_extract(v, parts + (i,), media_dir, base_url, stats)
        return node

    if isinstance(node, str) and node.startswith("data:image/"):
        m = DATA_URI_RE.match(node)
        if not m:
            stats["invalid_data_uri"] += 1
            return node

        mime, b64 = m.groups()
        b64 = re.sub(r"\s+", "", b64)

        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception as e:
            stats["decode_error"] += 1
            print(f"[ERROR] No se pudo decodificar {parts}: {e}")
            return node

        ext = ext_from_mime(mime)
        base = base_name_from_parts(parts)
        name = unique_name(media_dir, base, ext, raw)

        out = media_dir / name
        if not out.exists():
            out.write_bytes(raw)
            stats["extracted"] += 1
            print(f"[OK] {parts} -> media/{name}")
        else:
            stats["reused"] += 1
            print(f"[REUSED] {parts} -> media/{name}")

        if base_url:
            return base_url.rstrip("/") + "/" + name
        return f"media/{name}"

    return node


def scan_used_files(root: Path, media_dir: Path, keep: set) -> set:
    used = set()

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath).resolve()

        if current == media_dir or media_dir in current.parents:
            continue

        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and (Path(dirpath) / d).resolve() != media_dir
        ]

        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in SKIP_FILE_SUFFIXES:
                continue
            if p.suffix.lower() not in TEXT_EXTS:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for token in MEDIA_TOKEN_RE.findall(text):
                used.add(token)
                used.add(Path(token).name)

    return used


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data.json")
    parser.add_argument("--media", default="media")
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--base-url",
        default="https://raw.githubusercontent.com/grobdsgn/grobdsgn/main/media/",
        help="Prefijo para las nuevas rutas. Usa 'media/' para rutas relativas.",
    )
    parser.add_argument(
        "--delete-unused",
        action="store_true",
        help="Borra de verdad los archivos de media sin referencia. Sin esto solo lista.",
    )
    parser.add_argument(
        "--keep",
        nargs="*",
        default=[".gitkeep"],
        help="Nombres de archivos de media que no se deben borrar nunca.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    data_path = (root / args.data).resolve()
    media_dir = (root / args.media).resolve()

    if not data_path.exists():
        raise SystemExit(f"No existe {data_path}")
    media_dir.mkdir(parents=True, exist_ok=True)

    bak = data_path.with_suffix(data_path.suffix + ".bak")
    shutil.copy2(data_path, bak)
    print(f"Backup creado: {bak}")

    data = json.loads(data_path.read_text(encoding="utf-8"))

    stats = {
        "extracted": 0,
        "reused": 0,
        "invalid_data_uri": 0,
        "decode_error": 0,
    }

    new_data = walk_and_extract(data, (), media_dir, args.base_url, stats)

    data_path.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("\nResumen extracción:")
    print(f"  Nuevas:            {stats['extracted']}")
    print(f"  Reutilizadas:      {stats['reused']}")
    print(f"  Data URI inválidas:{stats['invalid_data_uri']}")
    print(f"  Errores decode:    {stats['decode_error']}")
    print(f"  JSON actualizado:  {data_path}")

    used = scan_used_files(root, media_dir, set(args.keep))
    used_lower = {u.lower() for u in used}
    keep = set(args.keep)

    unused = []
    for p in media_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.name in keep:
            continue
        if p.name in used or p.name.lower() in used_lower:
            continue
        unused.append(p)

    if not unused:
        print("\nNo hay archivos en media sin referencia.")
        return

    print("\nArchivos en media SIN referencia detectada:")
    for p in sorted(unused):
        print(f"  {p.relative_to(root)}")
    print(f"Total: {len(unused)} archivo(s).")

    if args.delete_unused:
        print("\nBorrando archivos no referenciados...")
        for p in unused:
            p.unlink()
            print(f"  borrado: {p.relative_to(root)}")
        print("Listo.")
    else:
        print("\nModo simulación: no se borró nada.")
        print("Revisa la lista y, si estás seguro, ejecuta de nuevo con --delete-unused.")


if __name__ == "__main__":
    main()