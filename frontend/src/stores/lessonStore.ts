import { create } from 'zustand'

export interface Checkpoint {
  title: string
  difficulty: string
  num_questions: number
  pass_threshold_percent: number
  question_mode: string
  markdown?: string | null
  quiz?: unknown[]
  sources?: string[]
}

export interface Lesson {
  lesson_id: string
  title: string
  topic: string
  status: string
  current_checkpoint: number
  total_checkpoints: number
  checkpoints: Checkpoint[]
}

interface LessonState {
  lessons: Lesson[]
  activeLessonId: string | null
  checkpointIndex: number
  setLessons: (l: Lesson[]) => void
  setActiveLesson: (id: string | null) => void
  setCheckpointIndex: (i: number) => void
  nextCheckpoint: () => void
  prevCheckpoint: () => void
}

export const useLessonStore = create<LessonState>((set, get) => ({
  lessons: [],
  activeLessonId: null,
  checkpointIndex: 0,
  setLessons: (lessons) => set({ lessons }),
  setActiveLesson: (id) => set({ activeLessonId: id, checkpointIndex: 0 }),
  setCheckpointIndex: (i) => set({ checkpointIndex: i }),
  nextCheckpoint: () => {
    const { checkpointIndex, lessons, activeLessonId } = get()
    const lesson = lessons.find((l) => l.lesson_id === activeLessonId)
    if (lesson && checkpointIndex < lesson.total_checkpoints - 1) {
      set({ checkpointIndex: checkpointIndex + 1 })
    }
  },
  prevCheckpoint: () => {
    const { checkpointIndex } = get()
    if (checkpointIndex > 0) {
      set({ checkpointIndex: checkpointIndex - 1 })
    }
  },
}))
