#!/bin/bash
# install.sh — установка навыка «Рамева-Бухгалтер» в Hermes (WSL2 / Linux)
# Запуск: bash install.sh

set -e

SKILL_DIR="$HOME/.hermes/skills/rameva"
SCRIPTS_DIR="$SKILL_DIR/scripts"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "========================================"
echo "  Установка навыка: Рамева-Бухгалтер"
echo "========================================"
echo ""

# 1. Создать структуру директорий
echo "→ Создаю директории..."
mkdir -p "$SCRIPTS_DIR"

# 2. Копировать файлы
echo "→ Копирую файлы навыка..."
cp "$SOURCE_DIR/SKILL.md"    "$SKILL_DIR/"
cp "$SOURCE_DIR/config.yaml" "$SKILL_DIR/"
cp "$SOURCE_DIR/init_db.py"     "$SCRIPTS_DIR/"
cp "$SOURCE_DIR/parse_bank.py"  "$SCRIPTS_DIR/"
cp "$SOURCE_DIR/classify.py"    "$SCRIPTS_DIR/"
cp "$SOURCE_DIR/tax_calc.py"    "$SCRIPTS_DIR/"
cp "$SOURCE_DIR/reports.py"     "$SCRIPTS_DIR/"

# 3. Инициализировать БД
echo "→ Инициализирую базу данных..."
python3 "$SCRIPTS_DIR/init_db.py" --db "$SKILL_DIR/rameva.db"

# 4. Проверка Python
echo ""
echo "→ Проверка зависимостей Python..."
python3 -c "import sqlite3, pathlib, datetime, argparse, hashlib, calendar; print('  ✅ Все стандартные модули доступны')"

# 5. Итог
echo ""
echo "========================================"
echo "  ✅ Навык установлен!"
echo ""
echo "  Структура:"
echo "  $SKILL_DIR/"
echo "  ├── SKILL.md"
echo "  ├── config.yaml"
echo "  ├── rameva.db"
echo "  └── scripts/"
echo "      ├── init_db.py"
echo "      ├── parse_bank.py"
echo "      ├── classify.py"
echo "      ├── tax_calc.py"
echo "      └── reports.py"
echo ""
echo "  Следующий шаг:"
echo "  1. Отредактируй config.yaml (добавь gross salary, Диадок org_id)"
echo "  2. Загрузи первую выписку банка:"
echo "     python3 $SCRIPTS_DIR/parse_bank.py выписка.txt --db $SKILL_DIR/rameva.db"
echo "========================================"
