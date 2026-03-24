"""
Сервис рассылки прайс-листов.
1. Подключается к price@arus-trade.ru по IMAP (Mail.ru)
2. Находит последнее письмо с темой «Прайс-лист»
3. Скачивает Excel-вложение
4. Отправляет его с noreply@arustrade.ru через SMTP (Яндекс) на список получателей
"""

import os
import sys
import json
import imaplib
import smtplib
import email
import logging
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from email.header import decode_header
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

# ─── Пути ───────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
LOG_DIR = SCRIPT_DIR / "logs"
TEMP_DIR = SCRIPT_DIR / "temp"

# ─── Логирование ────────────────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"mailer_{datetime.now():%Y-%m-%d}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("price_mailer")


def load_config() -> dict:
    """Загрузка конфигурации из config.json."""
    if not CONFIG_PATH.exists():
        log.error(f"Файл конфигурации не найден: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def decode_subject(subject_raw) -> str:
    """Декодирует тему письма (может быть в base64/quoted-printable)."""
    parts = decode_header(subject_raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def fetch_price_attachment(cfg: dict) -> Path | None:
    """
    Подключается к IMAP (Mail.ru), ищет последнее письмо с темой «Прайс-лист»,
    скачивает первое Excel-вложение.
    """
    imap_cfg = cfg["imap"]
    subject_filter = cfg.get("search_subject", "Прайс-лист")

    log.info(f"[IMAP] Подключаюсь к {imap_cfg['server']}:{imap_cfg['port']}...")
    mail = imaplib.IMAP4_SSL(imap_cfg["server"], imap_cfg["port"])
    mail.login(imap_cfg["login"], imap_cfg["password"])
    log.info(f"[IMAP] Авторизация успешна: {imap_cfg['login']}")

    mail.select("INBOX")

    log.info(f'[IMAP] Ищу письма с темой «{subject_filter}»...')
    status, message_ids = mail.search("UTF-8", f'(SUBJECT "{subject_filter}")'.encode("utf-8"))

    if status != "OK" or not message_ids[0]:
        log.warning("[IMAP] Письма с такой темой не найдены.")
        mail.logout()
        return None

    ids = message_ids[0].split()
    latest_id = ids[-1]
    log.info(f"[IMAP] Найдено писем: {len(ids)}. Беру последнее (ID: {latest_id.decode()})...")

    status, msg_data = mail.fetch(latest_id, "(RFC822)")
    if status != "OK":
        log.error("[IMAP] Не удалось загрузить письмо.")
        mail.logout()
        return None

    msg = email.message_from_bytes(msg_data[0][1])
    subject = decode_subject(msg.get("Subject", ""))
    date = msg.get("Date", "неизвестно")
    log.info(f"[IMAP] Письмо: «{subject}» от {date}")

    attachment_path = None
    for part in msg.walk():
        content_disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in content_disposition:
            continue

        filename = part.get_filename()
        if filename:
            decoded_parts = decode_header(filename)
            decoded_filename = ""
            for fpart, charset in decoded_parts:
                if isinstance(fpart, bytes):
                    decoded_filename += fpart.decode(charset or "utf-8", errors="replace")
                else:
                    decoded_filename += fpart
            filename = decoded_filename

        if not filename:
            continue

        ext = Path(filename).suffix.lower()
        if ext in (".xls", ".xlsx", ".xlsm", ".xlsb"):
            attachment_path = TEMP_DIR / filename
            with open(attachment_path, "wb") as f:
                f.write(part.get_payload(decode=True))
            size_kb = attachment_path.stat().st_size / 1024
            log.info(f"[IMAP] Вложение сохранено: {filename} ({size_kb:.1f} КБ)")
            break

    if not attachment_path:
        log.warning("[IMAP] Вложение Excel в письме не найдено.")

    mail.logout()
    return attachment_path


def send_email(cfg: dict, attachment_path: Path):
    """Отправляет письмо с вложением через Яндекс SMTP каждому получателю."""
    smtp_cfg = cfg["smtp"]
    sender_login = smtp_cfg["login"]
    password = smtp_cfg["password"]
    recipients = cfg["recipients"]
    from_address = os.environ.get("FROM_ADDRESS", smtp_cfg.get("from_address", sender_login))
    subject = cfg.get("email_subject", "Актуальный прайс-лист")
    body = cfg.get("email_body", "Добрый день!\n\nВо вложении актуальный прайс-лист.\n\nС уважением.")

    log.info(f"[SMTP] Подключаюсь к {smtp_cfg['server']}:{smtp_cfg['port']}...")
    log.info(f"[SMTP] Отправитель: {from_address}")

    with smtplib.SMTP_SSL(smtp_cfg["server"], smtp_cfg["port"], timeout=30) as server:
        server.login(sender_login, password)
        log.info(f"[SMTP] Авторизация успешна: {sender_login}")

        log.info(f"[SMTP] Начинаю рассылку по {len(recipients)} адресам...")
        for recipient in recipients:
            msg = MIMEMultipart()
            msg["From"] = from_address
            msg["To"] = recipient
            msg["Subject"] = subject

            msg.attach(MIMEText(body, "plain", "utf-8"))

            with open(attachment_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)

                encoded_filename = quote(attachment_path.name)
                part.add_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{encoded_filename}"
                )
                msg.attach(part)

            try:
                server.sendmail(sender_login, recipient, msg.as_string())
                log.info(f"[SMTP] Письмо успешно отправлено → {recipient}")
            except Exception as e:
                log.error(f"[SMTP] Не удалось отправить письмо на {recipient}: {e}")

    log.info("[SMTP] Рассылка завершена.")


def cleanup(file_path: Path):
    """Удаляет временный файл."""
    try:
        file_path.unlink(missing_ok=True)
        log.info("Временный файл удалён.")
    except Exception as e:
        log.warning(f"Не удалось удалить временный файл: {e}")


def main():
    start_time = time.time()
    log.info("=" * 60)
    log.info("Запуск сервиса рассылки прайс-листов")
    log.info("=" * 60)

    try:
        cfg = load_config()

        # Шаг 1: Забрать прайс с почты (IMAP — Mail.ru)
        attachment = fetch_price_attachment(cfg)
        if not attachment:
            log.error("Прайс-лист не найден. Рассылка отменена.")
            sys.exit(1)

        # Шаг 2: Отправить получателям (SMTP — Яндекс)
        send_email(cfg, attachment)

        # Шаг 3: Очистка
        cleanup(attachment)

        end_time = time.time()
        duration = end_time - start_time
        log.info(f"Скрипт выполнен за {duration:.2f} сек.")
        log.info("✅ Все задачи выполнены!")

    except imaplib.IMAP4.error as e:
        log.error(f"Ошибка IMAP (получение почты): {e}")
        sys.exit(1)
    except smtplib.SMTPAuthenticationError:
        log.error("Ошибка авторизации SMTP. Проверьте логин и пароль приложения Яндекс.")
        sys.exit(1)
    except Exception as e:
        log.error(f"Непредвиденная ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
