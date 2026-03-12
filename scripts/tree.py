"""
tree.py - выводит дерево проекта и (опционально) содержимое всех файлов.
Исключает: дебаг-файлы, *.txt, README, .env, логи, куки, coverage.

Использование:
    python tree.py [путь]             # только дерево
    python tree.py [путь] --full-content   # дерево + содержимое всех файлов

Опции:
    --full-content   Показать содержимое всех файлов после дерева (без ограничения размера)
"""

import argparse
import sys
import fnmatch
from pathlib import Path
from typing import List, Set, Tuple

# Список игнорируемых элементов (расширенный для безопасности и чистоты)
IGNORE_LIST = {
    # Python cache & virtual environments
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "env",
    "ENV",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "htmlcov",
    "*.egg-info/",
    ".eggs/",
    "build/",
    "dist/",
    "downloads/",
    "wheels/",

    # IDE & Editor
    ".vscode",
    ".idea",
    "*.swp",
    "*.swo",
    "*.sublime-project",
    "*.sublime-workspace",

    # Environment & Secrets
    ".env",
    ".env.local",
    ".env.prod",
    ".env.example",
    ".env.*.local",
    "*cookies*.txt",
    "*.pem",
    "*.key",

    # Logs & Debug
    "*.log",
    "*.tmp",
    "*.dump",
    "debug_*.py",
    "debug_*.html",
    "*_debug.py",
    "scripts/",
    "cookies/"
    "scripts/debug_*",
    "scripts/*.dump",
    "scripts/*.json",

    # Documentation (по запросу)
    "README.md",
    "README.rst",
    "README.txt",
    "CHANGELOG.md",
    "LICENSE",
    "MANIFEST",

    # Database & Binary
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "Thumbs.db",

    # Build & Deploy
    "alembic/versions",
    "script.py.mako",
    "uv.lock",
    "poetry.lock",
    "requirements.txt",  # опционально

    # Project specific
    "tree.py",  # сам скрипт
    "ansible",
    "terraform",
    "redis/data",
    "postgres/data",
    "pg_data",
    "pgdata",
    "old_app/",
    "tmp/",
    ".Python",
    "*.pyc",
    "*.py[cod]",
    "*$py.class",
    "*.so",
    "*.egg",
    "*.whl",
    "*.lock",
}

# Расширения бинарных файлов, которые не читаем как текст
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".pyc", ".pyo", ".pyd", ".whl", ".egg",
    ".lock", ".db", ".sqlite", ".sqlite3",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".avi", ".mov",
    ".doc", ".docx", ".xls", ".xlsx",
}


class IgnoreChecker:
    """Класс для проверки игнорирования файлов с поддержкой .gitignore."""

    def __init__(self, ignore_list: Set[str], gitignore_path: Path = None):
        self.suffix_patterns = []
        self.dir_patterns = []
        self.exact_patterns = []
        self.glob_patterns = []

        self.compile_patterns(ignore_list)

        # Загружаем паттерны из .gitignore если файл существует
        if gitignore_path and gitignore_path.exists():
            self.load_gitignore(gitignore_path)

    def compile_patterns(self, ignore_list: Set[str]):
        for pattern in ignore_list:
            pattern = pattern.strip()
            if not pattern or pattern.startswith('#'):
                continue

            if pattern.startswith("*."):
                # Паттерн типа *.txt
                self.suffix_patterns.append(pattern[1:])
            elif pattern.endswith("/"):
                # Директория
                self.dir_patterns.append(pattern.rstrip('/'))
            elif '*' in pattern or '?' in pattern:
                # Glob-паттерн
                self.glob_patterns.append(pattern)
            else:
                # Точное совпадение
                self.exact_patterns.append(pattern)

    def load_gitignore(self, gitignore_path: Path):
        """Загружает паттерны из .gitignore файла."""
        try:
            with open(gitignore_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    self.compile_patterns({line})
        except Exception as e:
            print(f"# [Предупреждение: не удалось прочитать .gitignore: {e}]", file=sys.stderr)

    def should_ignore(self, rel_path: Path) -> bool:
        rel_str = str(rel_path).replace("\\", "/")
        rel_name = rel_path.name
        rel_suffix = rel_path.suffix.lower()

        # Проверка по суффиксам (*.txt, *.log и т.д.)
        for suffix in self.suffix_patterns:
            if rel_name.endswith(suffix):
                return True

        # Проверка по директориям
        for dir_pat in self.dir_patterns:
            if rel_str == dir_pat or rel_str.startswith(dir_pat + "/"):
                return True
            if rel_name == dir_pat:
                return True

        # Проверка по точным совпадениям
        for exact in self.exact_patterns:
            if rel_str == exact or rel_name == exact:
                return True

        # Проверка по glob-паттернам
        for glob_pat in self.glob_patterns:
            if fnmatch.fnmatch(rel_name, glob_pat):
                return True
            if fnmatch.fnmatch(rel_str, glob_pat):
                return True

        # Дополнительная проверка для debug-файлов
        if 'debug' in rel_name.lower():
            return True

        # Проверка для *.txt файлов (кроме исключений)
        if rel_suffix == '.txt':
            if rel_name not in ['requirements.txt', 'LICENSE.txt']:
                return True

        return False

    def is_binary(self, path: Path) -> bool:
        """Проверяет, является ли файл бинарным."""
        if path.suffix.lower() in BINARY_EXTS:
            return True

        # Дополнительная проверка по содержимому (первые 8192 байта)
        try:
            with open(path, 'rb') as f:
                chunk = f.read(8192)
                if b'\x00' in chunk:
                    return True
        except Exception:
            pass

        return False


def walk_tree(
        dir_path: Path,
        ignore_checker: IgnoreChecker,
        root: Path,
        prefix: str = "",
) -> List[str]:
    """Рекурсивно обходит дерево и возвращает список строк с его структурой."""
    lines = []
    try:
        entries = []
        for p in dir_path.iterdir():
            try:
                rel_path = p.relative_to(root)
            except ValueError:
                rel_path = Path(p.name)

            if not ignore_checker.should_ignore(rel_path):
                entries.append(p)

        entries.sort(key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        lines.append(prefix + "└── [не доступно]")
        return lines
    except OSError as e:
        lines.append(prefix + f"└── [ошибка: {e}]")
        return lines

    if not entries:
        return lines

    pointers = ["├── "] * (len(entries) - 1) + ["└── "]
    for pointer, path in zip(pointers, entries):
        lines.append(prefix + pointer + path.name)
        if path.is_dir():
            extension = "│   " if pointer == "├── " else "    "
            lines.extend(walk_tree(path, ignore_checker, root, prefix + extension))

    return lines


def dump_all_contents(
        dir_path: Path,
        ignore_checker: IgnoreChecker,
        root: Path,
):
    """Выводит содержимое всех файлов (без дерева) в stdout."""
    try:
        entries = []
        for p in dir_path.iterdir():
            try:
                rel_path = p.relative_to(root)
            except ValueError:
                rel_path = Path(p.name)

            if not ignore_checker.should_ignore(rel_path):
                entries.append(p)

        entries.sort(key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return
    except OSError:
        return

    for path in entries:
        if path.is_dir():
            dump_all_contents(path, ignore_checker, root)
        elif path.is_file():
            # Пропускаем бинарные файлы
            if ignore_checker.is_binary(path):
                continue

            rel_path = path.relative_to(root)
            print(f"\n# Файл: {rel_path}")
            try:
                with path.open(encoding="utf-8", errors="replace") as f:
                    for line in f:
                        print(line.rstrip())
            except (OSError, UnicodeDecodeError) as e:
                print(f"# [Ошибка чтения: {e}]")


def main():
    parser = argparse.ArgumentParser(
        description="Выводит дерево проекта и (опционально) содержимое всех файлов",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Корневая директория (по умолчанию: текущая)",
    )
    parser.add_argument(
        "--full-content",
        action="store_true",
        help="Показать содержимое всех файлов после дерева (без ограничений)",
    )
    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Не загружать паттерны из .gitignore",
    )

    args = parser.parse_args()
    root_path = Path(args.path).resolve()

    if not root_path.exists():
        print(f"Ошибка: путь '{root_path}' не существует.", file=sys.stderr)
        sys.exit(1)

    if not root_path.is_dir():
        print(f"Ошибка: путь '{root_path}' не является директорией.", file=sys.stderr)
        sys.exit(1)

    # Загружаем .gitignore если он существует
    gitignore_path = root_path / ".gitignore" if not args.no_gitignore else None

    ignore_checker = IgnoreChecker(IGNORE_LIST, gitignore_path)

    try:
        # Выводим структуру проекта
        print("СТРУКТУРА ПРОЕКТА:")
        print(root_path.name + "/")
        tree_lines = walk_tree(root_path, ignore_checker, root_path)
        for line in tree_lines:
            print(line)

        # Если запрошено полное содержимое
        if args.full_content:
            print("\n" + "=" * 60)
            print("СОДЕРЖИМОЕ ФАЙЛОВ:")
            print("=" * 60)
            dump_all_contents(root_path, ignore_checker, root_path)

    except OSError as e:
        print(f"Ошибка ввода/вывода: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[Прервано пользователем]", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()