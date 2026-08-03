"""
Тесты доменной схемы по ссылке.

Схема обновляется один раз — на подготовке, вместе с репозиторием. Дальше,
начиная с брифа, работа идёт по скачанной копии: в закрытом контуре сети
может не быть, и обрывать этим сборку схемы нельзя.
"""

import io
import urllib.error
import urllib.request

import pytest

from app.core import ddd_source

SCHEMA = b'<?xml version="1.0"?><mxfile><diagram/></mxfile>'
LINK = 'https://drive.google.com/file/d/EXAMPLE_FILE_ID/view'


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    """Свой кэш на каждый тест."""
    monkeypatch.setenv('DOCHUB_CACHE', str(tmp_path))
    return tmp_path


def serve(body: bytes, monkeypatch, seen=None):
    """Подменить сеть на заданный ответ."""
    def fake_urlopen(request, timeout=None):
        if seen is not None:
            seen.append(request.full_url)
        return io.BytesIO(body)

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)


def no_network(monkeypatch):
    """Сеть недоступна."""
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError('нет доступа')

    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)


def forbid_network(monkeypatch):
    """Любое обращение в сеть — ошибка теста."""
    def explode(*args, **kwargs):
        raise AssertionError('обращение в сеть после подготовки')

    monkeypatch.setattr(urllib.request, 'urlopen', explode)


class TestLink:
    """Что считается ссылкой и куда она ведёт."""

    def test_google_drive_view_link_becomes_download(self):
        assert ddd_source.download_url(LINK) == (
            'https://drive.google.com/uc?export=download'
            '&id=EXAMPLE_FILE_ID'
        )

    def test_other_urls_are_left_alone(self):
        url = 'https://wiki.example.com/ddd.drawio'
        assert ddd_source.download_url(url) == url

    def test_path_is_not_a_url(self, tmp_path):
        assert not ddd_source.is_url(str(tmp_path / 'ddd.drawio'))


class TestRefresh:
    """Подготовка: схему обновляют до начала работы."""

    def test_downloaded_and_cached(self, monkeypatch):
        seen = []
        serve(SCHEMA, monkeypatch, seen)

        path, problem = ddd_source.refresh(LINK)

        assert path.read_bytes() == SCHEMA
        assert problem is None
        assert 'uc?export=download' in seen[0]

    def test_offline_keeps_previous_copy_and_says_so(self, monkeypatch):
        serve(SCHEMA, monkeypatch)
        ddd_source.refresh(LINK)

        no_network(monkeypatch)
        path, problem = ddd_source.refresh(LINK)

        assert path.read_bytes() == SCHEMA
        assert problem and 'копии' in problem

    def test_offline_without_copy_is_explained(self, monkeypatch):
        no_network(monkeypatch)

        path, problem = ddd_source.refresh(LINK)

        assert path is None
        assert 'не скачалась' in problem

    def test_closed_access_is_recognised(self, monkeypatch):
        """Диск отдаёт страницу входа вместо файла — это не схема."""
        serve(b'<!DOCTYPE html><html><body>Sign in</body></html>', monkeypatch)

        path, problem = ddd_source.refresh(LINK)

        assert path is None
        assert 'HTML-страница' in problem

    def test_local_file_needs_no_network(self, tmp_path, monkeypatch):
        forbid_network(monkeypatch)
        local = tmp_path / 'ddd.drawio'
        local.write_bytes(SCHEMA)

        path, problem = ddd_source.refresh(str(local))

        assert path == local
        assert problem is None

    def test_two_links_do_not_share_a_file(self, monkeypatch):
        serve(SCHEMA, monkeypatch)
        first, _ = ddd_source.refresh('https://example.com/a.drawio')
        serve(b'<?xml version="1.0"?><mxfile>b</mxfile>', monkeypatch)
        second, _ = ddd_source.refresh('https://example.com/b.drawio')

        assert first != second
        assert first.read_bytes() != second.read_bytes()


class TestLocalPath:
    """Работа: после подготовки в сеть не ходят."""

    def test_cached_copy_is_used_without_network(self, monkeypatch):
        serve(SCHEMA, monkeypatch)
        ddd_source.refresh(LINK)

        forbid_network(monkeypatch)
        path, problem = ddd_source.local_path(LINK)

        assert path.read_bytes() == SCHEMA
        assert problem is None

    def test_missing_copy_points_at_preparation(self, monkeypatch):
        """Без скачанной копии инструмент называет шаг, который её берёт."""
        forbid_network(monkeypatch)

        path, problem = ddd_source.local_path(LINK)

        assert path is None
        assert 'dochub_repo_sync' in problem

    def test_plain_path_passes_through(self, tmp_path, monkeypatch):
        forbid_network(monkeypatch)
        local = tmp_path / 'ddd.drawio'
        local.write_bytes(SCHEMA)

        assert ddd_source.local_path(str(local))[0] == local
