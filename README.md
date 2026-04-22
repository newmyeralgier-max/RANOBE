# Проект Ranobe — Скачивание и перевод ранобэ

## Суть проекта
Автоматизированный пайплайн: скачать ранобэ с сайта → перевести через LLM API → собрать в EPUB.

## Ключевая задумка
- Пользователь даёт ссылку на книгу (freewebnovel.com, ranobes.com и др.)
- Скрипт скачивает все главы (или диапазон глав)
- Текст переводится через Fireworks API на русский
- Результат собирается в EPUB для читалки

## Быстрый старт

```bash
cd "D:\1. Project\Ranobe\execution"

# Скачать + перевести + EPUB (главы 500-607)
python3 ranobe_pipeline.py \
  --url "https://freewebnovel.com/horror-game-developer-my-games-arent-that-scary.html" \
  --start 500 --end 607 --output epub

# Скачать полный том
python3 ranobe_pipeline.py \
  --url "https://freewebnovel.com/horror-game-developer-my-games-arent-that-scary.html" \
  --start 1 --end 607 --output epub
```

## Поддерживаемые сайты
| Сайт | URL формат | Статус |
|------|-----------|--------|
| freewebnovel.com | `https://freewebnovel.com/SLUG.html` | Работает |
| ranobes.com | `https://ranobes.com/ranobe/SLUG.html` | Есть скрипт (ranobes_dl.py) |
| ranobes.net | `https://ranobes.net/...` | Есть скрипт (ranobes_tool.py) |

## Конкретная задача
**Книга:** Horror Game Developer (My Games Aren't That Scary)
**Сайт:** FreeWebNovel.com (607 глав, без авторизации)
**Оригинал:** английский
**Перевод:** русский через Fireworks API (llama-v3p3-70b-instruct)
**Формат:** EPUB

## Скрипты
- `execution/ranobe_pipeline.py` — ОСНОВНОЙ. Единый пайплайн: скачать + перевести + EPUB
- `execution/ranobes_dl.py` — парсер для ranobes.com
- `execution/ranobes_tool.py` — парсер для ranobes.net

## API ключи
В `.env`:
- FIREWORKS_API_KEY — для перевода
- TRANSLATE_MODEL — модель для перевода (по умолчанию llama-v3p3-70b-instruct)

## Архитектура (Antigravity 3-слойная)
```
Слой 1: Директивы (directives/) — Что делать
Слой 2: Оркестрация (GEMINI.md) — Как решать
Слой 3: Исполнение (execution/) — Делание
```

## Резьюме
Скрипт поддерживает продолжение после обрыва:
- `data/raw/chapter_NNNN.txt` — скачанные сырые главы
- `data/translated/chapter_NNNN.txt` — переведённые главы
- Если файл уже существует — пропуск, продолжаем дальше

## Структура проекта
```
.agent/           — Навыки для Gemini
.vscode/          — Настройки VS Code
data/raw/         — Скачанные сырые главы (JSON)
data/translated/  — Переведённые главы (JSON)
directives/       — СОПы (директивы)
execution/        — Python-скрипты
output/           — EPUB файлы
prompts/          — Промпты для перевода
.env              — API ключи
GEMINI.md         — Инструкции для Gemini
README.md         — Этот файл
```
