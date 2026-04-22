# Директива: Сборка EPUB

## Цель
Собрать переведённые главы в EPUB-файл для читалок

## Вход
- Папка с переведёнными главами (`data/translated/`)
- Метаданные книги (название, автор, жанры, обложка)

## Выход
- EPUB файл в `output/`

## Инструмент
Библиотека `ebooklib` (Python). Установить: `pip install ebooklib`

## Шаги
1. Создать объект epub.EpubBook()
2. Установить метаданные: title, author, language='ru'
3. Добавить обложку если есть
4. Для каждой главы:
   - Создать EpubHtml с содержимым
   - Добавить в книгу и в spine
5. Сформировать оглавление (toc)
6. Добавить nav (навигация)
7. Записать в файл через epub.write_epub()

## Стиль CSS
```css
body { font-family: serif; line-height: 1.8; margin: 1em; }
h1 { font-size: 1.4em; border-bottom: 1px solid #ccc; margin-top: 2em; }
p { text-indent: 1.5em; margin: 0.5em 0; text-align: justify; }
```

## Граничные случаи
- Более 200 глав → разбить на тома
- Главы без перевода → вставить оригинал с пометкой
- Спецсимволы в названии → экранировать HTML
