"""
tree.py — выводит дерево проекта и (опционально) содержимое всех файлов.

Использование:
    python scripts/tree.py                        # только дерево
    python scripts/tree.py --full-content         # дерево + содержимое всех файлов
    python scripts/tree.py --content-only         # только содержимое
    python scripts/tree.py --path .               # корень проекта

Исключаются: .venv, .git, __pycache__, cookies, logs, .env, *.db, *.pyc,
бинарные файлы, .pytest_cache и прочий мусор. README и Dockerfile
включаются.
"""

import argparse
import fnmatch
import sys
from pathlib import Path
from typing import List, Set


# ============ Игнор-лист ============
IGNORE_PATTERNS = {
    # Python-кэш и окружения
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    "*.egg-info",
    ".eggs",
    "build",
    "dist",
    "downloads",
    "wheels",

    # IDE
    ".vscode",
    ".idea",
    "*.swp",
    "*.swo",
    "*.sublime-project",
    "*.sublime-workspace",

    # Секреты и куки (нельзя отправлять)
    ".env",
    ".env.local",
    ".env.prod",
    ".env.*.local",
    "cookies",
    "*.pem",
    "*.key",

    # Логи и отладка
    "logs",
    "*.log",
    "*.tmp",
    "*.dump",
    "debug_*.py",
    "debug_*.html",
    "*_debug.py",

    # Бинарники и БД
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "Thumbs.db",
    "pgdata",
    "pg_data",

    # Сам скрипт и его окружение
    "tree.py",
    ".gitignore",

    # Кэш Python
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    "*.egg",
    "*.whl",
    "*.lock",
    "uv.lock",
    "poetry.lock",
    "script.py.mako",
}

# Расширения, которые точно не читаем как текст
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pyc", ".pyo", ".pyd", ".whl", ".egg",
    ".lock", ".db", ".sqlite", ".sqlite3",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".avi", ".mov",
    ".doc", ".docx", ".xls", ".xlsx",
}


class IgnoreChecker:
    """Проверка игнорирования с поддержкой .gitignore."""

    def __init__(self, patterns: Set[str], gitignore_path: Path = None):
        self.suffix_patterns: List[str] = []
        self.dir_patterns: List[str] = []
        self.exact_patterns: List[str] = []
        self.glob_patterns: List[str] = []

        self._compile(patterns)

        if gitignore_path and gitignore_path.exists():
            self._load_gitignore(gitignore_path)

    def _compile(self, patterns: Set[str]):
        for raw in patterns:
            pattern = raw.strip()
            if not pattern or pattern.startswith('#'):
                continue
            if pattern.startswith("*."):
                self.suffix_patterns.append(pattern[1:])
            elif pattern.endswith("/"):
                self.dir_patterns.append(pattern.rstrip('/'))
            elif '*' in pattern or '?' in pattern:
                self.glob_patterns.append(pattern)
            else:
                self.exact_patterns.append(pattern)

    def _load_gitignore(self, gitignore_path: Path):
        try:
            with open(gitignore_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        self._compile({line})
        except Exception as e:
            print(f"# [.gitignore: {e}]", file=sys.stderr)

    def should_ignore(self, rel_path: Path) -> bool:
        rel_str = str(rel_path).replace("\\", "/")
        rel_name = rel_path.name
        rel_suffix = rel_path.suffix.lower()

        for suffix in self.suffix_patterns:
            if rel_name.endswith(suffix):
                return True

        for dir_pat in self.dir_patterns:
            # Директория совпадает, если это она сама или путь через неё
            if rel_str == dir_pat or rel_str.startswith(dir_pat + "/"):
                return True
            if rel_name == dir_pat:
                return True

        for exact in self.exact_patterns:
            if rel_str == exact or rel_name == exact:
                return True

        for glob_pat in self.glob_patterns:
            if fnmatch.fnmatch(rel_name, glob_pat):
                return True
            if fnmatch.fnmatch(rel_str, glob_pat):
                return True

        return False

    def is_binary(self, path: Path) -> bool:
        if path.suffix.lower() in BINARY_EXTS:
            return True
        try:
            with open(path, 'rb') as f:
                chunk = f.read(8192)
                if b'\x00' in chunk:
                    return True
        except Exception:
            pass
        return False


def walk_tree(dir_path: Path, ignore_checker: IgnoreChecker,
              root: Path, prefix: str = "") -> List[str]:
    lines: List[str] = []
    try:
        entries = []
        for p in dir_path.iterdir():
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = Path(p.name)
            if not ignore_checker.should_ignore(rel):
                entries.append(p)
        entries.sort(key=lambda p: (p.is_file(), p.name.lower()))
    except (PermissionError, OSError) as e:
        lines.append(prefix + f"└── [ошибка: {e}]")
        return lines

    if not entries:
        return lines

    pointers = ["├── "] * (len(entries) - 1) + ["└── "]
    for pointer, path in zip(pointers, entries):
        lines.append(prefix + pointer + path.name)
        if path.is_dir():
            ext = "│   " if pointer == "├── " else "    "
            lines.extend(walk_tree(path, ignore_checker, root, prefix + ext))
    return lines


def dump_contents(dir_path: Path, ignore_checker: IgnoreChecker,
                  root: Path, root_prefix: str):
    """Печатает содержимое всех файлов, с указанием полного пути от корня проекта."""
    try:
        entries = []
        for p in dir_path.iterdir():
            try:
                rel = p.relative_to(root)
            except ValueError:
                rel = Path(p.name)
            if not ignore_checker.should_ignore(rel):
                entries.append(p)
        entries.sort(key=lambda p: (p.is_file(), p.name.lower()))
    except (PermissionError, OSError):
        return

    for path in entries:
        if path.is_dir():
            dump_contents(path, ignore_checker, root, root_prefix)
        elif path.is_file():
            if ignore_checker.is_binary(path):
                continue

            rel = path.relative_to(root)
            # Полный путь: hhparser/app/db/models.py
            display = f"{root_prefix}/{rel}".replace("\\", "/")

            print(f"\n\n{'=' * 78}")
            print(f"# Файл: {display}")
            print(f"{'=' * 78}")

            try:
                with path.open(encoding="utf-8", errors="replace") as f:
                    for line in f:
                        print(line.rstrip())
            except (OSError, UnicodeDecodeError) as e:
                print(f"# [Ошибка чтения: {e}]")


def main():
    parser = argparse.ArgumentParser(
        description="Дерево проекта и (опционально) содержимое всех файлов.",
    )
    parser.add_argument(
        "--path", default=".",
        help="Корневая директория (по умолчанию: текущая)",
    )
    parser.add_argument(
        "--full-content", action="store_true",
        help="Показать дерево + содержимое всех файлов",
    )
    parser.add_argument(
        "--content-only", action="store_true",
        help="Показать только содержимое (без дерева)",
    )
    parser.add_argument(
        "--no-gitignore", action="store_true",
        help="Не загружать паттерны из .gitignore",
    )

    args = parser.parse_args()
    root = Path(args.path).resolve()

    if not root.exists() or not root.is_dir():
        print(f"Ошибка: {root} не существует или не директория.", file=sys.stderr)
        sys.exit(1)

    gitignore = None if args.no_gitignore else (root / ".gitignore")
    checker = IgnoreChecker(IGNORE_PATTERNS, gitignore)

    # Структура — всегда в начале, если не content-only
    if not args.content_only:
        print("СТРУКТУРА ПРОЕКТА:")
        print(f"{root.name}/")
        for line in walk_tree(root, checker, root):
            print(line)

    # Содержимое
    if args.full_content or args.content_only:
        if not args.content_only:
            print(f"\n{'#' * 78}")
            print(f"# СОДЕРЖИМОЕ ФАЙЛОВ")
            print(f"{'#' * 78}")
        dump_contents(root, checker, root, root.name)


if __name__ == "__main__":
    main()