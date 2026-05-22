# Облачный запуск без Codex и без включенного компьютера

Финальная схема состоит из двух частей:

1. Cloud Run Job запускает Python-парсер, собирает цены и записывает результат в постоянную Google Таблицу.
2. Cloud Run Service принимает короткий запрос от Apps Script или Cloud Scheduler и запускает этот Job.

Такой вариант не зависит от Codex и не зависит от включенного компьютера.

## Что разворачивается

- HTTP-сервис: `cloud_service.main:app`
  - `GET /health` проверяет, что сервис жив.
  - `POST /run` запускает Cloud Run Job и сразу возвращает ответ.
- Задача парсера: `python -m cloud_service.run_update`
  - собирает Билайн и Мегафон обычным парсером;
  - собирает DNS и МТС через Playwright-браузер;
  - объединяет результат;
  - проверяет результат валидатором;
  - записывает строки в лист `Номенклатура`;
  - пишет дату обновления в `H1`;
  - обновляет служебный лист `ParserControl`.

## Переменные окружения

Общие:

- `SPREADSHEET_ID=151fl2XsI_gmqPXIhFA47OZ-nSQEMBbDb2JaKNXN9be0`

Для HTTP-сервиса:

- `WEBHOOK_TOKEN` - секрет для Apps Script.
- `CLOUD_RUN_JOB_NAME` - имя Cloud Run Job, например `competitors-parser-job`.
- `CLOUD_RUN_REGION` - регион, например `europe-west1`.
- `GOOGLE_CLOUD_PROJECT` - id проекта Google Cloud.

Для Job:

- `SPREADSHEET_ID`
- учетная запись Cloud Run должна иметь доступ редактора к Google Таблице.

Локально вместо Google Cloud service account можно использовать:

- `GOOGLE_SERVICE_ACCOUNT_JSON`
- или `GOOGLE_APPLICATION_CREDENTIALS`

В Cloud Run лучше использовать штатную service account и выдать ей доступ к таблице.

## Apps Script

Код для таблицы лежит в:

- `apps_script/Code.gs`
- `apps_script/appsscript.json`

В Script Properties нужно задать:

- `PARSER_WEBHOOK_URL=https://<cloud-run-service-url>/run`
- `PARSER_WEBHOOK_TOKEN=<тот же WEBHOOK_TOKEN>`

Меню `Парсер -> Запустить сейчас` запускает облачный парсер.

Меню `Парсер -> Установить ежедневный запуск 07:00` ставит серверный Apps Script trigger на 07:00 по Москве. Этот trigger тоже работает без включенного компьютера.

Скрипт `deploy_cloud_run.sh` дополнительно ставит Cloud Scheduler на `POST /run` каждый день в 07:00 по Москве. Это основной ежедневный запуск на стороне Google Cloud.

## Проверка после развертывания

1. Открыть `/health` у Cloud Run Service.
2. Нажать `Парсер -> Запустить сейчас` в Google Таблице.
3. Проверить `ParserControl`:
   - сначала `QUEUED` или `RUNNING`;
   - после завершения `DONE`.
4. Проверить `Номенклатура!A1:H5`:
   - заголовки: `Конкурент`, `Категория`, `Наименование`, `Цена`, `Цена со скидкой`, `Ссылка`, `Якорь`, дата обновления;
   - в строках ниже должны быть товары с ценами.
