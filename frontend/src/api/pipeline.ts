import { api } from './client'

export interface Curriculum {
  title: string
  description: string
  topics: {
    id: string
    title: string
    summary: string
    prerequisites: string[]
  }[]
}

export interface TopicQASet {
  topic_id: string
  topic_title: string
  questions: {
    question: string
    answer: string
    difficulty: string
    source_context: string | null
  }[]
}

export async function generateCurriculum(globalContext: string, maxTopics?: number) {
  return api.post<Curriculum>('/pipeline/generate-curriculum', {
    global_context: globalContext,
    max_topics: maxTopics ?? 6,
  })
}

export async function generateTopicQA(
  topicId: string,
  topicTitle: string,
  topicSummary: string,
  contextChunks: string[],
  qaPerTopic?: number,
) {
  return api.post<TopicQASet>('/pipeline/generate-topic-qa', {
    topic_id: topicId,
    topic_title: topicTitle,
    topic_summary: topicSummary,
    context_chunks: contextChunks,
    qa_per_topic: qaPerTopic ?? 5,
  })
}

export async function savePipeline(curriculumQA: {
  curriculum: Curriculum
  qa_sets: TopicQASet[]
  metadata?: { generated_at: string; device_used: string }
}) {
  return api.post<{ status: string; saved_path: string }>('/pipeline/save', {
    curriculum_qa: curriculumQA,
  })
}
