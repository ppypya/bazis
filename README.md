# Асинхронный сервис процессинга платежей

Сервис принимает запросы на оплату, сохраняет платёж и событие в одной
транзакции PostgreSQL, обрабатывает оплату асинхронно и отправляет webhook.
Один consumer выполняет оплату и уведомление. Издатель outbox работает
фоновым циклом в том же процессе.

## Стек

- FastAPI и Pydantic v2.
- SQLAlchemy 2.0 в асинхронном режиме и PostgreSQL.
- RabbitMQ и FastStream.
- Alembic.
- Docker и Docker Compose.

Python 3.12+; Docker-образ использует Python 3.13. Проверенные версии
зависимостей и инструментов тестирования зафиксированы в `requirements.lock`.

## Запуск

Нужен Docker с Compose v2.

```bash
cp env.template .env
# Замените API_KEY в .env (не менее 16 символов).
docker compose up --build -d
docker compose ps
```

Миграции выполняются в контейнере `api` перед запуском HTTP-сервера.
Consumer стартует после готовности API и RabbitMQ.

| Сервис | Адрес с хоста |
|---|---|
| API | `http://localhost:18000` |
| RabbitMQ Management | `http://localhost:15673` |
| PostgreSQL | `localhost:55432` |
| AMQP | `localhost:5673` |

Порты настраиваются в `.env`. Учётные данные PostgreSQL/RabbitMQ в шаблоне:
`payments` / `payments`. Внутри Docker используются имена `postgres`,
`rabbitmq` и внутренние порты 5432/5672.

```bash
docker compose logs -f api consumer
docker compose stop
docker compose start
```

`docker compose down` удаляет контейнеры, сохраняя данные в именованных volumes.

## API

Эндпоинты платежей требуют `X-API-Key`; отсутствие или неверный ключ дают `401`.

Swagger UI доступен на [http://localhost:18000/docs](http://localhost:18000/docs),
схема OpenAPI — на `/openapi.json`. Документация открывается без ключа.
Для отправки запросов нажмите **Authorize** и введите значение `API_KEY` из `.env`.
При создании платежа также заполните обязательный заголовок `Idempotency-Key`.

### Создать платёж

Укажите значение `API_KEY` из `.env` и HTTPS-адрес вашего получателя.
По умолчанию разрешён только порт 443 и публичные IP-адреса назначения.

```bash
curl -i http://localhost:18000/api/v1/payments \
  -H 'X-API-Key: local-development-api-key' \
  -H 'Idempotency-Key: order-42' \
  -H 'Content-Type: application/json' \
  -d '{
    "amount": "1250.50",
    "currency": "RUB",
    "description": "Оплата заказа 42",
    "metadata": {"order_id": 42},
    "webhook_url": "https://merchant.example/webhook"
  }'
```

Ответ `202 Accepted`:

```json
{
  "payment_id": "37d97648-cc9b-4aee-a065-339bc869c4d0",
  "status": "pending",
  "created_at": "2026-10-20T12:00:00Z"
}
```

`Location` содержит путь получения платежа. Сумма положительная, до 16 цифр
в целой части и двух после запятой; в БД — `NUMERIC(18,2)`, в Python —
`Decimal`, в ответах — строка. Валюты: `RUB`, `USD`, `EUR`.
Описание ограничено 1000 символами, метаданные — JSON-объектом до 16 KiB.

`Idempotency-Key` обязателен, от 1 до 255 символов без пробельных символов.
Одинаковый ключ с теми же нормализованными параметрами возвращает `202`
с исходными ID и датой создания и текущим статусом платежа.
Изменение параметров при том же ключе даёт `409`.
Уникальное ограничение БД защищает от гонки конкурентных запросов.

### Получить платёж

```bash
curl http://localhost:18000/api/v1/payments/PAYMENT_ID \
  -H 'X-API-Key: local-development-api-key'
```

Ответ содержит поля платежа, `processed_at`, `webhook_delivered_at`,
`attempts`, `last_error` и `dead_lettered_at`. Неизвестный ID даёт `404`,
невалидные данные — `422`. `processed_at` устанавливается для обоих
финальных статусов: `succeeded` и `failed`.

### Webhook

Consumer отправляет `POST` с JSON:

```json
{
  "event_id": "37d97648-cc9b-4aee-a065-339bc869c4d0",
  "event": "payment.completed",
  "payment_id": "37d97648-cc9b-4aee-a065-339bc869c4d0",
  "status": "succeeded",
  "amount": "1250.50",
  "currency": "RUB",
  "description": "Оплата заказа 42",
  "metadata": {"order_id": 42},
  "created_at": "2026-10-20T12:00:00+00:00",
  "processed_at": "2026-10-20T12:00:04+00:00"
}
```

`X-Webhook-Id` и `event_id` равны ID платежа и сохраняются при повторах.
Получатель должен дедуплицировать события по этому идентификатору.
Любой `2xx` означает успешную доставку. Таймауты, сетевые ошибки и остальные
HTTP-статусы приводят к retry; перенаправления не выполняются.
API-ключ сервиса получателю webhook не передаётся.

## Обработка, retry и DLQ

Эмулятор ждёт 2–5 секунд без блокировки event loop. Для случайных UUID
примерно 90% платежей успешны, 10% получают `failed`. Результат вычисляется
по хешу ID: повторное обращение и перезапуск дают тот же результат.
Это бизнес-результат, поэтому `failed` также отправляется клиенту webhook.

Всего три попытки: первая сразу, вторая через 5 секунд после первой ошибки,
третья через 10 секунд после второй. На итоговое время влияет очередь.
`RETRY_DELAY_SECONDS` меняет базовую задержку.

| Exchange | Очередь | Назначение |
|---|---|---|
| `payments.events` | `payments.new` | Единственная подписка consumer |
| `payments.events` | `payments.retry.1` | TTL 5 секунд, затем возврат в `payments.new` |
| `payments.events` | `payments.retry.2` | TTL 10 секунд, затем возврат в `payments.new` |
| `payments.dead` | `payments.dlq` | Исчерпанные попытки и некорректные сообщения |

Очереди quorum/durable, сообщения persistent. Для переходов через DLX
используется `at-least-once` dead-lettering с `reject-publish`.
Брокерный счётчик redelivery отделён от трёх прикладных попыток.

Результат оплаты фиксируется **до** отправки webhook. При повторе финальный
платёж пропускает шлюз и повторяет только уведомление.
Сбой webhook не меняет `succeeded` на `failed`.

После третьей технической ошибки устанавливается `dead_lettered_at`, а
сообщение для DLQ записывается в outbox в той же транзакции. Если шлюз ни разу
не вернул результат, платёж завершается как `failed`; если результат уже
сохранён, он остаётся прежним. `dead_lettered_at` означает постановку в DLQ
через outbox, фактическая публикация может произойти позже.
Автоматического повторного запуска исчерпанных платежей нет.

## Гарантии и восстановление

- Создание платежа и первого outbox-события атомарно.
- ACK выполняется после записи результата или задания продолжения в БД.
- Retry/DLQ также публикуются через outbox: сбой брокера не теряет продолжение.
- Издатель использует короткий `FOR UPDATE SKIP LOCKED`, аренду записи и
  publisher confirms с `mandatory` и обработкой unroutable-сообщений.
- Публикация выполняется вне транзакции БД. После подтверждения брокера
  outbox отмечается опубликованным. Потеря подтверждения допускает дубль.
- Ошибки публикации повторяются неограниченно с задержкой до 60 секунд;
  лимит в три попытки относится к consumer.
- Аренда платежа защищает от конкурентной обработки. После падения процесса
  сообщение возвращается брокером и продолжится после истечения аренды
  (по умолчанию 90 секунд). Для outbox аренда — 30 секунд.
- Счётчик завершённых попыток хранится в БД. Повтор старой попытки не создаёт
  ещё один retry. Падение до фиксации попытки может повторить её.
- Если PostgreSQL недоступен и результат нельзя надёжно сохранить,
  сообщение возвращается в очередь; прикладной счётчик не увеличивается.
- Если клиент принял webhook, но ответ или запись результата потеряны,
  webhook будет отправлен повторно с тем же `event_id`.

Это доставка **at least once** при сохранности БД и брокера. Compose использует
один узел RabbitMQ и не обеспечивает отказоустойчивость при потере его диска.
Эмулятор не выполняет реальных списаний. Реальный шлюз потребует
идемпотентного API и сверки неопределённых результатов.

Webhook URL задаёт авторизованный клиент. По умолчанию принимаются только
HTTPS-адреса на порту 443 без credentials и fragment. Недопустимые URL и
запрещённые IP, указанные напрямую, отклоняются API с `422`.

Перед каждой отправкой consumer разрешает имя в IPv4/IPv6 и проверяет все
полученные адреса. Смешанный ответ с публичным и внутренним IP отклоняется
целиком. Loopback, private, link-local, multicast, unspecified, reserved,
cloud infrastructure и IPv6 transition-адреса блокируются; IPv4-mapped IPv6
проверяются по вложенному IPv4. Ошибка DNS также запрещает отправку.

HTTP-транспорт подключается к проверенному числовому IP, сохраняя исходный
`Host` и имя для TLS SNI и проверки сертификата. Повторного разрешения домена
между проверкой и соединением нет. Для каждой попытки используется отдельный
транспорт, чтобы соединение к одному IP не переиспользовалось другим доменом.
Редиректы отключены; тело ответа webhook не загружается. Ошибки доставки и
запрет назначения обрабатываются существующим retry/DLQ-механизмом.

Для локального HTTP-получателя явно задайте `WEBHOOK_ALLOW_LOCAL=true` в `.env`
и пересоздайте `api` и `consumer`. Этот режим разрешает HTTP, произвольные
порты, loopback, RFC1918 и IPv6 ULA. Link-local, известные cloud metadata и
остальные специальные адреса остаются запрещены. Используйте этот режим
только при разработке. `localhost` обозначает сам consumer;
для сервера на хосте Docker Desktop используйте `host.docker.internal`.

Для рабочего развёртывания дополнительно ограничьте исходящий webhook-трафик
сетевой политикой/egress proxy: маршруты инфраструктуры могут отличаться от
стандартных диапазонов. Сетевые правила хоста этот репозиторий не настраивает.

## Тестирование

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest tests/unit -q
.venv/bin/ruff check src migrations tests scripts
.venv/bin/mypy
```

mypy проверяет `src/` и `migrations/` в строгом режиме (`strict = true`).
Настройки находятся в `pyproject.toml`; подключён плагин Pydantic для моделей
и настроек приложения. Тесты и вспомогательные скрипты в эту проверку не входят.

Интеграционные тесты используют PostgreSQL и RabbitMQ из Compose.
Каждый тест БД создаёт отдельную временную схему; тест брокера использует
уникальные имена очередей/обменников и удаляет их после проверки.

```bash
TEST_DATABASE_URL=postgresql+asyncpg://payments:payments@localhost:55432/payments \
TEST_RABBITMQ_URL=amqp://payments:payments@localhost:5673/ \
.venv/bin/pytest -q
```

Без этих переменных интеграционные тесты пропускаются. Проверяются гонки
идемпотентности, rollback payment/outbox, повтор после потери confirm,
конкурентная обработка, TTL, DLQ и unroutable-публикации.

Полная проверка запущенного стека с временным HTTP-получателем требует
локального режима в обоих процессах:

```bash
WEBHOOK_ALLOW_LOCAL=true docker compose up -d api consumer
docker compose exec -T api python scripts/smoke.py
WEBHOOK_ALLOW_LOCAL=false docker compose up -d api consumer
```

Скрипт создаёт три тестовых платежа: обычная доставка, две ошибки webhook
с последующим успехом и три ошибки с переходом в DLQ. Проверяются неизменность
payload и задержки. Тестовые платежи и DLQ-сообщение остаются для просмотра.
Получатель работает внутри `api` на порту 9009 и завершается по окончании.

Проверить соответствие миграций моделям:

```bash
docker compose exec api alembic check
```

## Структура

```text
src/
├── domain/
│   ├── entities/
│   ├── value_objects/
│   ├── services/
│   └── exceptions.py
├── application/
│   ├── use_cases/
│   ├── dtos/
│   ├── interfaces/
│   └── exceptions.py
├── presentation/
│   ├── api/
│   ├── cli/
│   ├── consumers/
│   └── jobs/
├── infrastructures/
│   ├── db/
│   ├── broker/
│   ├── mappers/
│   ├── external_apis/
│   └── webhooks/
└── config/
    ├── ioc/
    ├── app.py
    ├── database.py
    ├── external_apis.py
    ├── broker.py
    ├── cors.py
    └── settings.py
tests/
├── unit/
└── integration/
migrations/
└── versions/
docs/
└── architecture.md
scripts/
```

`config/api.py` и `config/worker.py` создают приложения;
`config/ioc/container.py` содержит общую сборку зависимостей.

`src` — корень исходников. Пакеты импортируются как `domain`, `application`
и т. д., без префикса `src`.

Правила зависимостей, сборка приложения и границы транзакций описаны
в [документации архитектуры](docs/architecture.md).
