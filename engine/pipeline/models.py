from __future__ import annotations

from pydantic import BaseModel, Field


class Topic(BaseModel):
    id: str = Field(description="Stable topic id, e.g. topic_01")
    title: str
    summary: str
    prerequisites: list[str] = Field(default_factory=list)


class Curriculum(BaseModel):
    title: str
    description: str
    topics: list[Topic]


class QuestionAnswer(BaseModel):
    question: str
    answer: str
    difficulty: str
    source_context: str | None = None


class TopicQASet(BaseModel):
    topic_id: str
    topic_title: str
    questions: list[QuestionAnswer]


class CurriculumQAOutput(BaseModel):
    curriculum: Curriculum
    qa_sets: list[TopicQASet]
    metadata: dict[str, object] | None = None
