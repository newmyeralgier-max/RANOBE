# Проект Ranobe — Скачивание и перевод ранобэ

## Суть проекта
Автоматизированный пайплайн: скачать ранобэ с сайта → перевести через LLM API → собрать в EPUB.

## Ключевая задумка
- Пользователь даёт ссылку на книгу (freewebnovel.com, ranobes.com и др.)
- Скрипт скачивает все главы (или диапазон глав)
- Текст переводится через Fireworks API на русский
- Результат собирается в EPUB для читалки

## Быстрый старт

### Универсальный скачивальщик (рекомендуется)

Новый CLI `execution/download_novel.py` принимает ссылку на книгу, показывает
список глав и даёт выбрать, что скачать. Работает для ranobes.net, ranobes.com
и freewebnovel.com; архитектура пакета `execution/novel_dl/` расширяется
новыми адаптерами.

```bash
# Интерактивный режим: показывает список глав и спрашивает диапазон
python3 execution/download_novel.py \
  "https://ranobes.net/novels/1206834-horror-game-developer.html"

# Только диапазон, без промпта
python3 execution/download_novel.py \
  "https://ranobes.net/novels/1206834-horror-game-developer.html" \
  --range 1-50 -o downloads

# Весь том сразу + единый combined.txt для локального переводчика
python3 execution/download_novel.py \
  "https://ranobes.net/novels/1206834-horror-game-developer.html" \
  --all --combined -o downloads

# Только распечатать список глав
python3 execution/download_novel.py URL --list
```

Синтаксис диапазона: `1-50`, `500-`, `-100`, `1,5,10-20`, `all`.

Вывод:
```
downloads/<slug>/
  meta.json
  chapter_0001_<title>.txt    # "# Заголовок\n\nТекст главы..."
  chapter_0002_<title>.txt
  combined.txt                # опционально (--combined)
```

Формат файла главы — чистый текст с заголовком первой строкой (`# Название`),
что удобно для перевода локальной LLM.

### Старый пайплайн (скачать + перевести + EPUB через Fireworks)

```bash
# Скачать + перевести + EPUB (главы 500-607)
python3 execution/ranobe_pipeline.py \
  --url "https://freewebnovel.com/horror-game-developer-my-games-arent-that-scary.html" \
  --start 500 --end 607 --output epub

# Скачать полный том
python3 execution/ranobe_pipeline.py \
  --url "https://freewebnovel.com/horror-game-developer-my-games-arent-that-scary.html" \
  --start 1 --end 607 --output epub
```

## Поддерживаемые сайты
| Сайт | URL формат | Статус |
|------|-----------|--------|
| freewebnovel.com | `https://freewebnovel.com/SLUG.html` | Работает |
| ranobes.com | `https://ranobes.com/ranobe/SLUG.html` | Есть скрипт (ranobes_dl.py) |
| ranobes.net | `https://ranobes.net/novels/<id>-<slug>.html` | Работает (`download_novel.py` + `ranobes_tool.py`) |

## Конкретная задача
**Книга:** Horror Game Developer (My Games Aren't That Scary)
**Сайт:** FreeWebNovel.com (607 глав, без авторизации)
**Оригинал:** английский
**Перевод:** русский через Fireworks API (llama-v3p3-70b-instruct)
**Формат:** EPUB

## Скрипты
- `execution/download_novel.py` + `execution/novel_dl/` — УНИВЕРСАЛЬНЫЙ скачивальщик (ranobes.net/.com, freewebnovel.com). Только текст, без перевода
- `execution/ranobe_pipeline.py` — полный пайплайн: скачать + перевести (Fireworks) + EPUB (freewebnovel.com)
- `execution/ranobes_dl.py` — старый парсер для ranobes.com
- `execution/ranobes_tool.py` — старый парсер для ranobes.net

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
