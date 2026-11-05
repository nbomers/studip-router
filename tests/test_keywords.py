"""The keyword lists: real folder and file names from German and English courses.

Each case is what a student would expect. Names that only *contain* a keyword
("Merkblatt", "Prüfungsordnung", "Examples") must not be taken for it.
"""

from __future__ import annotations

import pytest

from studip_router.suggest import _category, file_category

FOLDERS = {
    # exams
    "Altklausuren": "exams",
    "Probeklausur WS 25/26": "exams",
    "Nachklausur": "exams",
    "Klausurvorbereitung": "exams",
    "Prüfungsvorbereitung": "exams",
    "Prufung": "exams",
    "Past Papers": "exams",
    "Past exams": "exams",
    "Mock Exam": "exams",
    "Midterm": "exams",
    "Testate": "exams",
    "Klausur Lösungen": "exams",  # exams first
    # solutions
    "Musterlösungen": "solutions",
    "Lösungsvorschläge": "solutions",
    "Loesungen": "solutions",
    "Losungen": "solutions",
    "Solutions": "solutions",
    "Answer keys": "solutions",
    "Erwartungshorizonte": "solutions",
    "Übungsblatt 3 - Lösung": "solutions",
    # exercises
    "Übungen": "exercises",
    "Uebungsblaetter": "exercises",
    "Übungs_Blätter": "exercises",
    "Ubungen": "exercises",
    "Zentralübung": "exercises",
    "Präsenzübung": "exercises",
    "Hausübungen": "exercises",
    "Globalübung": "exercises",
    "Ü 3": "exercises",
    "UE03": "exercises",
    "Aufgabenblätter": "exercises",
    "Arbeitsblätter": "exercises",
    "Übungszettel": "exercises",
    "Hausaufgaben": "exercises",
    "Einsendeaufgaben": "exercises",
    "Abgaben": "exercises",
    "Homework": "exercises",
    "HW1": "exercises",
    "Assignments": "exercises",
    "Exercise sheets": "exercises",
    "Worksheets": "exercises",
    "Problem Sets": "exercises",
    "problem-sets": "exercises",
    "Problemsets": "exercises",
    "Tutorium": "exercises",
    "Tutorien": "exercises",
    "Tutorials": "exercises",
    "Recitations": "exercises",
    "Praktikum": "exercises",
    "Labor": "exercises",
    "Lab 2": "exercises",
    "Notebooks": "exercises",
    "Workbook": "exercises",
    # slides
    "Folien": "slides",
    "Vorlesungsfolien": "slides",
    "Foliensätze": "slides",
    "Slides": "slides",
    "Vorlesung": "slides",
    "Vorlesungsunterlagen": "slides",
    "Lecture Notes": "slides",
    "Lectures": "slides",
    "VL 3": "slides",
    "VL03": "slides",
    "Skript": "slides",
    "Skriptum": "slides",
    "Script": "slides",
    "Skript : Alle Folien": "slides",
    "Präsentationen": "slides",
    "Vorträge": "slides",
    "Handouts": "slides",
    "Kapitel 1": "slides",
    "Chapter 2": "slides",
    "Lektion 4": "slides",
    "Mitschriften": "slides",
    "Tafelbilder": "slides",
    "Tafelanschrieb": "slides",
    "Zusammenfassungen": "slides",
    "Formelsammlung": "slides",
    "Lehrbriefe": "slides",
    "Kurseinheit 2": "slides",
    # reading
    "Literatur": "reading",
    "Pflichtliteratur": "reading",
    "Lektüre": "reading",
    "Reader": "reading",
    "Readings": "reading",
    "Semesterapparat": "reading",
    "Papers": "reading",
    "Texte": "reading",
    "Quellen": "reading",
    "Bibliographie": "reading",
    "Books": "reading",
    "Lehrbuch": "reading",
    "Artikel": "reading",
}

NOTHING = [
    # say nothing about their content
    "Allgemeiner Dateiordner",
    "Material",
    "Unterlagen",
    "Sonstiges",
    "Organisatorisches",
    "Sitzung 3",
    "Woche 5",
    "Videos",
    # contain a keyword but mean something else
    "Examples",
    "Beispiele",
    "Cheat Sheet",
    "Datasheets",
    "Merkblatt",
    "Deckblatt",
    "Infoblätter",
    "Laufzettel",
    "Scripts",
    "Source Code",
    "Code",
    "Prüfungsordnung",
    "Vorlesungsverzeichnis",
    "Vorlesungsplan",
    "Handbuch",
    "Raumbuchung",
    "Label",
    "Collab",
]


@pytest.mark.parametrize(("name", "category"), FOLDERS.items())
def test_folder_names(name: str, category: str) -> None:
    assert _category(name) == category


@pytest.mark.parametrize("name", NOTHING)
def test_names_that_say_nothing(name: str) -> None:
    assert _category(name) is None


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Uebungsblatt_3.pdf", "exercises"),
        ("blatt03.pdf", "exercises"),
        ("HW2_solutions.pdf", "solutions"),
        ("02_exercise_mlp.pdf", "exercises"),
        ("pytorch_intro_student.ipynb", "exercises"),
        ("Loesung_Blatt_3.pdf", "solutions"),
        ("Musterloesung-Klausur-2024.pdf", "exams"),
        ("vorlesung_06.pdf", "slides"),
        ("VL03_Einleitung.pdf", "slides"),
        ("Foliensatz_Kapitel_2.pdf", "slides"),
        ("Altklausur_SS24.pdf", "exams"),
        ("Literaturliste.pdf", "reading"),
        ("script.py", None),
        ("Merkblatt_Abgabe.pdf", "exercises"),  # "Abgabe" still counts
        ("Deckblatt.pdf", None),
        ("Pruefungsordnung_2023.pdf", None),
        ("Orga.pdf", None),
        ("03.java", None),
    ],
)
def test_file_names(name: str, category: str | None) -> None:
    assert file_category(name) == category
