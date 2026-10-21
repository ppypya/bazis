class PaymentNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class ProcessingBusy(Exception):
    """Другой обработчик ещё владеет платежом."""


class LostLease(Exception):
    """Обработчик больше не имеет права записывать результат."""


class WebhookError(Exception):
    pass


class WebhookRejected(WebhookError):
    """Адрес webhook запрещён политикой исходящих запросов."""
