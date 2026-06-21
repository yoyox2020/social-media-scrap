"""
AI Service — orkestrasi pipeline inference per post dan per keyword.

Pipeline per post:
  1. Sentiment (IndoBERT) → simpan ke tabel sentiments
  2. NER (GLiNER)         → simpan ke tabel entities
  3. Embedding (BGE-M3)   → update posts.embedding (pgvector)

Semua model di-load secara lazy (pertama kali dibutuhkan).
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities.models import Entity
from app.domain.posts.models import Post
from app.domain.sentiments.models import Sentiment
from app.repositories.entity_repository import EntityRepository
from app.repositories.post_repository import PostRepository
from app.repositories.sentiment_repository import SentimentRepository
from app.services.ai.schemas import (
    AIAnalysisResult,
    AnalyzeRequest,
    EntityResult,
    KeywordAnalysisStats,
    SentimentResult,
)


class AIService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.post_repo = PostRepository(db)
        self.sentiment_repo = SentimentRepository(db)
        self.entity_repo = EntityRepository(db)

    # ── Single-post analysis ───────────────────────────────────────────────────

    async def analyze_post(
        self,
        post_id: uuid.UUID,
        run_sentiment: bool = True,
        run_ner: bool = True,
        run_embedding: bool = True,
        force: bool = False,
    ) -> AIAnalysisResult:
        result = AIAnalysisResult(post_id=post_id)

        post = await self.post_repo.get_by_id(post_id)
        if not post:
            result.errors.append(f"post {post_id} tidak ditemukan")
            return result

        text = post.cleaned_content or post.content or ""
        if not text.strip():
            result.errors.append("konten kosong, skip")
            return result

        # ── Sentiment ─────────────────────────────────────────────────────────
        if run_sentiment:
            try:
                sentiment_result = await self._run_sentiment(text)
                await self._save_sentiment(post_id, sentiment_result, force)
                result.sentiment = sentiment_result
            except Exception as exc:
                result.errors.append(f"sentiment error: {exc}")

        # ── NER ───────────────────────────────────────────────────────────────
        if run_ner:
            try:
                entity_results = await self._run_ner(text)
                await self._save_entities(post_id, entity_results, force)
                result.entities = entity_results
            except Exception as exc:
                result.errors.append(f"ner error: {exc}")

        # ── Embedding ─────────────────────────────────────────────────────────
        if run_embedding:
            try:
                embedding = await self._run_embedding(text)
                await self.post_repo.update_embedding(post_id, embedding)
                result.embedding_updated = True
            except Exception as exc:
                result.errors.append(f"embedding error: {exc}")

        return result

    # ── Keyword-level analysis ─────────────────────────────────────────────────

    async def analyze_keyword(self, request: AnalyzeRequest) -> KeywordAnalysisStats:
        stats = KeywordAnalysisStats(keyword_id=request.keyword_id)

        posts = await self.post_repo.list_processed_by_keyword(
            request.keyword_id,
            force=request.force_reanalyze,
        )
        stats.total_posts = len(posts)

        for post in posts:
            r = await self.analyze_post(
                post.id,
                run_sentiment=request.run_sentiment,
                run_ner=request.run_ner,
                run_embedding=request.run_embedding,
                force=request.force_reanalyze,
            )
            stats.analyzed += 1

            if r.sentiment:
                if r.sentiment.label == "positive":
                    stats.sentiment_positive += 1
                elif r.sentiment.label == "negative":
                    stats.sentiment_negative += 1
                else:
                    stats.sentiment_neutral += 1

            stats.entities_extracted += len(r.entities)

            if r.embedding_updated:
                stats.embeddings_generated += 1

            if r.errors:
                stats.errors.extend(r.errors)

        await self.db.commit()
        return stats

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _run_sentiment(self, text: str) -> SentimentResult:
        from app.services.ai.sentiment_analyzer import SentimentAnalyzer
        return SentimentAnalyzer.get_instance().analyze(text)

    async def _run_ner(self, text: str) -> list[EntityResult]:
        from app.services.ai.ner_extractor import NERExtractor
        return NERExtractor.get_instance().extract(text)

    async def _run_embedding(self, text: str) -> list[float]:
        from app.services.ai.embedding_generator import EmbeddingGenerator
        return EmbeddingGenerator.get_instance().generate(text)

    async def _save_sentiment(
        self,
        post_id: uuid.UUID,
        result: SentimentResult,
        force: bool,
    ) -> None:
        if force:
            await self.sentiment_repo.delete_by_post_id(post_id)
        existing = await self.sentiment_repo.get_by_post_id(post_id)
        if existing and not force:
            return
        sentiment = Sentiment(
            post_id=post_id,
            label=result.label,
            score=result.score,
            model_version=result.model_version,
        )
        await self.sentiment_repo.create(sentiment)

    async def _save_entities(
        self,
        post_id: uuid.UUID,
        results: list[EntityResult],
        force: bool,
    ) -> None:
        if force:
            await self.entity_repo.delete_by_post_id(post_id)
        existing = await self.entity_repo.count_by_post_id(post_id)
        if existing > 0 and not force:
            return
        entities = [
            Entity(
                post_id=post_id,
                text=e.text,
                entity_type=e.entity_type,
                start_char=e.start_char,
                end_char=e.end_char,
                score=e.score,
            )
            for e in results
        ]
        if entities:
            await self.entity_repo.bulk_create(entities)
