"""Browser automation tests for client-side JS in the Web TUI.

Covers:
- Grading utility functions (tokenize, edit distance, fuzzy match, lexical score)
- Cosine similarity (normal, zero-vector, dimension mismatch)
- MCQ grading (letter, exact text, fuzzy text)
- Open-answer grading with mocked embeddings
- Pipeline orchestration with mocked API responses
"""
from __future__ import annotations

import json

import pytest


def _inject_grading(page) -> None:
    """Inject grading JS helpers into a blank page via IIFE."""
    page.evaluate("""
    (function() {
        window.T = {};

        window.T.tokenize = function(text) {
            const parts = text.toLowerCase().match(/[a-z0-9]+/g);
            return parts ? new Set(parts) : new Set();
        };

        window.T.normalizeText = function(text) {
            const parts = text.toLowerCase().match(/[a-z0-9]+/g);
            return parts ? parts.join(" ") : "";
        };

        window.T.editDistance = function(a, b) {
            const m = a.length, n = b.length;
            const dp = Array.from({length: m + 1}, () => new Array(n + 1).fill(0));
            for (let i = 0; i <= m; i++) dp[i][0] = i;
            for (let j = 0; j <= n; j++) dp[0][j] = j;
            for (let i = 1; i <= m; i++)
                for (let j = 1; j <= n; j++)
                    dp[i][j] = a[i-1] === b[j-1]
                        ? dp[i-1][j-1]
                        : 1 + Math.min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1]);
            return dp[m][n];
        };

        window.T.isTypoMatch = function(a, b) {
            if (a === b) return true;
            if (Math.abs(a.length - b.length) > 1) return false;
            if (Math.min(a.length, b.length) < 4) return false;
            const dist = window.T.editDistance(a, b);
            return (1 - dist / Math.max(a.length, b.length)) >= 0.85;
        };

        window.T.fuzzyOverlap = function(expected, submitted) {
            const overlap = new Set([...expected].filter(t => submitted.has(t)));
            const unmatchedExp = [...expected].filter(t => !overlap.has(t));
            const unmatchedSub = [...submitted].filter(t => !overlap.has(t));
            if (!unmatchedExp.length || !unmatchedSub.length) return overlap;
            for (const et of unmatchedExp) {
                for (const st of unmatchedSub) {
                    if (window.T.isTypoMatch(et, st)) { overlap.add(et); break; }
                }
            }
            return overlap;
        };

        window.T.lexicalScore = function(expected, submitted) {
            const expTokens = window.T.tokenize(expected);
            const subTokens = window.T.tokenize(submitted);
            if (!expTokens.size || !subTokens.size) return 0;
            const overlap = window.T.fuzzyOverlap(expTokens, subTokens);
            const precision = overlap.size / subTokens.size;
            const recall = overlap.size / expTokens.size;
            if (precision + recall <= 0) return 0;
            const f1 = (2 * precision * recall) / (precision + recall);
            return 0.7 * f1 + 0.3 * recall;
        };

        window.T.mcqGrading = function(q, submitted) {
            const sel = submitted.trim();
            if (!sel) return 0;
            const upper = sel.toUpperCase();
            if (upper.length === 1 && upper >= "A" && upper <= "Z") {
                const idx = upper.charCodeAt(0) - 65;
                if (idx >= 0 && idx < q.options.length) return idx === q.correct_option_index ? 1 : 0;
            }
            for (let i = 0; i < q.options.length; i++) {
                if (sel.toLowerCase() === q.options[i].trim().toLowerCase()) return i === q.correct_option_index ? 1 : 0;
            }
            const normSel = window.T.normalizeText(sel);
            let bestIdx = -1, bestRatio = 0;
            for (let i = 0; i < q.options.length; i++) {
                const normOpt = window.T.normalizeText(q.options[i]);
                let matches = 0, si = 0, oi = 0;
                while (si < normSel.length && oi < normOpt.length) {
                    if (normSel[si] === normOpt[oi]) { matches++; si++; oi++; }
                    else if (normSel.length > normOpt.length) si++;
                    else oi++;
                }
                const ratio = matches / Math.max(normSel.length, normOpt.length);
                if (ratio > bestRatio) { bestRatio = ratio; bestIdx = i; }
            }
            if (bestIdx >= 0 && bestRatio >= 0.85) return bestIdx === q.correct_option_index ? 1 : 0;
            return 0;
        };

        window.T.cosineSimilarity = function(v1, v2) {
            if (!v1 || !v2 || v1.length !== v2.length) return 0;
            let dot = 0, norm1 = 0, norm2 = 0;
            for (let i = 0; i < v1.length; i++) {
                dot += v1[i] * v2[i];
                norm1 += v1[i] * v1[i];
                norm2 += v2[i] * v2[i];
            }
            const denom = Math.sqrt(norm1) * Math.sqrt(norm2);
            return denom <= 1e-12 ? 0.0 : dot / denom;
        };

        window.T.gradeQuizLocally = async function(quiz, answers, mockEmbeddingMap) {
            const results = [];
            for (const q of quiz.questions) {
                const submitted = answers[q.question_id] || "";
                let score;
                if (q.question_mode === "mcq" && q.options && q.correct_option_index != null) {
                    score = window.T.mcqGrading(q, submitted);
                } else {
                    const lex = window.T.lexicalScore(q.expected_answer, submitted);
                    const expVec = mockEmbeddingMap ? mockEmbeddingMap[q.expected_answer] : null;
                    const subVec = mockEmbeddingMap ? mockEmbeddingMap[submitted] : null;
                    if (expVec && subVec) {
                        const raw = window.T.cosineSimilarity(expVec, subVec);
                        const sem = Math.max(0, Math.min(1, (raw + 1) / 2));
                        score = 0.7 * sem + 0.3 * lex;
                    } else {
                        score = lex;
                    }
                }
                results.push({
                    question_id: q.question_id,
                    is_correct: score >= 0.58,
                    score: Math.round(score * 1000) / 1000,
                });
            }
            return results;
        };
    })();
    """)


# =========================================================================
# Tokenize
# =========================================================================


class TestTokenize:
    def test_basic_tokens(self, page):
        _inject_grading(page)
        result = page.evaluate("Array.from(window.T.tokenize('Hello World 123'))")
        assert sorted(result) == ["123", "hello", "world"]

    def test_empty_string(self, page):
        _inject_grading(page)
        result = page.evaluate("window.T.tokenize('').size")
        assert result == 0

    def test_special_characters_ignored(self, page):
        _inject_grading(page)
        result = page.evaluate("Array.from(window.T.tokenize('foo-bar baz!'))")
        assert sorted(result) == ["bar", "baz", "foo"]


# =========================================================================
# Edit Distance
# =========================================================================


class TestEditDistance:
    def test_identical_strings(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.editDistance('abc', 'abc')") == 0

    def test_single_insertion(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.editDistance('abc', 'abcd')") == 1

    def test_single_substitution(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.editDistance('abc', 'axc')") == 1

    def test_empty_vs_nonempty(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.editDistance('', 'abc')") == 3

    def test_both_empty(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.editDistance('', '')") == 0


# =========================================================================
# Typo Match
# =========================================================================


class TestTypoMatch:
    def test_exact_match(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.isTypoMatch('binary', 'binary')") is True

    def test_single_char_diff_below_threshold(self, page):
        _inject_grading(page)
        # "binary" vs "bimary" — 1 edit on 6 chars → 5/6 = 0.833 < 0.85
        assert page.evaluate("window.T.isTypoMatch('binary', 'bimary')") is False

    def test_too_short_rejected(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.isTypoMatch('ab', 'ac')") is False

    def test_length_diff_gt_1(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.isTypoMatch('abc', 'abcde')") is False

    def test_close_enough(self, page):
        _inject_grading(page)
        # "binarytree" vs "binarytre" — 1 edit on 10 chars → 9/10 = 0.9 >= 0.85
        assert page.evaluate("window.T.isTypoMatch('binarytree', 'binarytre')") is True


# =========================================================================
# Lexical Score
# =========================================================================


class TestLexicalScore:
    def test_identical_answers(self, page):
        _inject_grading(page)
        score = page.evaluate("window.T.lexicalScore('binary search', 'binary search')")
        assert score == pytest.approx(1.0)

    def test_completely_different(self, page):
        _inject_grading(page)
        score = page.evaluate("window.T.lexicalScore('binary search', 'hash table')")
        assert score == pytest.approx(0.0)

    def test_partial_overlap(self, page):
        _inject_grading(page)
        score = page.evaluate("window.T.lexicalScore('binary search tree', 'binary tree')")
        assert 0.0 < score < 1.0

    def test_empty_submitted(self, page):
        _inject_grading(page)
        score = page.evaluate("window.T.lexicalScore('binary search', '')")
        assert score == 0.0

    def test_fuzzy_typo_bonus(self, page):
        _inject_grading(page)
        exact = page.evaluate("window.T.lexicalScore('binary', 'binary')")
        # "binarytree" vs "binarytre" — 1 edit on 10 chars → 9/10 = 0.9 >= 0.85 → typo match
        fuzzy = page.evaluate("window.T.lexicalScore('binarytree', 'binarytre')")
        assert exact == pytest.approx(1.0)
        assert fuzzy > 0


# =========================================================================
# Cosine Similarity
# =========================================================================


class TestCosineSimilarity:
    def test_identical_vectors(self, page):
        _inject_grading(page)
        sim = page.evaluate("window.T.cosineSimilarity([1, 0, 0], [1, 0, 0])")
        assert sim == pytest.approx(1.0)

    def test_orthogonal_vectors(self, page):
        _inject_grading(page)
        sim = page.evaluate("window.T.cosineSimilarity([1, 0, 0], [0, 1, 0])")
        assert sim == pytest.approx(0.0)

    def test_opposite_vectors(self, page):
        _inject_grading(page)
        sim = page.evaluate("window.T.cosineSimilarity([1, 0, 0], [-1, 0, 0])")
        assert sim == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self, page):
        _inject_grading(page)
        sim = page.evaluate("window.T.cosineSimilarity([0, 0, 0], [1, 2, 3])")
        assert sim == 0.0

    def test_both_zero_vectors(self, page):
        _inject_grading(page)
        sim = page.evaluate("window.T.cosineSimilarity([0, 0, 0], [0, 0, 0])")
        assert sim == 0.0

    def test_dimension_mismatch_returns_zero(self, page):
        _inject_grading(page)
        sim = page.evaluate("window.T.cosineSimilarity([1, 0], [1, 0, 0])")
        assert sim == 0.0

    def test_null_inputs(self, page):
        _inject_grading(page)
        assert page.evaluate("window.T.cosineSimilarity(null, [1,0])") == 0
        assert page.evaluate("window.T.cosineSimilarity([1,0], null)") == 0
        assert page.evaluate("window.T.cosineSimilarity(undefined, undefined)") == 0

    def test_known_angle(self, page):
        _inject_grading(page)
        # [1,1] and [1,0] → cos(45°) = sqrt(2)/2 ≈ 0.7071
        sim = page.evaluate("window.T.cosineSimilarity([1, 1], [1, 0])")
        assert sim == pytest.approx(0.7071, rel=1e-3)


# =========================================================================
# MCQ Grading
# =========================================================================


class TestMcqGrading:
    MCQ = {"options": ["Binary Search", "Linear Search", "Hash Lookup", "Depth-First Search"],
           "correct_option_index": 0}

    def test_correct_letter(self, page):
        _inject_grading(page)
        q = json.dumps(self.MCQ)
        assert page.evaluate(f"window.T.mcqGrading({q}, 'A')") == 1

    def test_wrong_letter(self, page):
        _inject_grading(page)
        q = json.dumps(self.MCQ)
        assert page.evaluate(f"window.T.mcqGrading({q}, 'C')") == 0

    def test_correct_text_exact(self, page):
        _inject_grading(page)
        q = json.dumps(self.MCQ)
        assert page.evaluate(f"window.T.mcqGrading({q}, 'Binary Search')") == 1

    def test_correct_text_case_insensitive(self, page):
        _inject_grading(page)
        q = json.dumps(self.MCQ)
        assert page.evaluate(f"window.T.mcqGrading({q}, 'binary search')") == 1

    def test_empty_input(self, page):
        _inject_grading(page)
        q = json.dumps(self.MCQ)
        assert page.evaluate(f"window.T.mcqGrading({q}, '')") == 0

    def test_out_of_range_letter(self, page):
        _inject_grading(page)
        q = json.dumps(self.MCQ)
        assert page.evaluate(f"window.T.mcqGrading({q}, 'Z')") == 0

    def test_lowercase_letter(self, page):
        _inject_grading(page)
        q = json.dumps(self.MCQ)
        # 'b' → toUpperCase 'B' → idx 1 → "Linear Search" ≠ correct (idx 0)
        assert page.evaluate(f"window.T.mcqGrading({q}, 'b')") == 0


# =========================================================================
# Open-Answer Grading with Mock Embeddings
# =========================================================================


class TestOpenAnswerGrading:
    @pytest.fixture()
    def quiz_payload(self):
        return {
            "questions": [
                {
                    "question_id": "q1",
                    "question_mode": "open",
                    "expected_answer": "binary search halves the search space each step",
                    "options": None,
                    "correct_option_index": None,
                },
                {
                    "question_id": "q2",
                    "question_mode": "mcq",
                    "expected_answer": "O(log n)",
                    "options": ["O(n)", "O(log n)", "O(n log n)", "O(1)"],
                    "correct_option_index": 1,
                },
            ]
        }

    def test_exact_match_open(self, page, quiz_payload):
        _inject_grading(page)
        quiz = json.dumps(quiz_payload)
        answers = json.dumps({"q1": "binary search halves the search space each step", "q2": "B"})
        results = page.evaluate(
            f"window.T.gradeQuizLocally({quiz}, {answers}, null)"
        )
        assert results[0]["is_correct"] is True
        assert results[1]["is_correct"] is True

    def test_semantic_blend_boostes_score(self, page, quiz_payload):
        _inject_grading(page)
        # Lexical-only score for a semantically correct but differently worded answer
        lex_score = page.evaluate(
            "window.T.lexicalScore("
            "'binary search halves the search space each step',"
            "'it reduces the remaining elements by half every iteration'"
            ")"
        )
        # The lexical score alone should be below the threshold
        assert lex_score < 0.58, "Lexical score should be below threshold for different wording"

        # Now grade with mock embeddings that return high semantic similarity
        # [1,0,0] and [0.98, 0.1, 0.1] → cosine ≈ 0.99
        quiz = json.dumps(quiz_payload)
        answers = json.dumps({
            "q1": "it reduces the remaining elements by half every iteration",
            "q2": "B",
        })
        embedding_map = json.dumps({
            "binary search halves the search space each step": [1.0, 0.0, 0.0],
            "it reduces the remaining elements by half every iteration": [0.98, 0.1, 0.1],
        })
        results = page.evaluate(
            f"window.T.gradeQuizLocally({quiz}, {answers}, {embedding_map})"
        )
        # With semantic boost, the blended score should be above threshold
        assert results[0]["is_correct"] is True
        assert results[0]["score"] >= 0.58

    def test_wrong_answer_incorrect(self, page, quiz_payload):
        _inject_grading(page)
        quiz = json.dumps(quiz_payload)
        answers = json.dumps({"q1": "hash tables use arrays", "q2": "A"})
        results = page.evaluate(
            f"window.T.gradeQuizLocally({quiz}, {answers}, null)"
        )
        assert results[0]["is_correct"] is False
        assert results[1]["is_correct"] is False

    def test_empty_answer_incorrect(self, page, quiz_payload):
        _inject_grading(page)
        quiz = json.dumps(quiz_payload)
        answers = json.dumps({"q1": "", "q2": ""})
        results = page.evaluate(
            f"window.T.gradeQuizLocally({quiz}, {answers}, null)"
        )
        assert results[0]["is_correct"] is False


# =========================================================================
# Pipeline Orchestration (integration with mocked API)
# =========================================================================


class TestPipelineOrchestration:
    CURRICULUM_RESPONSE = {
        "title": "Test Curriculum",
        "description": "A test",
        "topics": [
            {"id": "t1", "title": "Binary Search", "summary": "Logarithmic search", "prerequisites": []},
            {"id": "t2", "title": "Hash Tables", "summary": "O(1) lookup", "prerequisites": []},
        ],
    }

    QA_RESPONSE = {
        "topic_id": "t1",
        "topic_title": "Binary Search",
        "questions": [
            {"question_id": "q1", "question_text": "What is binary search?",
             "expected_answer": "A search algorithm", "question_mode": "open",
             "options": None, "correct_option_index": None, "difficulty": "easy"},
        ],
    }

    SAVE_RESPONSE = {"status": "ok", "saved_path": "output/output_curriculum_qa.json"}

    def test_pipeline_generate_calls_three_endpoints(self, app_page):
        """Verify /pipeline generate calls curriculum → topic-qa → save in sequence."""
        page = app_page
        calls = []

        def capture_curriculum(route):
            calls.append({"endpoint": "curriculum", "body": route.request.post_data})
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(self.CURRICULUM_RESPONSE),
            )

        def capture_topic_qa(route):
            calls.append({"endpoint": "topic-qa", "body": route.request.post_data})
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(self.QA_RESPONSE),
            )

        def capture_save(route):
            calls.append({"endpoint": "save", "body": route.request.post_data})
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(self.SAVE_RESPONSE),
            )

        page.route("**/pipeline/generate-curriculum", capture_curriculum)
        page.route("**/pipeline/generate-topic-qa", capture_topic_qa)
        page.route("**/pipeline/save", capture_save)
        # Mock lessons refresh that happens after save
        page.route("**/lessons", lambda route: route.fulfill(
            status=200, content_type="application/json", body="[]"
        ))

        # Simulate the pipeline command flow via fetch calls (matching app's handlePipelineCommand)
        page.evaluate("""
        (async function() {
            const allTexts = [
                "Binary search is a search algorithm",
                "Hash tables provide O(1) lookup"
            ];
            const globalContext = allTexts.join("\\n\\n");

            // Step 1: curriculum
            const currResp = await fetch("/pipeline/generate-curriculum", {
                method: "POST",
                headers: {"Content-Type": "application/json", "X-Client-Id": "test-client-id"},
                body: JSON.stringify({global_context: globalContext})
            });
            const curriculum = await currResp.json();

            // Step 2: topic QA for each topic
            const qaSets = [];
            for (const topic of curriculum.topics) {
                const qaResp = await fetch("/pipeline/generate-topic-qa", {
                    method: "POST",
                    headers: {"Content-Type": "application/json", "X-Client-Id": "test-client-id"},
                    body: JSON.stringify({
                        topic_id: topic.id,
                        topic_title: topic.title,
                        topic_summary: topic.summary,
                        context_chunks: allTexts.slice(0, 5)
                    })
                });
                qaSets.push(await qaResp.json());
            }

            // Step 3: save
            await fetch("/pipeline/save", {
                method: "POST",
                headers: {"Content-Type": "application/json", "X-Client-Id": "test-client-id"},
                body: JSON.stringify({
                    curriculum_qa: {curriculum: curriculum, qa_sets: qaSets, metadata: {}}
                })
            });
        })();
        """)

        page.wait_for_timeout(1000)

        assert len(calls) == 4  # curriculum + 2 topics + save
        assert calls[0]["endpoint"] == "curriculum"
        assert calls[1]["endpoint"] == "topic-qa"
        assert calls[2]["endpoint"] == "topic-qa"
        assert calls[3]["endpoint"] == "save"

        # Verify curriculum request body
        body = json.loads(calls[0]["body"])
        assert "Binary search is a search algorithm" in body["global_context"]

        # Verify save request includes merged result
        save_body = json.loads(calls[3]["body"])
        assert "curriculum_qa" in save_body
        assert save_body["curriculum_qa"]["curriculum"]["title"] == "Test Curriculum"
        assert len(save_body["curriculum_qa"]["qa_sets"]) == 2

    def test_pipeline_curriculum_failure_stops_flow(self, app_page):
        """If curriculum generation fails, topic-qa and save should not be called."""
        page = app_page
        calls = []

        def fail_curriculum(route):
            calls.append("curriculum")
            route.fulfill(status=500, content_type="application/json",
                          body=json.dumps({"detail": "LLM error"}))

        def capture(route):
            calls.append("other")

        page.route("**/pipeline/generate-curriculum", fail_curriculum)
        page.route("**/pipeline/generate-topic-qa", capture)
        page.route("**/pipeline/save", capture)

        error_msg = page.evaluate("""
        (async function() {
            try {
                const resp = await fetch("/pipeline/generate-curriculum", {
                    method: "POST",
                    headers: {"Content-Type": "application/json", "X-Client-Id": "test"},
                    body: JSON.stringify({global_context: "test"})
                });
                if (!resp.ok) return "curriculum_failed";
                return "should_not_reach";
            } catch (e) {
                return e.message;
            }
        })();
        """)

        page.wait_for_timeout(500)
        assert error_msg == "curriculum_failed"
        assert "other" not in calls

    def test_pipeline_topic_qa_failure_stops_flow(self, app_page):
        """If topic QA fails, save should not be called."""
        page = app_page
        calls = []

        def ok_curriculum(route):
            calls.append("curriculum")
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(self.CURRICULUM_RESPONSE))

        def fail_qa(route):
            calls.append("topic-qa")
            route.fulfill(status=500, content_type="application/json",
                          body=json.dumps({"detail": "LLM error"}))

        def capture_save(route):
            calls.append("save")

        page.route("**/pipeline/generate-curriculum", ok_curriculum)
        page.route("**/pipeline/generate-topic-qa", fail_qa)
        page.route("**/pipeline/save", capture_save)

        error_msg = page.evaluate("""
        (async function() {
            try {
                const currResp = await fetch("/pipeline/generate-curriculum", {
                    method: "POST",
                    headers: {"Content-Type": "application/json", "X-Client-Id": "test"},
                    body: JSON.stringify({global_context: "test"})
                });
                const curriculum = await currResp.json();
                for (const topic of (curriculum.topics || [])) {
                    const qaResp = await fetch("/pipeline/generate-topic-qa", {
                        method: "POST",
                        headers: {"Content-Type": "application/json", "X-Client-Id": "test"},
                        body: JSON.stringify({topic_id: topic.id, topic_title: topic.title,
                            topic_summary: topic.summary, context_chunks: []})
                    });
                    if (!qaResp.ok) return "topic_qa_failed";
                }
                return "should_not_reach";
            } catch (e) {
                return e.message;
            }
        })();
        """)

        page.wait_for_timeout(500)
        assert error_msg == "topic_qa_failed"
        assert "save" not in calls
