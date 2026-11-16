# FoodSource

FoodSource помогает найти поставщиков продуктов, ингредиентов и упаковки по товару и городу. Можно сравнить до трёх компаний, открыть контакты и источники, а после звонка записать цену, минимальный заказ и заметку. Записи сохраняются в браузере.

## Запуск

Из корня проекта. Нужны Node.js 22.12+ и uv.

```sh
npm ci --prefix frontend
npm run build --prefix frontend
uv --directory backend run --locked --group ingest python -m foodsource.ingest
uv --directory backend run --locked --no-sync uvicorn main:app --host 127.0.0.1 --port 8765
```

Откройте [каталог](http://localhost:8765/). Для примера выберите Екатеринбург и введите `коробки для пиццы`.

Scrapy собирает данные из [выгрузки Росаккредитации](https://fsa.gov.ru/opendata/7736638268-rds/), каталогов выставок и [региональных каталогов](https://primexport.ru/ru/katalog). Сбор скачивает архивы и занимает несколько минут. По одному проверенному ИНН выдача и сравнение показывают одну компанию. Фильтр по городу учитывает ассортимент конкретной площадки. Дата в заголовке относится к сбору каталога, рядом с источником указана дата наблюдения.

Для запуска без сбора с сайтов после сборки фронтенда выполните `uv run --project backend --locked --no-dev python -m scripts.demo` и откройте [учебное демо](http://localhost:8770/). Это семь вымышленных карточек. Запрос `бумажные стаканы` в Екатеринбурге даёт три карточки для сравнения.

## Публичное демо

После сборки фронтенда и импорта подготовьте отдельную папку для Vercel.

```sh
uv run --project backend --locked python scripts/stage_vercel.py --output ../foodsource-vercel
```

В ней собранный интерфейс, API и копия опубликованной SQLite для чтения. Для обновления демо соберите новый снимок и повторите публикацию.

Географические данные предоставлены [GeoNames (CC BY 4.0)](https://download.geonames.org/export/dump/readme.txt). Чтобы дополнительно проверить карту, добавьте `--group overture` перед `python` и `--overture-city Курган` в конце команды импорта. Используются данные [Overture Maps Foundation и поставщиков набора Places](https://docs.overturemaps.org/attribution/).
