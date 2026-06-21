"""
Agent orchestrator: routes user questions through the agent pipeline.

Flow: User Question → Planner → Search → Trend → Sentiment → Entity → Summary → Response
"""


class AgentOrchestrator:
    async def run(self, question: str, project_id: str) -> dict:
        # TODO: Phase 6 - implement multi-agent pipeline
        raise NotImplementedError
