"""
Локальный сервер превью.

Показывает рядом исходную схему и сгенерированный DocHub YAML,
чтобы сверить их перед пушем. Страница сама опрашивает /api/state
и перерисовывается, когда агент правит YAML.
"""

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from app.preview.renderer import PreviewError, PreviewRenderer
from app.utils.logger import get_logger
from app.utils.paths import DRAWIO_VIEWER, STATIC_DIR


class PreviewServer:
    """Сервер превью, работающий в фоновом потоке."""

    def __init__(
        self,
        source_path: Path,
        yaml_path: Path,
        host: str = '127.0.0.1',
        port: int = 0
    ):
        """
        Args:
            source_path: Исходная схема (.drawio / .puml)
            yaml_path: Сгенерированный DocHub YAML
            host: Интерфейс (по умолчанию только localhost)
            port: Порт, 0 — выбрать свободный
        """
        self.source_path = Path(source_path)
        self.yaml_path = Path(yaml_path)
        self.renderer = PreviewRenderer()
        self.logger = get_logger()

        handler = partial(_PreviewHandler, self)
        self._httpd = ThreadingHTTPServer((host, port), handler)
        self._thread: Optional[threading.Thread] = None

        self._cached_version: Optional[float] = None
        self._cached_contexts: list = []
        self._cached_error: Optional[str] = None

        # Картинка исходной схемы, отрисованная встроенным движком DrawIO.
        # Страница присылает её сразу после отрисовки: так экспорт работает
        # тем же движком, что и просмотр, без установки Draw.io Desktop
        self.source_svg: Optional[str] = None

    @property
    def url(self) -> str:
        """URL превью."""
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> str:
        """
        Запустить сервер в фоне.

        Returns:
            URL превью
        """
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            daemon=True,
            name='preview-server'
        )
        self._thread.start()
        self.logger.info(f"Превью доступно на {self.url}")
        return self.url

    def stop(self) -> None:
        """Остановить сервер."""
        self._httpd.shutdown()
        self._httpd.server_close()

    def state(self) -> dict:
        """
        Текущее состояние: список контекстов и версия YAML.

        Версия — время изменения файла, по ней страница понимает,
        что агент переписал YAML и пора перерисовать.
        """
        version = self._version()

        # Перечитываем YAML только когда файл изменился —
        # опрос идёт раз в полторы секунды
        if version != self._cached_version:
            try:
                self._cached_contexts = self.renderer.list_contexts(self.yaml_path)
                self._cached_error = None
            except Exception as e:
                # Дамп разборщика YAML пользователю ничего не говорит:
                # называем файл и то, что с ним делать
                detail = ' '.join(str(e).split())[:200]
                self._cached_contexts = []
                self._cached_error = (
                    f'{self.yaml_path.name}: файл не читается как YAML. '
                    f'Проверьте отступы, кавычки и незакрытые скобки — '
                    f'разборщик остановился так: {detail}'
                )
            self._cached_version = version

        return {
            'source_name': self.source_path.name,
            'yaml_name': self.yaml_path.name,
            'contexts': self._cached_contexts,
            'version': version,
            'error': self._cached_error
        }

    def _version(self) -> float:
        """Метка изменения YAML."""
        try:
            return self.yaml_path.stat().st_mtime
        except OSError:
            return 0.0


class _PreviewHandler(BaseHTTPRequestHandler):
    """Обработчик запросов превью."""

    def __init__(self, server: PreviewServer, *args, **kwargs):
        self._preview = server
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # noqa: N802 - подпись из базового класса
        """Заглушить лог в stderr."""

    def do_GET(self):  # noqa: N802 - подпись из базового класса
        """Обработать GET."""
        route = urlparse(self.path)
        query = parse_qs(route.query)

        try:
            if route.path == '/':
                self._send_file(STATIC_DIR / 'index.html', 'text/html; charset=utf-8')

            elif route.path == '/vendor/viewer-static.min.js':
                self._send_file(DRAWIO_VIEWER, 'text/javascript; charset=utf-8')

            elif route.path == '/api/state':
                self._send_json(self._preview.state())

            elif route.path == '/api/source':
                self._send_json(
                    self._preview.renderer.describe_source(self._preview.source_path)
                )

            elif route.path == '/api/context':
                context_id = (query.get('id') or [None])[0]
                svg = self._preview.renderer.render_context(
                    self._preview.yaml_path, context_id
                )
                self._send_bytes(svg.encode('utf-8'), 'image/svg+xml; charset=utf-8')

            elif route.path == '/api/context.puml':
                context_id = (query.get('id') or [None])[0]
                source = self._preview.renderer.context_plantuml(
                    self._preview.yaml_path, context_id
                )
                self._send_bytes(
                    source.encode('utf-8'), 'text/plain; charset=utf-8'
                )

            elif route.path == '/api/yaml':
                self._send_bytes(
                    self._preview.yaml_path.read_bytes(),
                    'text/yaml; charset=utf-8'
                )

            elif route.path == '/api/source.svg':
                if not self._preview.source_svg:
                    self._send_json(
                        {'error': 'страница ещё не прислала отрисованную схему'},
                        status=404
                    )
                else:
                    self._send_bytes(
                        self._preview.source_svg.encode('utf-8'),
                        'image/svg+xml; charset=utf-8'
                    )

            else:
                self._send_bytes(b'Not found', 'text/plain; charset=utf-8', status=404)

        except PreviewError as e:
            self._send_json({'error': str(e)}, status=422)
        except FileNotFoundError as e:
            self._send_json({'error': f'Файл не найден: {e}'}, status=404)
        except Exception as e:
            self._send_json({'error': f'Внутренняя ошибка: {e}'}, status=500)

    def do_POST(self):  # noqa: N802 - подпись из базового класса
        """Принять от страницы схему, отрисованную движком DrawIO."""
        route = urlparse(self.path)

        if route.path != '/api/source.svg':
            self._send_bytes(b'Not found', 'text/plain; charset=utf-8', status=404)
            return

        try:
            length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            length = 0

        # Схемы бывают на несколько мегабайт, но не безгранично
        if not 0 < length <= 64 * 1024 * 1024:
            self._send_json({'error': 'неверный размер тела запроса'}, status=400)
            return

        body = self.rfile.read(length).decode('utf-8', errors='replace')

        if '<svg' not in body[:2000]:
            self._send_json({'error': 'ожидается SVG'}, status=400)
            return

        self._preview.source_svg = body
        self._send_json({'ok': True, 'bytes': len(body)})

    def _send_file(self, path: Path, content_type: str) -> None:
        """Отдать файл с диска."""
        self._send_bytes(path.read_bytes(), content_type)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        """Отдать JSON."""
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._send_bytes(body, 'application/json; charset=utf-8', status)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        """Отдать произвольное тело ответа."""
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        # Превью всегда должно показывать текущее состояние файлов
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
