"""
Модуль логирования для DocHub Architect Tool.

Настраивает логирование с ротацией файлов и форматированием.
"""

import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_logger(
    name: str = "dochub_architect",
    log_file: str = "logs/app.log",
    level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 3
) -> logging.Logger:
    """
    Настройка логгера с ротацией файлов.

    Args:
        name: Имя логгера
        log_file: Путь к файлу логов
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
        max_bytes: Максимальный размер файла лога
        backup_count: Количество резервных копий

    Returns:
        Настроенный логгер
    """
    # Создаем директорию для логов
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Создаем логгер
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Очищаем существующие обработчики
    logger.handlers.clear()

    # Формат логов
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Обработчик для файла с ротацией
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Обработчик для консоли.
    # В stdio-транспорте MCP stdout занят протоколом, поэтому пишем в stderr:
    # любая посторонняя строка в stdout ломает обмен с клиентом.
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "dochub_architect") -> logging.Logger:
    """
    Получить существующий логгер.

    Args:
        name: Имя логгера

    Returns:
        Логгер
    """
    return logging.getLogger(name)
