import os
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_voice_accessibility_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import app as application  # noqa: E402
from learnova.translations import frontend_catalog  # noqa: E402


class VoiceAccessibilityTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def register(self, language="en"):
        return self.client.post(
            "/register",
            data={
                "username": "voice_student",
                "email": "voice@example.com",
                "password": "correct-horse-battery",
                "language": language,
            },
            follow_redirects=False,
        )

    def test_microphone_controls_render_for_chat_and_written_quiz_answers(self):
        self.register()
        html = self.client.get("/").data
        self.assertIn(b'id="answerMicrophoneControl"', html)
        self.assertIn(b'data-speech-target="#writtenAnswer"', html)
        self.assertIn(b'id="chatMicrophoneControl"', html)
        self.assertIn(b'data-speech-target="#chatInput"', html)
        self.assertIn(b'data-speech-record aria-label="Start recording"', html)
        self.assertIn(b'data-speech-stop aria-label="Stop recording"', html)
        self.assertIn(b'data-speech-cancel aria-label="Cancel recording"', html)
        self.assertIn(b'aria-pressed="false"', html)
        self.assertIn(b'role="status" aria-live="polite"', html)

    def test_listen_controls_cover_lesson_question_hint_and_feedback(self):
        self.register()
        html = self.client.get("/").data
        for control_id, target in (
            (b"explanationListenControl", b"#explanation"),
            (b"questionListenControl", b"#questionPrompt"),
            (b"hintListenControl", b"#hint"),
            (b"feedbackListenControl", b"#feedbackSpeechText"),
        ):
            self.assertIn(b'id="' + control_id + b'"', html)
            self.assertIn(b'data-speech-target="' + target + b'"', html)
        self.assertIn(b'data-speech-play aria-label="Play"', html)
        self.assertIn(b'data-speech-pause aria-label="Pause" disabled', html)
        self.assertIn(b'data-speech-resume aria-label="Resume" disabled', html)
        self.assertIn(b'data-speech-stop-playback aria-label="Stop" disabled', html)
        self.assertIn(b'data-speech-rate aria-label="Playback speed"', html)
        self.assertNotIn(b"autoplay", html.lower())

    def test_content_language_is_separate_and_unsupported_messages_are_translated(self):
        self.register(language="de")
        html = self.client.get("/").data
        self.assertIn(b'window.LEARNOVA_LANGUAGE = "de"', html)
        self.assertIn(b'window.LEARNOVA_CONTENT_LANGUAGE = "de-DE"', html)
        german = frontend_catalog("de")
        self.assertEqual(
            german["speechRecognitionUnsupported"],
            "Die Spracherkennung wird in diesem Browser nicht unterstützt. Du kannst weiter tippen.",
        )
        self.assertEqual(
            german["speechSynthesisUnsupported"],
            "Die Sprachwiedergabe wird in diesem Browser nicht unterstützt.",
        )
        self.assertIn(b'"speechRecognitionUnsupported":', html)
        self.assertIn("Mikrofon verwenden".encode(), html)

    def test_speech_to_text_never_submits_or_persists_raw_audio(self):
        source = Path("static/js/speech-to-text.js").read_text(encoding="utf-8")
        self.assertIn("target.value =", source)
        self.assertIn("target.focus()", source)
        self.assertNotIn("requestSubmit", source)
        self.assertNotIn(".submit(", source)
        self.assertNotIn("MediaRecorder", source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("fetch(", source)
        self.assertNotIn('dispatchEvent(new Event("input"', source)
        self.assertIn('addEventListener("click", () => startRecording(control))', source)
        self.assertIn('recognition.lang = window.LEARNOVA_CONTENT_LANGUAGE', source)
        self.assertIn('window.addEventListener("pagehide"', source)
        self.assertIn('document.addEventListener("visibilitychange"', source)

    def test_playback_is_single_item_and_cancels_during_navigation(self):
        source = Path("static/js/text-to-speech.js").read_text(encoding="utf-8")
        self.assertIn("if (active) stopActive", source)
        self.assertIn("synthesis?.cancel()", source)
        self.assertIn('window.addEventListener("pagehide", () => stopActive())', source)
        self.assertIn('window.addEventListener("beforeunload", () => stopActive())', source)
        self.assertIn('utterance.lang = window.LEARNOVA_CONTENT_LANGUAGE', source)
        self.assertIn("synthesis.pause()", source)
        self.assertIn("synthesis.resume()", source)
        self.assertNotIn("autoplay", source.lower())

    def test_reusable_controls_cover_recall_and_exam_fields_without_breaking_forms(self):
        section = Path("templates/section_learning.html").read_text(encoding="utf-8")
        exam = Path("templates/exam_take.html").read_text(encoding="utf-8")
        index = Path("templates/index.html").read_text(encoding="utf-8")
        self.assertIn("microphone_control('#recallAnswer'", section)
        self.assertIn("listen_control('#recallPrompt'", section)
        self.assertIn('button[type="submit"]', section)
        self.assertIn("microphone_control('#examAnswer'", exam)
        self.assertIn("listen_control('#examQuestionPrompt'", exam)
        self.assertIn('class="primary-button" type="submit"', exam)
        self.assertIn('id="answerForm"', index)
        self.assertIn('class="primary-button" type="submit"', index)
        self.assertIn('id="chatForm"', index)
        self.assertIn('button type="submit"', index)

    def test_focus_and_reduced_motion_accessibility_styles_exist(self):
        styles = Path("static/styles.css").read_text(encoding="utf-8")
        self.assertIn(".speech-input-control button:focus-visible", styles)
        self.assertIn(".listen-control select:focus-visible", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("animation: none", styles)


if __name__ == "__main__":
    unittest.main()
