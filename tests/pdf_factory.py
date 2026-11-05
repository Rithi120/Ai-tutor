"""Synthetic documents for the upload-robustness tests (no student data, no fonts needed).

Ported from the document-reliability probes (R:/ai_tutor_suite_tmp/prod_docrel/docs_factory.py).
Every damaged document is made from a valid one, so the damage is the only difference.
Not a test module (no `test_` prefix): pytest does not collect it.
"""

from __future__ import annotations

import io
import random
import re

import pymupdf  # pyright: ignore[reportMissingImports]
from PIL import Image, ImageDraw, ImageFont

KANT = ("Immanuel Kant begruendet die Moral nicht mit den Folgen, sondern mit der Pflicht. Der "
        "kategorische Imperativ verlangt, nur nach der Maxime zu handeln, von der man zugleich wollen "
        "kann, dass sie ein allgemeines Gesetz werde. Der Mensch darf nie nur als Mittel gebraucht "
        "werden, sondern immer zugleich als Zweck an sich selbst. Pflicht ist die Notwendigkeit einer "
        "Handlung aus Achtung vor dem Gesetz.")
UTIL = ("Der Utilitarismus bewertet Handlungen nach ihren Folgen. Eine Handlung ist moralisch richtig, "
        "wenn sie das groesste Glueck der groessten Zahl bewirkt. Jeremy Bentham schlug dafuer einen "
        "Kalkuel vor, mit dem Freude und Leid gemessen werden sollen. John Stuart Mill unterschied "
        "spaeter hoehere und niedere Freuden und verteidigte damit den Utilitarismus gegen Kritik.")
PLATON = ("Im Hoehlengleichnis beschreibt Platon Gefangene, die nur Schattenbilder an der Wand sehen und "
          "sie fuer die Wirklichkeit halten. Einer wird befreit, steigt aus der Hoehle und erkennt die "
          "Sonne als Idee des Guten. Die Ideenlehre unterscheidet die sichtbare Welt der Abbilder von der "
          "Welt der unveraenderlichen Urbilder, die nur das Denken erfassen kann.")
RAWLS = ("John Rawls fragt, welche Grundsaetze der Gerechtigkeit Menschen im Urzustand waehlen wuerden. "
         "Hinter dem Schleier des Nichtwissens kennt niemand seine eigene Stellung in der Gesellschaft. "
         "Deshalb waehlen alle das Differenzprinzip: Ungleichheiten sind nur erlaubt, wenn sie den am "
         "schlechtesten Gestellten zugutekommen. Fairness ist der Kern dieser Theorie.")
JONAS = ("Hans Jonas entwirft eine Verantwortungsethik fuer das technologische Zeitalter. Die Heuristik "
         "der Furcht verlangt, bei neuen Techniken zuerst die schlimmsten moeglichen Folgen zu bedenken. "
         "Sein Imperativ lautet: Handle so, dass die Wirkungen deiner Handlung vertraeglich sind mit der "
         "Permanenz echten menschlichen Lebens auf Erden. Verantwortung gilt auch kuenftigen Generationen.")
WERTE = ("Werte sind Vorstellungen davon, was wuenschenswert ist, etwa Freiheit, Gerechtigkeit oder "
         "Ehrlichkeit. Normen sind Regeln, die aus Werten ein Sollen fuer das Handeln ableiten. Moralische "
         "Normen gelten ohne Strafe von aussen, rechtliche Normen werden vom Staat durchgesetzt. Ein "
         "Wertekonflikt entsteht, wenn zwei Werte in einer Situation nicht zugleich erfuellt werden koennen.")
POTENZEN = ("Potenzgesetze im Ueberblick. Wenn man zwei Potenzen mit derselben Basis multipliziert, werden "
            "die Exponenten addiert. Wenn man zwei Potenzen mit derselben Basis dividiert, werden die "
            "Exponenten subtrahiert. Eine Potenz wird potenziert, indem man die Exponenten multipliziert. "
            "Brueche werden addiert, indem man sie zuerst auf einen gemeinsamen Nenner bringt und dann die "
            "Zaehler addiert. Die folgenden Beispiele zeigen die Regeln an einfachen Zahlen.")


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:   # Pillow < 10.1
        return ImageFont.load_default()


def text_image(text: str, *, size=(1240, 1754), paper=(250, 250, 248), ink=(20, 20, 20), font_size=34,
               fmt="PNG") -> bytes:
    """A page picture (a photo of a page, or a handwritten note): the words drawn on paper."""

    image = Image.new("RGB", size, paper)
    draw = ImageDraw.Draw(image)
    face = _font(font_size)
    words, line, y = text.split(), "", 110
    for word in words:
        if len(line) + len(word) + 1 > 48:
            draw.text((90, y), line, fill=ink, font=face)
            y += int(font_size * 1.7)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        draw.text((90, y), line, fill=ink, font=face)
    output = io.BytesIO()
    image.save(output, format=fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    return output.getvalue()


def _bytes(document) -> bytes:
    data = document.tobytes(garbage=0, deflate=True)
    document.close()
    return data


def text_pdf(*texts: str) -> bytes:
    """A born-digital PDF: one page of vector text per argument ("" is a blank page)."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    for text in texts:
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        if text:
            page.insert_textbox(pymupdf.Rect(60, 60, 540, 780), text, fontsize=12)
    return _bytes(document)


def many_pages_pdf(count: int) -> bytes:
    texts = [KANT, UTIL, PLATON, RAWLS, JONAS, WERTE]
    return text_pdf(*(f"Seite {index + 1}. {texts[index % len(texts)]}" for index in range(count)))


def picture_pdf(*pictures: bytes) -> bytes:
    """A scanner-app PDF without a text layer: one full-page picture per page."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    for picture in pictures:
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        page.insert_image(page.rect, stream=picture)
    return _bytes(document)


def scanner_app_pdf() -> bytes:
    """A phone-scanner export: a full-page photo (printed text plus a handwritten note) with the
    app's invisible OCR text layer, which holds only the printed text (DR-05)."""

    picture = text_image(RAWLS + " Notiz: Schleier = keiner kennt seinen Platz, also fair!", fmt="JPEG")
    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
    page.insert_image(page.rect, stream=picture)
    page.insert_textbox(pymupdf.Rect(40, 40, 560, 600), RAWLS, fontsize=11, render_mode=3)  # invisible OCR
    return _bytes(document)


def mixed_pdf(share: float = 0.2) -> bytes:
    """A born-digital page (long text layer) with a raster handwritten margin note covering
    about `share` of the page (DR-05)."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 330), WERTE, fontsize=12)
    note = text_image("Randnotiz: Wertekonflikt = Freiheit gegen Sicherheit", size=(1000, 360), font_size=40)
    width = page.rect.width - 120
    height = page.rect.width * page.rect.height * share / width
    page.insert_image(pymupdf.Rect(60, 360, 60 + width, 360 + height), stream=note, keep_proportion=False)
    return _bytes(document)


def formula_pdf() -> bytes:
    """A born-digital maths sheet whose text layer splits exponents and stacked fractions onto
    lines of their own (D5), under enough prose to pass the text-layer gate."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
    page.insert_textbox(pymupdf.Rect(60, 50, 540, 250), POTENZEN, fontsize=12)
    y = 300
    for base, exponent, rest in (("2", "3", "· 2"), ("a", "m", "· a"), ("x", "2", "+ x")):
        page.insert_text((80, y), base, fontsize=14)
        page.insert_text((90, y - 7), exponent, fontsize=9)
        page.insert_text((104, y), rest, fontsize=14)
        page.insert_text((134, y - 7), "4" if base == "2" else "n", fontsize=9)
        page.insert_text((150, y), "= " + base, fontsize=14)
        page.insert_text((182, y - 7), "7" if base == "2" else "k", fontsize=9)
        y += 60
    for numerator, denominator, x in (("3", "4", 80), ("5", "6", 140), ("19", "12", 220)):
        page.insert_text((x, y), numerator, fontsize=12)
        page.insert_text((x - 6, y + 10), "—", fontsize=12)
        page.insert_text((x, y + 24), denominator, fontsize=12)
    page.insert_text((110, y + 12), "+", fontsize=12)
    page.insert_text((180, y + 12), "=", fontsize=12)
    return _bytes(document)


def owner_only_pdf(method: str = "AES_256", text: str = KANT) -> bytes:
    """A "restrict editing" export: an owner (permissions) password and no user password."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    document.new_page().insert_textbox(  # pyright: ignore[reportAttributeAccessIssue]
        pymupdf.Rect(60, 60, 540, 780), text, fontsize=12)
    data = document.tobytes(encryption=getattr(pymupdf, f"PDF_ENCRYPT_{method}"), user_pw="", owner_pw="owner",
                            permissions=pymupdf.PDF_PERM_ACCESSIBILITY | pymupdf.PDF_PERM_PRINT)
    document.close()
    return data


def user_password_pdf(text: str = KANT) -> bytes:
    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    document.new_page().insert_textbox(  # pyright: ignore[reportAttributeAccessIssue]
        pymupdf.Rect(60, 60, 540, 780), text, fontsize=12)
    data = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="geheim", owner_pw="owner")
    document.close()
    return data


# ------------------------------------------------------------------ corruptions

def truncated(data: bytes, keep: float = 0.6) -> bytes:
    """An interrupted download: the first `keep` share of the file."""

    return data[: int(len(data) * keep)]


def wrong_count(data: bytes) -> bytes:
    """/Pages /Count says one page more than there is... or less: 3 -> 2 (DR-06)."""

    return data.replace(b"/Count 3", b"/Count 2")


def corrupt_one_page(data: bytes, page_number: int = 2) -> bytes:
    """Overwrite the content stream of one page with garbage of the same length (DR-04)."""

    document = pymupdf.open(stream=data, filetype="pdf")
    target = document[page_number - 1].get_contents()[0]
    document.close()
    start = data.find(f"\n{target} 0 obj".encode())
    assert start >= 0, "content object not found"
    stream_at = data.find(b"stream", start) + len(b"stream\n")
    end = data.find(b"endstream", stream_at)
    garbage = bytes(random.Random(7).randrange(256) for _ in range(end - stream_at))
    return data[:stream_at] + garbage + data[end:]


def corrupt_page_object(data: bytes, page_number: int = 2) -> bytes:
    """Destroy one page dictionary itself (DR-03): pypdf skips the page, PyMuPDF repairs it."""

    document = pymupdf.open(stream=data, filetype="pdf")
    xref = document[page_number - 1].xref
    document.close()
    marker = f"\n{xref} 0 obj".encode()
    start = data.find(marker) + len(marker)
    end = data.find(b"endobj", start)
    garbage = b"<<\x00\x13 \xff garbage ]] >> /Broken" + b"#" * max(0, end - start - 34)
    return data[:start] + garbage[: end - start] + data[end:]


def half(data: bytes) -> bytes:
    """A half-transferred photo."""

    return data[: len(data) // 2]


def ethics_six(damaged: tuple[str, bytes] | None = None) -> list[tuple[str, bytes, str]]:
    """The six-document Ethics scenario: five valid papers and a damaged 4th PDF (a download
    cut at 60 %, which no PDF library can open)."""

    fourth = damaged or ("ethik-4-abgeschnitten.pdf", truncated(text_pdf(KANT, UTIL, PLATON), 0.6))
    return [
        ("ethik-1-kant.pdf", text_pdf(KANT, UTIL), "application/pdf"),
        ("ethik-2-platon.pdf", text_pdf(PLATON), "application/pdf"),
        ("ethik-3-handschrift.png", text_image("Pflicht und Neigung bei Kant, die Maxime als Grundsatz"), "image/png"),
        (fourth[0], fourth[1], "application/pdf"),
        ("ethik-5-rawls-jonas.pdf", text_pdf(RAWLS, JONAS), "application/pdf"),
        ("ethik-6-werte.pdf", text_pdf(WERTE), "application/pdf"),
    ]


def pdf_page_count(data: bytes) -> int:
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return len(document)
    finally:
        document.close()


def has_count(data: bytes, count: int) -> bool:
    return re.search(rb"/Count\s+%d\b" % count, data) is not None
