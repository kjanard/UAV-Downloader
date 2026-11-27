"""Source resolution is not URL validation or a successful skipped download."""
import errno
from types import SimpleNamespace

import pytest
from requests.exceptions import Timeout

from uav_downloader.apps import watch
from uav_downloader.core.storage import StorageWriteError


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(watch, 'load_seen', lambda: {})
    monkeypatch.setattr(watch, 'save_seen', lambda _seen: None)
    logs = []
    instance = watch.SmallToolWorker(logs.append)
    instance._cleanup_temp = lambda _site: pytest.fail('No download was started')
    return instance, logs


@pytest.mark.parametrize('error', [
    Timeout('https://cdn.example/file?token=PRIVATE'),
    ValueError('Huangguo private player sources are not supported'),
    RuntimeError('metadata contains PRIVATE'),
    None,
])
def test_resolution_failure_is_retryable_and_does_not_leak(error, worker, monkeypatch, tmp_path):
    instance, logs = worker
    job = SimpleNamespace(_last_error=error, is_url_vaildate=lambda: False)
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: job)
    assert instance._download_one(
        {'url': 'https://huangguoai.com/video/123/', 'title': 'Fixture'},
        str(tmp_path)) == 'source_failed'
    assert instance._seen == {}
    assert instance._progress is None
    assert any('[SOURCE-ERR]' in line for line in logs)
    assert not any('invalid URL' in line or 'PRIVATE' in line for line in logs)


def test_source_storage_error_retains_stop_semantics(worker, monkeypatch, tmp_path):
    instance, _logs = worker
    job = SimpleNamespace(
        _last_error=StorageWriteError(tmp_path, OSError(errno.EIO, 'device offline')),
        is_url_vaildate=lambda: False)
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: job)
    assert instance._download_one(
        {'url': 'https://huangguoai.com/video/123/'}, str(tmp_path)) == 'storage_failed'
    assert instance._stop.is_set()
    assert instance._seen == {}


def test_unrecognized_site_is_not_saved_as_seen(worker, monkeypatch, tmp_path):
    instance, logs = worker
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: None)
    assert instance._download_one(
        {'url': 'https://unsupported.example/video/123/', 'title': 'Fixture'},
        str(tmp_path)) == 'source_failed'
    assert instance._seen == {}
    assert any('unsupported URL' in line for line in logs)


def test_failed_and_cancelled_downloads_return_explicit_status_not_none(
        worker, monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from uav_downloader.apps.watch_categories import find_target

    instance, _logs = worker
    instance._cleanup_temp = lambda _site: None

    false_job = SimpleNamespace(
        _cancel_job=False, is_url_vaildate=lambda: True,
        start_download=lambda: False)
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: false_job)
    assert instance._download_one(
        {'url': 'https://missav.ws/test-001', 'title': 'TEST-001'},
        str(tmp_path)) == 'download_incomplete'

    cancel_job = SimpleNamespace(
        _cancel_job=True, is_url_vaildate=lambda: True,
        start_download=lambda: False)
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: cancel_job)
    assert instance._download_one(
        {'url': 'https://missav.ws/test-002', 'title': 'TEST-002'},
        str(tmp_path)) == 'cancelled'

    def boom():
        raise RuntimeError('merge failed')

    err_job = SimpleNamespace(
        _cancel_job=False, is_url_vaildate=lambda: True, start_download=boom)
    monkeypatch.setattr(watch.M3U8Sites, 'CreateSite', lambda *_args: err_job)
    assert instance._download_one(
        {'url': 'https://missav.ws/test-003', 'title': 'TEST-003'},
        str(tmp_path)) == 'download_incomplete'

    # End-to-end _scan_and_download check: failed download must keep first_run_done False.
    monkeypatch.setattr(watch, 'PER_VIDEO_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch, 'PER_LISTING_FETCH_DELAY_SEC', 0)
    monkeypatch.setattr(watch.MissAVBrowser, 'fetch_page', lambda url: (
        [] if 'page=2' in url
        else [{'url': 'https://missav.ws/test-001', 'title': 'TEST-001'}]
    ))
    instance._fetch_missav_video_date = lambda _url: (
        datetime(2026, 10, 10, tzinfo=timezone.utc), '2026-10-10'
    )
    cfg = {
        'output_folder': str(tmp_path),
        'baseline_date': '2026-01-01',
        'first_run_done': False,
        'version_preference': 'standard',
        'selected_targets': [{'site': 'MissAV', 'id': find_target(
            'MissAV', legacy_name='最近更新')['id']}],
    }
    assert instance._scan_and_download(cfg) is False
    assert cfg['first_run_done'] is False
