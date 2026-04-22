import logging
import os
from datetime import datetime


def setup_logger(name=__name__):
    """
    Настраивает логгер, который выводит сообщения в консоль и в файл в папке .tmp/logs/.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Создаем папку для логов
    log_dir = os.path.join(os.getcwd(), ".tmp", "logs")
    os.makedirs(log_dir, exist_ok=True)

    # Имя файла лога с текущей датой
    log_file = os.path.join(log_dir, f"{datetime.now().strftime('%Y-%m-%d')}.log")

    # Формат сообщений
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Handler для файла
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    # Handler для консоли
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


if __name__ == "__main__":
    log = setup_logger("TestLogger")
    log.info("Логгер успешно настроен!")
    log.debug("Это сообщение уровня DEBUG")
