# Технический паспорт навыка: kontur-extern

**Версия:** 2.0.0  
**Статус:** Только чтение (мониторинг и оповещение)  
**Последнее обновление:** апрель 2026  
**Документация API:** https://developer.kontur.ru/Docs/extern-api/index.html

---

## 1. Назначение

Навык обеспечивает Nanobot-агенту доступ к данным Контур.Экстерна через REST API. Агент выступает помощником руководителя, который самостоятельно ведёт бухгалтерию, — следит за своевременностью отчётности, предупреждает о дедлайнах и требованиях контролирующих органов.

**Навык является только читающим.** Отправка отчётности, ответы на требования и любые изменяющие операции не реализованы.

---

## 2. Что умеет навык

| Функция | Модуль | Описание |
|---------|--------|----------|
| Таблица отчётности | extern_monitor | Формы с дедлайнами и статусами за квартал/год |
| Ближайшие дедлайны | extern_monitor | Формы с дедлайном в ближайшие N дней |
| Просроченные отчёты | extern_monitor | Формы с прошедшим дедлайном и статусом не «принят» |
| Требования ФНС | extern_monitor | Входящие требования без ответа (срок 6 р.дней) |
| Сводка по периоду | extern_monitor | Статистика: принято / отклонено / не отправлено |
| ДО требующие действий | extern_monitor | Документообороты с ссылками типа 'reply' |
| Незавершённые черновики | extern_monitor | Черновики, не дошедшие до отправки |
| Лента событий | extern_monitor | Инкрементальное получение изменений в ДО |
| Сертификаты КЭП | extern_monitor | Сроки действия подписей |
| ИОН / сверка с ФНС | extern_monitor | Статус ранее отправленного запроса сверки |
| Проактивные оповещения | extern_alerts | Heartbeat с классификацией по уровням critical/warning |

---

## 3. Структура файлов

```
~/.nanobot/workspace/skills/kontur-extern/
    SKILL.md              # инструкции для агента
    TECHSPEC.md           # этот файл
    extern_api.py         # клиент API: аутентификация, HTTP, управление площадкой
    extern_monitor.py     # сценарии мониторинга
    extern_alerts.py      # проактивные оповещения для heartbeat
    extern.json           # токены и настройки (⚠ НЕ копировать в GitHub)
```

### extern.json — структура v2

```json
{
  "test_mode": true,
  "credentials": {
    "api_key":   "...",
    "client_id": "..."
  },
  "tokens": {
    "access_token":  "...",
    "refresh_token": "...",
    "expires_at":    "2026-04-05T12:00:00+00:00"
  },
  "account": {
    "account_id": "...",
    "inn":        "...",
    "kpp":        "...",
    "org_name":   "..."
  },
  "settings": {
    "deadline_warning_days": 5,
    "demand_critical_days":  2,
    "ens_alert_on_negative": true
  }
}
```

**Изменения по сравнению с v1:**
- Убраны `base_url` и `identity_url` из `settings` — теперь определяются по `test_mode`
- Добавлен флаг `test_mode: true/false` на верхнем уровне
- Убран лишний `client_secret` (он совпадает с `api_key`)

> **ВНИМАНИЕ:** `extern.json` содержит секреты. Навык `backup` автоматически обнаружит и исключит этот файл из GitHub-выгрузки.

---

## 4. Аутентификация

### Площадки

| `test_mode` | API | Identity |
|---|---|---|
| `true` | https://extern-api.testkontur.ru | https://identity.testkontur.ru |
| `false` | https://extern-api.kontur.ru | https://identity.kontur.ru |

### Первичная авторизация (Device Flow)

Документация: https://developer.kontur.ru/Docs/extern-api/auth_oidc/device_flow.html

1. `POST /connect/deviceauthorization` с `client_id`, `client_secret=api_key`, `scope=extern.api`
2. Получаем `verification_uri_complete`, `device_code`, `interval`, `expires_in`
3. Показываем пользователю ссылку для открытия в браузере
4. Параллельно опрашиваем `POST /connect/token` с `grant_type=urn:ietf:params:oauth:grant-type:device_code`
5. После подтверждения получаем `access_token` + `refresh_token`, сохраняем в `extern.json`

### Обновление токена (автоматически)

```
POST /connect/token
grant_type=refresh_token
client_id=...&client_secret=...&refresh_token=...
```

`KonturExternClient.ensure_token()` проверяет `expires_at` перед каждым запросом. Если истёк или истекает через менее 5 минут — обновляет автоматически.

### Заголовки каждого запроса

```
Authorization:   Bearer <access_token>
X-Kontur-Apikey: <api_key>
Content-Type:    application/json
```

---

## 5. Эндпоинты API

Swagger-документация (все методы, интерактивные примеры):
- Учётные записи и организации: https://developer.kontur.ru/doc/extern
- Черновики: https://developer.kontur.ru/doc/extern.drafts
- Документообороты и лента событий: https://developer.kontur.ru/doc/extern.docflows
- Сервис контентов: https://developer.kontur.ru/doc/extern.contents
- Тестовые инструменты: https://developer.kontur.ru/doc/extern.test.tools

Базовый URL: `{base_url}/v1/{account_id}/`

### Реализованные методы

| Метод | Эндпоинт | Назначение |
|-------|----------|-----------|
| GET | `/v1/accounts` | Список учётных записей |
| GET | `/v1/accounts/{id}` | Детали учётной записи |
| GET | `/v1/{acc}/organizations` | Список организаций |
| GET | `/v1/{acc}/organizations/{id}` | Детали организации |
| GET | `/v1/{acc}/forms` | Справочник форм отчётности |
| POST | `/v1/{acc}/forms/search` | Таблица отчётности с дедлайнами |
| GET | `/v1/{acc}/docflows` | Список ДО (с фильтрами + пагинация) |
| GET | `/v1/{acc}/docflows/{id}` | Детали ДО |
| GET | `/v1/{acc}/docflows/{id}/documents` | Документы в ДО |
| GET | `/v1/{acc}/docflows/{id}/documents/{doc_id}` | Детали документа |
| GET | `/v1/{acc}/events` | Лента событий ДО |
| GET | `/v1/{acc}/drafts` | Список черновиков |
| GET | `/v1/{acc}/drafts/{id}` | Детали черновика |
| GET | `/v1/{acc}/certificates` | Сертификаты КЭП |
| GET | `/v1/{acc}/warrants` | Машиночитаемые доверенности (МЧД) |

### Типы документооборотов

| Тип (без urn:docflow:) | Описание |
|---|---|
| `fns534-report` | Отчётность в ФНС |
| `fns534-demand` | Входящее требование ФНС |
| `fns534-ion` | Запрос сверки с ФНС (ИОН) |
| `fns-letter` | Письмо в ФНС |
| `sfr-report` | Отчётность в СФР |
| `stat-report` | Отчётность в Росстат |
| `stat-letter` | Письмо в Росстат |
| `mvd-notification` | Уведомление МВД |

### Фильтры GET /v1/{acc}/docflows

```
type=urn:docflow:fns534-demand    # по типу ДО
incoming=true                     # только входящие
finished=false                    # только незавершённые
updatedFrom=2026-01-01T00:00:00   # обновлены с даты
updatedTo=2026-04-01T00:00:00     # обновлены до даты
take=100                          # размер страницы (max 100)
skip=0                            # смещение
```

---

## 6. Описание модулей

### extern_api.py — клиент API

**Класс `KonturExternClient`**

| Метод | Назначение |
|-------|-----------|
| `ensure_token()` | Проверить/обновить access_token перед запросом |
| `device_flow_login()` | Первичная авторизация через браузер |
| `get(path, params)` | HTTP GET к API |
| `post(path, body)` | HTTP POST к API |
| `get_accounts()` | Список учётных записей |
| `get_account(id)` | Детали учётной записи |
| `get_organizations()` | Список организаций |
| `get_forms()` | Справочник форм |
| `get_forms_table(year, quarter)` | Таблица отчётности с дедлайнами |
| `get_docflows(**filters)` | Одна страница ДО |
| `get_all_docflows(**filters)` | Все ДО с обходом пагинации |
| `get_docflow(id)` | Детали ДО |
| `get_docflow_documents(id)` | Документы в ДО |
| `get_events(take, seq_id)` | Лента событий |
| `get_certificates()` | Список сертификатов КЭП |
| `get_warrants()` | Список доверенностей МЧД |
| `get_drafts()` | Список черновиков |
| `test_generate_fns_demand(...)` | Создать тестовое требование ФНС |
| `test_generate_fns_finish(id)` | Имитировать завершение ДО роботом |

**CLI команды:**

```bash
python3 extern_api.py status       # статус токена и режима
python3 extern_api.py login        # первичная авторизация
python3 extern_api.py accounts     # список учётных записей
python3 extern_api.py switch-test  # переключить на тест (сбросить токены)
python3 extern_api.py switch-prod  # переключить на прод (сбросить токены)
```

### extern_monitor.py — сценарии мониторинга

Каждая функция делает минимальное число запросов к API (1 или несколько страниц пагинации) и возвращает структурированный список.

| Функция | API-запросов | Что возвращает |
|---------|-------------|----------------|
| `check_reporting_deadlines(client, days_ahead)` | 1 | Формы с дедлайном в N дней |
| `check_overdue_reports(client)` | 1 | Просроченные формы |
| `check_pending_demands(client)` | N (пагинация) | Требования ФНС без ответа |
| `get_accepted_reports_summary(client, year, q)` | 1 | Статистика по периоду |
| `get_docflows_requiring_action(client)` | N (пагинация) | ДО с ссылками reply |
| `check_open_drafts(client)` | 1 | Незавершённые черновики |
| `get_new_events(client, seq_id)` | 1 | Новые события (лента) |
| `get_certificates_info(client)` | 1 | Сертификаты КЭП |
| `poll_ion_result(client, docflow_id)` | 1 | Статус ИОН-запроса |

### extern_alerts.py — проактивный мониторинг

`run_alerts(client)` — запускает все проверки в одном вызове, возвращает отсортированный список алертов. Порядок: overdue → demands → deadlines → action → certs.

Структура алерта:
```python
{
    "level":   "critical" | "warning" | "info",
    "type":    "overdue" | "demand" | "deadline" | "action" | "cert" | "system",
    "message": "Строка на русском языке",
    "details": {...}
}
```

---

## 7. Обработка ошибок

| HTTP | Причина | Действие скрипта |
|------|---------|-----------------|
| 200/201 | Успех | Вернуть JSON |
| 204 | Успех, пустой ответ | Вернуть {} |
| 401 | Токен истёк | Автообновление через refresh_token |
| 403 | Нет доступа | RuntimeError с деталями |
| 404 | Ресурс не найден | RuntimeError с деталями |
| 429 | Лимит запросов | Пауза 60 сек, RuntimeError |
| 5xx | Ошибка сервера | Пауза 30 сек, RuntimeError |

Все ошибки выводятся с префиксом `[KONTUR ERROR]`. Агент читает stdout и сообщает пользователю.

---

## 8. Особенности

- **Пагинация `get_all_docflows`:** первый запрос возвращает `total-count`, скрипт заранее вычисляет число страниц и делает ровно столько запросов, сколько нужно.
- **Лента событий:** хранить `last_event_id` в MEMORY.md для инкрементальных проверок вместо полного перебора ДО.
- **Срок токена:** `access_token` живёт ~1 час, `refresh_token` — дни/недели. При смене площадки токены несовместимы — нужна повторная авторизация.
- **Праздники:** рабочие дни считаются без учёта праздников. В критичные периоды учитывать вручную.
- **ИОН-запросы:** создание нового ИОН требует XML-документа с КЭП. Не реализовано. Статус существующего ИОН-ДО — через `poll_ion_result()`.

---

## 9. Ограничения API

- ИОН-запросы: ФНС отвечает до 5 рабочих дней
- Справка об исполнении обязанностей: до 10 рабочих дней
- Пагинация ДО: максимум 100 за запрос (параметр `take`)
- ФНС не принимает с 01.01.2023: Форма N39-1 (состояние расчётов), Выписка операций

---

## 10. Зависимости

```
requests>=2.31.0      # HTTP-клиент
python-dateutil>=2.8  # парсинг дат из ответов API
```

Стандартные библиотеки: `json`, `pathlib`, `datetime`, `time`, `sys`.

Установка:
```
pip install requests python-dateutil
```

---

## 11. Связанные навыки

| Навык | Связь |
|-------|-------|
| `backup` | Исключает `extern.json` как файл с секретами |

---

## 12. История изменений

### v2.0.0 (апрель 2026)
- Добавлен `test_mode` флаг — переключение площадок без ручного редактирования URL
- Команды `switch-test` / `switch-prod` с автоматическим сбросом токенов
- Методы: `get_organizations`, `get_docflow_documents`, `get_events`, `get_certificates`, `get_warrants`, `get_drafts`
- Мониторинг сертификатов КЭП (истёк / истекает через ≤ 30 дней)
- Лента событий для инкрементального мониторинга
- `get_all_docflows` использует `total-count` для минимизации запросов
- Убран лишний `client_secret` (дублировал `api_key`)
- Исправлены ссылки на документацию API
- Добавлены ссылки на Swagger для всех групп методов

### v1.0.0 (начало 2026)
- Первая версия: deadlines, overdue, demands, summary, action, alerts
