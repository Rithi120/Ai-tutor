"""Foreign languages (English, French, Spanish, other modern languages) and Latin.

A grammar point is learned rule → pattern → examples → mistakes, then practised in full
sentences. The target-language material always stays in the target language; only the
explanations are in the student's lesson language. Latin is read and translated rather
than spoken, so its variant adds a step-by-step translation part (verb first, then the
subject, then the cases).
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

LANGUAGES = SubjectStrategy(
    key="languages",
    name="Foreign Languages",
    priorities=(
        "using the structure correctly in whole sentences",
        "knowing when and why a form is used",
        "vocabulary in context",
        "avoiding the typical mistakes",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which grammar point or vocabulary the student will "
                   "learn and in which situations they will use it.", modes=ALL_MODES),
        LessonStep("rule", "Explain the grammar rule in simple words of the lesson language: when it is used, how "
                   "the form is built, and the one exception that matters most."),
        LessonStep("rule", "Explain the rule again differently: set a correct and a wrong sentence side by side "
                   "and point to the exact word that makes the difference.", modes=RETEACH_ONLY),
        LessonStep("structure", "Show the sentence pattern as a template with slots the student can copy (for "
                   "example 'linking word + subject + verb + rest'), one item per variant, word order visible."),
        LessonStep("example", "Give 3-5 clear example sentences in the target language (from the notes first), each "
                   "with its meaning in the student's language; name the words that show the rule.",
                   states=NOT_SECURE),
        LessonStep("example", "Give two very short everyday sentences that use only this rule, each with its "
                   "meaning in the student's language.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the typical mistakes with this point as items: label = the wrong sentence, "
                   "text = the corrected sentence and a short reason why."),
        LessonStep("terms", "List the vocabulary and irregular or special forms from the notes that this point "
                   "needs as items: label = the word or form in the target language exactly as in the notes, text = "
                   "meaning and a short usage hint.", modes=ALL_MODES),
        LessonStep("guided_exercise", "Give one short guided exercise (complete the gaps or combine two sentences "
                   "into one) with a hint per task in items and the correct sentences in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the rule, the sentence pattern and the exception to remember in two to four "
                   "short lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "rule", "structure", "example", "work"),
    question_types=("multiple_choice", "dropdown", "text", "checkboxes", "text", "ordering"),
    card_kinds=("term", "rule", "structure", "example"),
    rules=(
        "Keep every target-language word and sentence in the target language and give its meaning in the "
        "student's language.",
        "Teach rule, then pattern, then examples; never open with a list of exceptions.",
        "Use example sentences from the notes first; mark your own as additions.",
        "Practise in whole sentences, not isolated forms.",
    ),
    feedback_rule="Name the rule that was broken and point to the wrong word; let the student retry, and only "
                  "after the retry show the corrected sentence.",
    exam_style="Gap texts, sentence transformations and combining sentences, choosing the correct form, short "
               "translations, and short texts that use the structure.",
)


LATIN = SubjectStrategy(
    key="latin",
    name="Latin",
    priorities=(
        "recognising forms precisely",
        "analysing a sentence before translating it",
        "vocabulary with its principal parts",
        "a translation that is correct and natural",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which form, construction or text the student will be "
                   "able to recognise and translate after this lesson.", modes=ALL_MODES),
        LessonStep("rule", "Explain the grammar point in simple words: how to recognise the form or construction "
                   "(endings, signal words) and how it is translated."),
        LessonStep("rule", "Explain the rule again differently: compare two Latin forms that look alike and show "
                   "the one ending or signal word that tells them apart.", modes=RETEACH_ONLY),
        LessonStep("structure", "Show how the form or construction is built as a pattern (for example 'stem + "
                   "ending' or an ending table), one item per form."),
        LessonStep("example", "Give 3-5 Latin example sentences (from the notes first), each with a word-for-word "
                   "and a natural translation; name the words that show the rule.", states=NOT_SECURE),
        LessonStep("example", "Give two very short Latin sentences that use only this form, each with its "
                   "translation.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "List typical mistakes (confusing cases or tenses with the same ending, translating "
                   "word order literally) as items: label = the wrong translation, text = the correction and why."),
        LessonStep("terms", "List the vocabulary from the notes as items: label = the Latin word exactly as in the "
                   "notes with its principal parts or genitive and gender, text = its meanings.", modes=ALL_MODES),
        LessonStep("source_analysis", "Translate one sentence from the notes step by step in items: find the "
                   "predicate (verb) first, then the subject, then the case of each other word and what it depends "
                   "on, then build the translation."),
        LessonStep("guided_exercise", "Give one short Latin sentence to analyse and translate, with hints in items "
                   "(where is the verb? which case?) and the full translation in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up how to recognise the form, how to translate it and the analysis steps in two "
                   "to four short lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "rule", "structure", "work", "example", "source", "person"),
    question_types=("multiple_choice", "dropdown", "text", "checkboxes", "ordering", "text"),
    card_kinds=("term", "rule", "structure", "work", "person", "example"),
    rules=(
        "Always start a sentence analysis with the predicate.",
        "Name forms precisely: case, number, gender; person, tense, mood, voice.",
        "Translate word for word first, then into natural language.",
        "Keep Latin words in Latin and give their meaning in the student's language.",
    ),
    feedback_rule="Point to the form that was misidentified (ending, case or tense), let the student analyse it "
                  "again, and only then show the correct translation.",
    exam_style="Translating a passage, identifying and explaining forms and constructions, and short questions on "
               "the content and context of a text.",
)
