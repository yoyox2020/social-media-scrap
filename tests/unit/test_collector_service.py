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
