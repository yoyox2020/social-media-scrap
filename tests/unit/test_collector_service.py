"""Unit tests untuk CollectorService — mock repository dan worker."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.keywords.models import Keyword
from app.repositories.keyword_repository import KeywordRepository
from app.services.collector.service import CollectorService
from app.shared.exceptions import NotFoundError, ValidationError


def _make_keyword(**kwargs) -> Keyword:
    kw = MagicMock(spec=Keyword)
    kw.id = kwargs.get("id", uuid.uuid4())
    kw.keyword = kwargs.get("keyword", "python tutorial")
    kw.is_active = kwargs.get("is_active", True)
    return kw


@pytest.mark.asyncio
async def test_trigger_collection_dispatches_tasks():
    keyword_repo = AsyncMock(spec=KeywordRepository)
    kw = _make_keyword()
    keyword_repo.get_by_id.return_value = kw

    mock_task = MagicMock()
    mock_task.id = "celery-task-id-123"

    with patch("app.services.collector.service.collect_posts_task") as mock_celery:
        mock_celery.delay.return_value = mock_task
        service = CollectorService(keyword_repo)
        result = await service.trigger_collection(kw.id, ["tiktok", "youtube"])

    assert result.keyword_id == kw.id
    assert result.keyword_text == "python tutorial"
    assert len(result.jobs) == 2
    assert result.jobs[0]["platform"] == "tiktok"
    assert result.jobs[0]["job_id"] == "celery-task-id-123"


@pytest.mark.asyncio
async def test_trigger_collection_raises_when_keyword_not_found():
    keyword_repo = AsyncMock(spec=KeywordRepository)
    keyword_repo.get_by_id.return_value = None

    service = CollectorService(keyword_repo)
    with pytest.raises(NotFoundError):
        await service.trigger_collection(uuid.uuid4(), ["tiktok"])


@pytest.mark.asyncio
async def test_trigger_collection_raises_for_inactive_keyword():
    keyword_repo = AsyncMock(spec=KeywordRepository)
    keyword_repo.get_by_id.return_value = _make_keyword(is_active=False)

    service = CollectorService(keyword_repo)
    with pytest.raises(ValidationError, match="tidak aktif"):
        await service.trigger_collection(uuid.uuid4(), ["tiktok"])


@pytest.mark.asyncio
async def test_trigger_collection_raises_for_invalid_platform():
    keyword_repo = AsyncMock(spec=KeywordRepository)
    keyword_repo.get_by_id.return_value = _make_keyword()

    service = CollectorService(keyword_repo)
    with pytest.raises(ValidationError, match="tidak didukung"):
        await service.trigger_collection(uuid.uuid4(), ["invalid_platform"])


def _mock_ensemble_client():
    """EnsembleDataClient dipakai sbg `async with EnsembleDataClient() as client:`."""
    client_cls = MagicMock()
    client_instance = MagicMock()
    client_cls.return_value = client_instance
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    return client_cls


@pytest.mark.asyncio
async def test_collect_for_platform_flags_used_fallback_on_silent_success():
    """
    Regresi utk bug 2026-07-16: kalau YouTubeConnector diam-diam fallback ke
    YouTube Data API v3 (raw response bermarker _source="youtube_data_api")
    TANPA exception, CollectionResult.used_fallback harus True -- BUKAN
    ditebak dari teks error (errors tetap kosong pada fallback yang berhasil).
    """
    keyword_repo = AsyncMock(spec=KeywordRepository)
    kw = _make_keyword()
    keyword_repo.get_by_id.return_value = kw

    fallback_raw = {
        "_source": "youtube_data_api",
        "data": {"items": [{"id": {"videoId": "abc123"}, "snippet": {"title": "Test"}}]},
    }

    fake_connector_instance = MagicMock()
    fake_connector_instance.extract_posts.return_value = [
        {"videoId": "abc123", "_yt_api": True, "snippet": {"title": "Test"}}
    ]
    fake_connector_instance.extract_cursor.return_value = None
    fake_connector_cls = MagicMock(return_value=fake_connector_instance)

    fake_post = MagicMock()
    fake_post.external_id = "abc123"
    fake_normalizer = MagicMock()
    fake_normalizer.normalize.return_value = [fake_post]

    post_repo_instance = AsyncMock()
    post_repo_instance.get_existing_external_ids.return_value = set()
    post_repo_instance.bulk_create.return_value = 1

    db = AsyncMock()
    db.commit = AsyncMock()

    inner_keyword_repo = AsyncMock(spec=KeywordRepository)
    inner_keyword_repo.get_by_id.return_value = kw

    with patch("app.services.collector.service.KeywordRepository", return_value=inner_keyword_repo), \
         patch("app.integrations.ensemble_data.client.EnsembleDataClient", _mock_ensemble_client()), \
         patch("app.services.collector.service._get_connector", return_value=fake_connector_cls), \
         patch("app.services.collector.service._fetch_page", AsyncMock(return_value=fallback_raw)), \
         patch("app.services.processing.normalizer.get_normalizer", return_value=fake_normalizer), \
         patch("app.services.processing.normalizer.enrich_youtube_statistics", AsyncMock()), \
         patch("app.repositories.post_repository.PostRepository", return_value=post_repo_instance):
        service = CollectorService(keyword_repo)
        result = await service.collect_for_platform(
            keyword_id=kw.id, platform="youtube", max_pages=1, db=db,
        )

    assert result.used_fallback is True
    assert result.errors == []
    assert result.new_posts == 1
    assert result.to_dict()["used_fallback"] is True


@pytest.mark.asyncio
async def test_collect_for_platform_no_fallback_flag_when_ensembledata_succeeds():
    """Kebalikan dari test di atas -- raw response TANPA marker _source berarti
    EnsembleData asli yang berhasil, used_fallback harus tetap False."""
    keyword_repo = AsyncMock(spec=KeywordRepository)
    kw = _make_keyword()
    keyword_repo.get_by_id.return_value = kw

    ensembledata_raw = {"data": {"posts": [{"videoId": "xyz789"}]}}

    fake_connector_instance = MagicMock()
    fake_connector_instance.extract_posts.return_value = [{"videoId": "xyz789"}]
    fake_connector_instance.extract_cursor.return_value = None
    fake_connector_cls = MagicMock(return_value=fake_connector_instance)

    fake_post = MagicMock()
    fake_post.external_id = "xyz789"
    fake_normalizer = MagicMock()
    fake_normalizer.normalize.return_value = [fake_post]

    post_repo_instance = AsyncMock()
    post_repo_instance.get_existing_external_ids.return_value = set()
    post_repo_instance.bulk_create.return_value = 1

    db = AsyncMock()
    db.commit = AsyncMock()

    inner_keyword_repo = AsyncMock(spec=KeywordRepository)
    inner_keyword_repo.get_by_id.return_value = kw

    with patch("app.services.collector.service.KeywordRepository", return_value=inner_keyword_repo), \
         patch("app.integrations.ensemble_data.client.EnsembleDataClient", _mock_ensemble_client()), \
         patch("app.services.collector.service._get_connector", return_value=fake_connector_cls), \
         patch("app.services.collector.service._fetch_page", AsyncMock(return_value=ensembledata_raw)), \
         patch("app.services.processing.normalizer.get_normalizer", return_value=fake_normalizer), \
         patch("app.services.processing.normalizer.enrich_youtube_statistics", AsyncMock()), \
         patch("app.repositories.post_repository.PostRepository", return_value=post_repo_instance):
        service = CollectorService(keyword_repo)
        result = await service.collect_for_platform(
            keyword_id=kw.id, platform="youtube", max_pages=1, db=db,
        )

    assert result.used_fallback is False
    assert result.new_posts == 1
