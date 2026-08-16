from __future__ import annotations

import unittest

from engine.pipeline.models import Curriculum, CurriculumQAOutput, QuestionAnswer, Topic, TopicQASet
from engine.quiz.quiz_backend import (
    QuizBuildRequest,
    QuizGradeRequest,
    QuizSubmissionAnswer,
    build_quiz,
    grade_quiz,
)
from engine.quiz.quiz_intent import parse_quiz_intent


def _sample_output() -> CurriculumQAOutput:
    return CurriculumQAOutput(
        curriculum=Curriculum(
            title="T",
            description="D",
            topics=[Topic(id="topic_01", title="Trees", summary="S", prerequisites=[])],
        ),
        qa_sets=[
            TopicQASet(
                topic_id="topic_01",
                topic_title="Trees",
                questions=[
                    QuestionAnswer(question="Q1", answer="A1", difficulty="medium"),
                    QuestionAnswer(question="Q2", answer="A2", difficulty="medium"),
                    QuestionAnswer(question="Q3", answer="A3", difficulty="medium"),
                    QuestionAnswer(question="Q4", answer="A4", difficulty="medium"),
                ],
            )
        ],
    )


class QuizModesTests(unittest.TestCase):
    def test_mcq_build_and_grade_letter(self) -> None:
        quiz = build_quiz(
            qa_output=_sample_output(),
            request=QuizBuildRequest(topic="trees", difficulty="medium", num_questions=1, question_mode="mcq"),
        )
        q = quiz.questions[0]
        self.assertEqual(q.question_mode, "mcq")
        self.assertIsNotNone(q.options)
        self.assertEqual(len(q.options or []), 4)
        self.assertIsNotNone(q.correct_option_index)
        correct_letter = chr(ord("A") + int(q.correct_option_index))
        result = grade_quiz(
            quiz=quiz,
            request=QuizGradeRequest(
                quiz_id=quiz.quiz_id,
                answers=[QuizSubmissionAnswer(question_id=q.question_id, answer=correct_letter)],
            ),
        )
        self.assertEqual(result.correct_answers, 1)
        self.assertEqual(result.results[0].score, 1.0)

    def test_open_mode_default(self) -> None:
        quiz = build_quiz(
            qa_output=_sample_output(),
            request=QuizBuildRequest(topic="trees", difficulty="medium", num_questions=1),
        )
        q = quiz.questions[0]
        self.assertEqual(q.question_mode, "open")
        self.assertIsNone(q.options)
        self.assertIsNone(q.correct_option_index)

    def test_intent_parser_detects_mcq(self) -> None:
        parsed = parse_quiz_intent("give me 3 multiple choice questions on trees")
        self.assertEqual(parsed.question_mode, "mcq")

    def test_open_answer_typo_is_accepted(self) -> None:
        quiz = build_quiz(
            qa_output=CurriculumQAOutput(
                curriculum=Curriculum(
                    title="T",
                    description="D",
                    topics=[Topic(id="topic_01", title="Trees", summary="S", prerequisites=[])],
                ),
                qa_sets=[
                    TopicQASet(
                        topic_id="topic_01",
                        topic_title="Trees",
                        questions=[
                            QuestionAnswer(
                                question="What structure is this?",
                                answer="balanced search tree",
                                difficulty="medium",
                            )
                        ],
                    )
                ],
            ),
            request=QuizBuildRequest(topic="trees", difficulty="medium", num_questions=1, question_mode="open"),
        )
        q = quiz.questions[0]
        result = grade_quiz(
            quiz=quiz,
            request=QuizGradeRequest(
                quiz_id=quiz.quiz_id,
                answers=[QuizSubmissionAnswer(question_id=q.question_id, answer="balnced serch tree")],
            ),
        )
        self.assertEqual(result.correct_answers, 1)


if __name__ == "__main__":
    unittest.main()
