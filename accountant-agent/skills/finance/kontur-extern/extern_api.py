#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extern_api.py — HTTP-клиент API Контур.Экстерн.

Обеспечивает:
- Первичную авторизацию через Device Flow (один раз, требует браузер)
- Автоматическое обновление access_token через refresh_token
- Хранение токенов и настроек в extern.json
- Переключение тестовой/рабочей площадки через флаг test_mode в extern.json
- Базовые HTTP-методы GET/POST с нужными заголовками
- Методы для работы с тестовыми данными (Extern Test Tools)

Использование из командной строки:
  python3 extern_api.py status      # проверить текущий токен и режим
  python3 extern_api.py login       # первичная авторизация
  python3 extern_api.py accounts    # вывести список учётных записей
  python3 extern_api.py switch-test # переключиться на тестовую площадку
  python3 extern_api.py switch-prod # переключиться на рабочую площадку

Использование из других скриптов:
  from extern_api import KonturExternClient
  client = KonturExternClient()
  accounts = client.get_accounts()

Площадки:
  Тестовая:   https://extern-api.testkontur.ru  (test_mode: true)
  Рабочая:    https://extern-api.kontur.ru       (test_mode: false)
"""

import json
import sys
import time
import pathlib
import datetime

try:
    import requests
    from dateutil import parser as dateutil_parser
except ImportError:
    print("[KONTUR ERROR] Не установлены зависимости. Запустите:")
    print("  pip install requests python-dateutil")
    sys.exit(1)

# ─── Пути ────────────────────────────────────────────────────────────────────

SKILL_DIR   = pathlib.Path(__file__).parent
CONFIG_FILE = SKILL_DIR / "extern.json"

# ─── URL площадок ────────────────────────────────────────────────────────────

PROD_BASE_URL      = "https://extern-api.kontur.ru"
PROD_IDENTITY_URL  = "https://identity.kontur.ru"
TEST_BASE_URL      = "https://extern-api.testkontur.ru"
TEST_IDENTITY_URL  = "https://identity.testkontur.ru"
TEST_TOOLS_URL     = "https://extern-api.testkontur.ru"  # Test Tools на том же хосте

# ─── Значения по умолчанию ───────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "test_mode": True,
    "credentials": {
        "api_key":   "",
        "client_id": ""
    },
    "tokens": {
        "access_token":  "",
        "refresh_token": "",
        "expires_at":    ""
    },
    "account": {
        "account_id": "",
        "inn":        "",
        "kpp":        "",
        "org_name":   ""
    },
    "settings": {
        "deadline_warning_days": 5,
        "demand_critical_days":  2,
        "ens_alert_on_negative": True
    }
}


# ─── Вспомогательные функции ─────────────────────────────────────────────────

def load_config() -> dict:
    """Загрузить конфигурацию из extern.json."""
    if not CONFIG_FILE.exists():
        print(f"[KONTUR] Файл конфигурации не найден: {CONFIG_FILE}")
        print("[KONTUR] Создаю шаблон. Заполните credentials и запустите login.")
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    """Сохранить конфигурацию в extern.json."""
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_dt(s: str) -> datetime.datetime:
    """Парсинг ISO-даты с timezone."""
    dt = dateutil_parser.parse(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# ─── Основной клиент ─────────────────────────────────────────────────────────

class KonturExternClient:
    """
    Клиент REST API Контур.Экстерн.

    test_mode: True  → тестовая площадка (testkontur.ru)
    test_mode: False → рабочая площадка (kontur.ru)

    Пример использования:
        client = KonturExternClient()
        forms  = client.get_forms_table(year=2026, quarter=1)
    """

    def __init__(self):
        self.cfg         = load_config()
        self.creds       = self.cfg["credentials"]
        self.account_cfg = self.cfg["account"]
        self.settings    = self.cfg.get("settings", {})
        self.test_mode   = self.cfg.get("test_mode", True)
        self.session     = requests.Session()

        # URL площадки определяется флагом test_mode
        if self.test_mode:
            self.base_url     = TEST_BASE_URL
            self.identity_url = TEST_IDENTITY_URL
        else:
            self.base_url     = PROD_BASE_URL
            self.identity_url = PROD_IDENTITY_URL

    # ── Аутентификация ────────────────────────────────────────────────────────

    def ensure_token(self) -> None:
        """
        Проверить актуальность access_token.
        Если истёк или истекает через менее 5 минут — обновить через refresh_token.
        """
        tokens = self.cfg["tokens"]

        if not tokens.get("access_token"):
            print("[KONTUR ERROR] Токен отсутствует. Выполните: python3 extern_api.py login")
            sys.exit(1)

        if tokens.get("expires_at"):
            expires_at = _parse_dt(tokens["expires_at"])
            if _now_utc() + datetime.timedelta(minutes=5) < expires_at:
                return  # токен ещё действителен

        self._refresh_access_token()

    def _refresh_access_token(self) -> None:
        """Обменять refresh_token на новый access_token."""
        tokens = self.cfg["tokens"]

        if not tokens.get("refresh_token"):
            print("[KONTUR ERROR] refresh_token отсутствует. Выполните: python3 extern_api.py login")
            sys.exit(1)

        print("[KONTUR] Обновление access_token...", flush=True)
        resp = requests.post(
            f"{self.identity_url}/connect/token",
            data={
                "grant_type":    "refresh_token",
                "client_id":     self.creds["client_id"],
                "client_secret": self.creds["api_key"],
                "refresh_token": tokens["refresh_token"],
                "scope":         "extern.api",
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[KONTUR ERROR] Ошибка обновления токена: {resp.status_code} {resp.text}")
            sys.exit(1)

        self._save_tokens(resp.json())
        print("[KONTUR] Токен обновлён.", flush=True)

    def device_flow_login(self) -> None:
        """
        Первичная авторизация через Device Flow.
        Выводит ссылку для открытия в браузере, ожидает подтверждения.
        Вызывается один раз при первичной настройке.

        Алгоритм (Device Authorization Flow):
        1. POST /connect/deviceauthorization → получить device_code и URL
        2. Пользователь открывает URL в браузере, входит в Контур, подтверждает доступ
        3. Параллельно опрашиваем /connect/token каждые interval секунд
        4. После подтверждения получаем access_token и refresh_token
        """
        if not self.creds.get("client_id") or not self.creds.get("api_key"):
            print("[KONTUR ERROR] Заполните credentials.client_id и credentials.api_key в extern.json")
            sys.exit(1)

        mode_label = "ТЕСТОВАЯ" if self.test_mode else "РАБОЧАЯ"
        print(f"[KONTUR] Запуск Device Flow авторизации ({mode_label} площадка)...")

        resp = requests.post(
            f"{self.identity_url}/connect/deviceauthorization",
            data={
                "client_id":     self.creds["client_id"],
                "client_secret": self.creds["api_key"],
                "scope":         "extern.api",
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[KONTUR ERROR] Ошибка получения device_code: {resp.status_code} {resp.text}")
            sys.exit(1)

        device_data = resp.json()
        verify_url  = device_data["verification_uri_complete"]
        device_code = device_data["device_code"]
        interval    = int(device_data.get("interval", 5))
        expires_in  = int(device_data.get("expires_in", 300))

        print()
        print("=" * 60)
        print(f"Площадка: {mode_label} ({self.base_url})")
        print()
        print("Откройте следующую ссылку в браузере и войдите под учётной")
        print("записью Контура, затем подтвердите доступ:")
        print()
        print(f"  {verify_url}")
        print()
        print(f"Ожидание подтверждения (максимум {expires_in} секунд)...")
        print("=" * 60)

        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            token_resp = requests.post(
                f"{self.identity_url}/connect/token",
                data={
                    "grant_type":    "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id":     self.creds["client_id"],
                    "client_secret": self.creds["api_key"],
                    "device_code":   device_code,
                    "scope":         "extern.api",
                },
                timeout=30,
            )
            if token_resp.status_code == 200:
                self._save_tokens(token_resp.json())
                print("\n✓ Авторизация успешна! Токены сохранены в extern.json.")
                print("Следующий шаг: python3 extern_api.py accounts")
                return

            err = token_resp.json()
            err_code = err.get("error", "")
            if err_code == "authorization_pending":
                print(".", end="", flush=True)
            elif err_code == "slow_down":
                interval += 2
            elif err_code in ("expired_token", "access_denied"):
                print(f"\n[KONTUR ERROR] Авторизация отклонена: {err_code}")
                sys.exit(1)

        print("\n[KONTUR ERROR] Время ожидания истекло. Запустите login заново.")
        sys.exit(1)

    def _save_tokens(self, data: dict) -> None:
        """Сохранить токены из ответа OAuth-сервера."""
        expires_in = data.get("expires_in") or data.get("expires", 3600)
        expires_at = (_now_utc() + datetime.timedelta(seconds=int(expires_in))).isoformat()
        self.cfg["tokens"] = {
            "access_token":  data["access_token"],
            "refresh_token": data.get("refresh_token", self.cfg["tokens"].get("refresh_token", "")),
            "expires_at":    expires_at,
        }
        save_config(self.cfg)

    # ── HTTP-методы ───────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization":   f"Bearer {self.cfg['tokens']['access_token']}",
            "X-Kontur-Apikey": self.creds["api_key"],
            "Content-Type":    "application/json",
            "Accept":          "application/json",
        }

    def get(self, path: str, params: dict = None, base: str = None) -> dict:
        """GET-запрос к API. path — относительно base_url."""
        self.ensure_token()
        url  = (base or self.base_url) + path
        resp = self.session.get(url, headers=self._headers(), params=params, timeout=30)
        return self._handle_response(resp)

    def post(self, path: str, body: dict = None, base: str = None) -> dict:
        """POST-запрос к API."""
        self.ensure_token()
        url  = (base or self.base_url) + path
        resp = self.session.post(
            url, headers=self._headers(), json=body or {}, timeout=30,
        )
        return self._handle_response(resp)

    def _handle_response(self, resp: requests.Response) -> dict:
        """Обработка ответа API."""
        if resp.status_code in (200, 201):
            return resp.json() if resp.content else {}
        if resp.status_code == 204:
            return {}
        if resp.status_code == 401:
            self._refresh_access_token()
            raise RuntimeError("TOKEN_REFRESHED — повторите запрос")
        if resp.status_code == 429:
            print("[KONTUR] Превышен лимит запросов. Пауза 60 секунд...")
            time.sleep(60)
            raise RuntimeError(f"RATE_LIMITED: {resp.text}")
        if resp.status_code >= 500:
            print(f"[KONTUR ERROR] Ошибка сервера {resp.status_code}. Пауза 30 секунд...")
            time.sleep(30)
            raise RuntimeError(f"SERVER_ERROR {resp.status_code}: {resp.text}")
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text
        raise RuntimeError(f"HTTP {resp.status_code}: {err_body}")

    # ── Учётные записи ────────────────────────────────────────────────────────

    @property
    def account_id(self) -> str:
        aid = self.account_cfg.get("account_id", "")
        if not aid:
            print("[KONTUR ERROR] account_id не задан в extern.json.")
            print("Запустите: python3 extern_api.py accounts")
            sys.exit(1)
        return aid

    def get_accounts(self) -> list:
        """Список учётных записей авторизованного пользователя."""
        data = self.get("/v1/accounts")
        return data.get("accounts-list", [])

    def get_account(self, account_id: str = None) -> dict:
        """Детали конкретной учётной записи."""
        aid = account_id or self.account_id
        return self.get(f"/v1/accounts/{aid}")

    # ── Организации ───────────────────────────────────────────────────────────

    def get_organizations(self) -> list:
        """Список организаций учётной записи."""
        data = self.get(f"/v1/{self.account_id}/organizations")
        return data.get("organizations-list", [])

    def get_organization(self, org_id: str) -> dict:
        """Детали организации."""
        return self.get(f"/v1/{self.account_id}/organizations/{org_id}")

    # ── Таблица отчётности ────────────────────────────────────────────────────

    def get_forms(self) -> list:
        """Справочник форм отчётности организации (без статусов)."""
        data = self.get(f"/v1/{self.account_id}/forms")
        return data.get("forms", [])

    def get_forms_table(self, year: int, quarter: int = None) -> list:
        """
        Таблица отчётности с дедлайнами и статусами за период.

        year    — отчётный год (например, 2026)
        quarter — квартал 1-4 или None для всего года

        Документация: https://developer.kontur.ru/Docs/extern-api/scenarios/device_api/reports_table.html
        Swagger: https://developer.kontur.ru/doc/extern
        """
        period = {"year": year}
        if quarter:
            period["quarter"] = quarter

        body = {
            "account-ids": [self.account_id],
            "period": period,
        }
        data = self.post(f"/v1/{self.account_id}/forms/search", body)
        accounts = data.get("accounts", [])
        if not accounts:
            return []
        return accounts[0].get("forms", [])

    # ── Документообороты ──────────────────────────────────────────────────────

    def get_docflows(
        self,
        doc_type:  str  = None,
        incoming:  bool = None,
        finished:  bool = None,
        date_from: str  = None,
        date_to:   str  = None,
        take:      int  = 100,
        skip:      int  = 0,
    ) -> dict:
        """
        Страница документооборотов с фильтрами.

        doc_type  — тип ДО без URN-префикса: 'fns534-report', 'fns534-demand', и др.
        incoming  — True = только входящие (от КО к нам)
        finished  — False = только незавершённые
        take/skip — пагинация (max take=100)

        Возвращает dict:
          docflows-list  — список ДО
          total-count    — общее количество ДО (для итерации)

        Swagger: https://developer.kontur.ru/doc/extern.docflows
        """
        params = {"take": take, "skip": skip}
        if doc_type:
            params["type"] = f"urn:docflow:{doc_type}"
        if incoming is not None:
            params["incoming"] = str(incoming).lower()
        if finished is not None:
            params["finished"] = str(finished).lower()
        if date_from:
            params["updatedFrom"] = date_from
        if date_to:
            params["updatedTo"] = date_to

        return self.get(f"/v1/{self.account_id}/docflows", params=params)

    def get_all_docflows(self, **kwargs) -> list:
        """
        Получить ВСЕ ДО с заданными фильтрами, обходя пагинацию.
        Делает минимальное число запросов: сначала проверяет total-count,
        затем запрашивает ровно столько страниц, сколько нужно.
        """
        # Первый запрос — узнаём общее количество
        first = self.get_docflows(take=100, skip=0, **kwargs)
        result = list(first.get("docflows-list", []))
        total  = first.get("total-count", len(result))

        skip = 100
        while skip < total:
            batch = self.get_docflows(take=100, skip=skip, **kwargs)
            items = batch.get("docflows-list", [])
            if not items:
                break
            result.extend(items)
            skip += 100

        return result

    def get_docflow(self, docflow_id: str) -> dict:
        """
        Детали одного документооборота по его ID.
        Содержит статус, список документов, ссылки типа 'reply'.
        """
        return self.get(f"/v1/{self.account_id}/docflows/{docflow_id}")

    def get_docflow_documents(self, docflow_id: str) -> list:
        """Список документов в документообороте."""
        data = self.get(f"/v1/{self.account_id}/docflows/{docflow_id}/documents")
        return data.get("docflow-document-list", [])

    def get_docflow_document(self, docflow_id: str, document_id: str) -> dict:
        """Детали конкретного документа в документообороте."""
        return self.get(
            f"/v1/{self.account_id}/docflows/{docflow_id}/documents/{document_id}"
        )

    # ── Лента событий ─────────────────────────────────────────────────────────

    def get_events(self, take: int = 100, seq_id: str = None) -> dict:
        """
        Лента событий документооборотов.

        Используется для отслеживания изменений в ДО без перебора всего списка.
        seq_id — идентификатор последнего обработанного события (для инкрементального опроса).

        Возвращает dict:
          docflows-with-events — список ДО с новыми событиями
          first-event-id       — ID первого события в выборке
          last-event-id        — ID последнего события (сохранять для следующего запроса)

        Swagger: https://developer.kontur.ru/doc/extern.docflows
        """
        params = {"take": take}
        if seq_id:
            params["fromId"] = seq_id
        return self.get(f"/v1/{self.account_id}/events", params=params)

    # ── Представление документов к требованиям ────────────────────────────────

    def get_inventories(self, docflow_id: str) -> dict:
        """
        Список инвентаризаций (представлений документов) к требованию ФНС.

        Swagger: https://developer.kontur.ru/doc/extern.docflows  (inventories section)
        """
        return self.get(
            f"/v1/{self.account_id}/docflows/{docflow_id}/documents/inventory"
        )

    # ── Черновики ─────────────────────────────────────────────────────────────

    def get_drafts(self) -> list:
        """Список черновиков (незавершённых отправок)."""
        data = self.get(f"/v1/{self.account_id}/drafts")
        return data.get("drafts-list", [])

    def get_draft(self, draft_id: str) -> dict:
        """Детали черновика."""
        return self.get(f"/v1/{self.account_id}/drafts/{draft_id}")

    # ── Сертификаты ───────────────────────────────────────────────────────────

    def get_certificates(self) -> list:
        """
        Список сертификатов учётной записи (электронных подписей).
        Нужны для понимания доступных КЭП.
        """
        data = self.get(f"/v1/{self.account_id}/certificates")
        return data.get("certificates-list", [])

    # ── Доверенности (МЧД) ────────────────────────────────────────────────────

    def get_warrants(self) -> list:
        """Список машиночитаемых доверенностей (МЧД)."""
        data = self.get(f"/v1/{self.account_id}/warrants")
        return data.get("warrants-list", [])

    # ── Тестовые инструменты (только test_mode=True) ──────────────────────────

    def _require_test_mode(self, method_name: str):
        if not self.test_mode:
            raise RuntimeError(
                f"{method_name} доступен только в тестовом режиме (test_mode: true в extern.json)"
            )

    def test_generate_fns_demand(
        self,
        inn:      str = None,
        kpp:      str = None,
        ifns_code: str = "0087",
    ) -> dict:
        """
        Сгенерировать входящее требование ФНС в тестовой среде.

        ifns_code:
          '0087' — робот, принимает всё
          '0088' — робот, не принимает ничего
          '0085' — присылает уведомления об уточнении

        Swagger: https://developer.kontur.ru/doc/extern.test.tools
        """
        self._require_test_mode("test_generate_fns_demand")
        body = {
            "account-id": self.account_id,
            "inn":  inn  or self.account_cfg.get("inn", ""),
            "kpp":  kpp  or self.account_cfg.get("kpp", ""),
            "ifns-code": ifns_code,
        }
        return self.post(f"/v1/{self.account_id}/test-tools/create-fns-letter", body)

    def test_generate_fns_finish(self, docflow_id: str) -> dict:
        """
        Имитировать завершение обработки ДО роботом ФНС (тестовая среда).

        Swagger: https://developer.kontur.ru/doc/extern.test.tools
        """
        self._require_test_mode("test_generate_fns_finish")
        return self.post(
            f"/v1/{self.account_id}/test-tools/docflows/{docflow_id}/finish"
        )


# ─── CLI для ручного использования ───────────────────────────────────────────

def _cmd_login():
    client = KonturExternClient()
    client.device_flow_login()


def _cmd_accounts():
    client = KonturExternClient()
    client.ensure_token()
    accounts = client.get_accounts()
    if not accounts:
        print("Учётных записей не найдено.")
        return
    print(f"Найдено учётных записей: {len(accounts)}\n")
    for acc in accounts:
        print(f"  ID      : {acc.get('id', '—')}")
        print(f"  ИНН     : {acc.get('inn', '—')}")
        print(f"  КПП     : {acc.get('kpp', '—')}")
        print(f"  Организ.: {acc.get('organization-name', '—')}")
        print()
    print("Скопируйте нужный ID в extern.json → account.account_id")


def _cmd_status():
    cfg    = load_config()
    tokens = cfg["tokens"]
    mode   = "ТЕСТОВАЯ" if cfg.get("test_mode", True) else "РАБОЧАЯ"
    print(f"Площадка  : {mode}")
    if not tokens.get("access_token"):
        print("Токен     : отсутствует. Требуется авторизация (python3 extern_api.py login)")
        return
    if tokens.get("expires_at"):
        try:
            expires_at = _parse_dt(tokens["expires_at"])
            remaining  = expires_at - _now_utc()
            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() // 60)
                print(f"Токен     : действителен ещё {mins} мин.")
            else:
                print("Токен     : истёк (будет обновлён автоматически)")
        except Exception:
            print("Токен     : есть (срок неизвестен)")
    account_id = cfg["account"].get("account_id", "")
    org_name   = cfg["account"].get("org_name", "")
    print(f"account_id: {account_id or '(не задан)'}")
    if org_name:
        print(f"Организ.  : {org_name}")


def _cmd_switch(test: bool):
    cfg = load_config()
    cfg["test_mode"] = test
    # Сбросить токены — у тестовой и рабочей площадок они разные
    cfg["tokens"] = {"access_token": "", "refresh_token": "", "expires_at": ""}
    save_config(cfg)
    mode = "ТЕСТОВАЯ (testkontur.ru)" if test else "РАБОЧАЯ (kontur.ru)"
    print(f"✓ Переключено на {mode}.")
    print("Токены сброшены. Выполните повторную авторизацию: python3 extern_api.py login")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "login":
        _cmd_login()
    elif cmd == "accounts":
        _cmd_accounts()
    elif cmd == "status":
        _cmd_status()
    elif cmd == "switch-test":
        _cmd_switch(test=True)
    elif cmd == "switch-prod":
        _cmd_switch(test=False)
    else:
        print(f"Неизвестная команда: {cmd}")
        print("Доступные команды: login, accounts, status, switch-test, switch-prod")
        sys.exit(1)
