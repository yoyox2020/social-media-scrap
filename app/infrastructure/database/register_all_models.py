"""Import SEMUA domain model supaya SQLAlchemy mapper bisa resolve
relationship antar tabel (mis. Comment.sentiment -> "Sentiment") --
dipakai bareng oleh app.main (proses API) DAN app.workers.celery_app
(proses Celery worker/beat), krn Celery TIDAK otomatis import app.main.

Ditemukan 2026-07-22 (fitur auto-crawl YouTube): task Celery yg cuma
import app.agents.pipeline (tanpa modul ini) crash
`InvalidRequestError: ... expression 'Sentiment' failed to locate a
name` begitu ORM pertama kali query Comment -- krn proses worker beda
dgn proses API, mapper registry-nya juga terpisah, import di main.py
TIDAK ikut kebawa ke worker."""

import app.domain.users.models  # noqa: F401
import app.domain.projects.models  # noqa: F401
import app.domain.keywords.models  # noqa: F401
import app.domain.posts.models  # noqa: F401
import app.domain.comments.models  # noqa: F401
import app.domain.sentiments.models  # noqa: F401
import app.domain.entities.models  # noqa: F401
import app.domain.trends.models  # noqa: F401
import app.domain.reports.models  # noqa: F401
import app.domain.trending.models  # noqa: F401
import app.domain.youtube_analysis.models  # noqa: F401
import app.domain.search_topics.models  # noqa: F401
import app.domain.scrape_runs.models  # noqa: F401
import app.domain.instagram_trending.models  # noqa: F401
import app.domain.trend_recommendations.models  # noqa: F401
import app.domain.trend_recommendations.platform_usage_models  # noqa: F401
import app.domain.agent_registry.models  # noqa: F401
import app.domain.agent_registry.pool_models  # noqa: F401
import app.domain.third_party_apis.models  # noqa: F401
import app.domain.agent_curl_targets.models  # noqa: F401
import app.domain.agent_activity_log.models  # noqa: F401
import app.domain.rotation_key_bank.models  # noqa: F401
import app.domain.youtube_discovery.models  # noqa: F401
import app.domain.youtube_video_metadata.models  # noqa: F401
import app.domain.threads.models  # noqa: F401
import app.domain.viral_tracking.models  # noqa: F401
