# studip-router: changes from 0.2.4 to 0.4.1

All changes made on 2026-11-05: three releases on PyPI and four merged pull requests:
[nbomers/studip-router#17](https://github.com/nbomers/studip-router/pull/17),
[#18](https://github.com/nbomers/studip-router/pull/18),
[#19](https://github.com/nbomers/studip-router/pull/19) and
[#20](https://github.com/nbomers/studip-router/pull/20).

| Version | Focus |
|---|---|
| **0.3.0** | Sorting by file name; a guided home screen; a clearer sync preview; every screen reworked; release dry run |
| **0.4.0** | Much longer keyword lists; courses closed to you no longer break the others; progress bars; merge set-up in the UI; course colours; compact settings |
| **0.4.1** | Course colours taken from Stud.IP |

**Update:** `uv tool upgrade studip-router` (or `pipx upgrade studip-router`)

---

## Contents

1. [Sorting files (routing)](#1-sorting-files-routing)
2. [Keyword lists](#2-keyword-lists)
3. [Home screen](#3-home-screen)
4. [Sync preview and sync log](#4-sync-preview-and-sync-log)
5. [The other screens](#5-the-other-screens)
6. [Settings](#6-settings)
7. [Merge slides](#7-merge-slides)
8. [Progress bars](#8-progress-bars)
9. [Course colours](#9-course-colours)
10. [Courses closed to you (HTTP 403/404)](#10-courses-closed-to-you-http-403404)
11. [System files from Stud.IP](#11-system-files-from-studip)
12. [Keys](#12-keys)
13. [Coming from 0.2.x](#13-coming-from-02x)
14. [Known limits](#14-known-limits)
15. [Ideas and roadmap](#15-ideas-and-roadmap)

Appendix for contributors: [A. Release process and CI](#a-release-process-and-ci) · [B. Documentation](#b-documentation) · [C. Tests](#c-tests)

---

## 1. Sorting files (routing)

*Since 0.3.0.*

### Before
A file that no rule matched was placed by the **name of its Stud.IP folder** alone. If the folder name said nothing, the file went to the default folder (`Sonstiges`), however clear its own name was. Typical examples:

- `Uebungsblatt_3.pdf` directly in the course root
- `VL03_Einleitung.pdf` in "Allgemeiner Dateiordner" (the folder Stud.IP creates for every course)
- `Altklausur_2024.pdf` in a folder called "Material"

### Now
Every file is checked in this order. The first step that decides wins:

| Step | Looks at | Example |
|---|---|---|
| 1 | **Your rules** in the config | `match = { folder = "Übungsblätter*" }` |
| 2 | **Stud.IP folder name** (top three levels) | `Folien/…` → your slides folder |
| 3 | **Stud.IP folder type** | a homework folder (`HomeworkFolder`) → exercises |
| 4 | **File name**, only when the folder says nothing | `Uebungsblatt_3.pdf` → exercises |
| 5 | **Default folder** | `Sonstiges` |

Details:

- **A folder name that says something wins over the file name.** An exercise sheet the lecturer put into "Folien" stays with the slides. The folder keeps its contents together.
- **Only local folders that exist are used.** Without a literature folder, literature goes to the default folder; no new folders are created.
- **Solutions without their own folder** go with the exercises.
- **Code files are never lecture scripts:** `script.py` or `demo.m` is not taken for a "script" because of its name.
- **Jupyter notebooks (`.ipynb`) count as exercises** by their extension, whatever their name. *(0.4.0)*
- The sync preview shows for every file what placed it (see [section 4](#4-sync-preview-and-sync-log)).

### Files already synced
They stay where they are; the tool never moves anything on its own. Files that the new order would place elsewhere are shown on the home screen as a next step. `studip reorganize` (or `o` in the UI) moves them, and `studip undo` (`u`) moves them back.

---

## 2. Keyword lists

*Extended in 0.3.0, reworked in 0.4.0.*

The keyword lists decide whether a name means exercises, slides, exams, solutions or reading. For 0.4.0 they were reworked against Stud.IP guides of several universities and English course websites, and checked against more than 130 real folder and file names.

### How names are compared
- Upper and lower case do not matter.
- Umlauts are written out: "Übung" = "Uebung". **Umlauts written without "e"** are recognised too: "Ubung", "Losung", "Prufung", "Prasentation", "Lekture".
- **`_`, `-`, `.` and `+` count as spaces:** "problem-sets", "übungs_blätter" and "VL.03" are recognised.
- **Short keywords of up to three letters count only as whole words:** "VL 3", "VL03", "HW1" and "Ü 3" hit, but "Label" or "Collab" do not hit "lab".
- **The order decides between categories:** exams → solutions → exercises → slides → reading. "Klausur Lösung" is an exam and "Lösung Blatt 3" a solution.

### Categories and keywords

**Exams**
Klausur (also Altklausur, Probeklausur, Nachklausur, Klausurvorbereitung), Prüfung/Prufung (also Prüfungsvorbereitung), Exam (also Past exams, Mock exam, Examination), Past paper, Midterm, Testat

**Solutions**
Lösung/Losung (also Musterlösung, Lösungsvorschlag, Lösungshinweise), Solution, Answer (also Answer key), Erwartungshorizont

**Exercises**
Übung/Ubung (also Übungsblatt, Hausübung, Präsenzübung, Zentralübung, Globalübung), Ü/UE as a whole word ("Ü 3", "UE03"), Exercise, Blatt/Blätter (also Aufgabenblatt, Arbeitsblatt), Zettel (also Übungszettel), Aufgabe (also Hausaufgabe, Einsendeaufgabe, Programmieraufgabe), Abgabe, Homework, HW ("HW1"), Assignment, Sheet (also Worksheet, Exercise sheet), Problem set/Problemset, Tutorium/Tutorien/Tutorial, Recitation, Praktikum, Labor/Lab, Notebook, Workbook

**Slides / script**
Folie (also Vorlesungsfolien, Foliensatz), Slide, Vorlesung (also Vorlesungsunterlagen), Lecture (also Lecture notes), VL, Skript/Skriptum, Script, Präsentation/Presentation, Vortrag/Vorträge, Handout, Kapitel/Chapter, Lektion, Mitschrift, Tafelbild/Tafelanschrieb, Whiteboard, Zusammenfassung/Summary, Formelsammlung, Lehrbrief/Studienbrief/Kurseinheit (FernUni)

**Reading** *(only when a local folder for it exists)*
Literatur (also Literaturliste, Pflichtliteratur), Lektüre, Reader, Reading, Semesterapparat, Paper(s), Texte, Quellen, Bibliographie, Book(s), Buch (also Lehrbuch), Artikel/Article

### Names that only contain a keyword
These names are removed before the keywords are compared, so they are not taken for the category:

| Name | Would have been taken for | Is actually |
|---|---|---|
| Example, Examples, Exemplar | exam | examples |
| Merkblatt, Deckblatt, Infoblatt, Hinweisblatt, Beiblatt, Anmeldeblatt, Formelblatt, Datenblatt | exercise sheet | information, cover or data sheets |
| Cheat sheet, Datasheet, Spreadsheet, Cover sheet, Info sheet | exercise sheet | not exercises |
| Laufzettel, Stundenzettel | exercise | forms |
| Scripts, Code, Source code | lecture script | programs |
| Prüfungsordnung, Prüfungsamt, Prüfungsausschuss, Prüfungsrecht | exam | regulations, offices |
| Vorlesungsverzeichnis, -plan, -zeit, -beginn, -ende, -frei | slides | timetables and dates |
| Handbuch, Logbuch, Raumbuchung | reading | manuals, logs, room bookings |

### Left out on purpose
Some names say too little and would cause more wrong hits than right ones: "Material", "Unterlagen", "Allgemeiner Dateiordner", "Projekt", "Referat", "Quiz", "Sitzung", "Woche". In such folders the file name decides (step 4 above).

### Local folders
Your own folders are recognised with matching lists, for example "Übungen", "Exercises", "Skript", "Folien", "Lecture notes", "Mitschrift", "Klausur", "Literatur", "Texte", "Bücher", "Semesterapparat" and "Quellen". Folders named like archives ("alt", "old", "Archiv", "Backup") never receive new files.

---

## 3. Home screen

*Since 0.3.0, refined in 0.4.0.*

### Before
Four status cards on top and eleven actions with long descriptions, all equally prominent.

### Now
- **One "next step" box at the top.** It shows the most important thing to do; **Enter** does it. Priority:
  1. Auto-sync is paused because Stud.IP rejected the password → log in again
  2. Not logged in → log in
  3. Courses could not be read during the last sync → look at the problem
  4. Files failed during the last sync → try again
  5. New courses on Stud.IP → choose whether to sync them
  6. Never synced → start the first sync
  7. Synced files now belong in another folder → move them
  8. Files landed in the default folder → sort them with rules
  9. Unread announcements → read them
  10. Otherwise: "All good, last sync …" → sync now

  Below it, up to two further points appear as "then: …".
- **One status line** instead of four cards: last sync, number of files and courses, unread announcements. Numbers are in bold.
- **Five everyday actions:** Sync now, What's new, Announcements, Dates, Courses.
- **Everything else is behind "More" (`+`):** Preview, Rules, Reorganize, Merge slides, Settings, Doctor. All keys work whether their line is shown or not.
- **Short descriptions** of two to three words ("get new files", "latest files", "what to sync"). *(0.4.0)*
- The footer shows `, Settings`, `+ More`, `? Help` and `q Quit`.

---

## 4. Sync preview and sync log

*Since 0.3.0.*

### Preview (`p`, or `s` before confirming)
- **Headline:** "**3 new** files · WiSe 2026/27 → **Enter** syncs"
- **Files going to the default folder** are marked `!`, shown in yellow and listed first, with a line "! **1** file goes to **Sonstiges** – no match".
- **Problems** come first in the table ("✗ **2** problem(s) – at the top").
- **Files not downloaded are counted with their reason**, for example "◎ 4 not downloaded (3 too large, 1 excluded) – v shows them". `v` shows or hides them in the table.
- **The "Why" column** says what placed a file:
  - `your rule sheets`
  - `by folder name`
  - `by file name`
  - `by Stud.IP folder type`
  - `nothing matched` (yellow)
  - for skipped files, the reason
- **Columns:** Course (in its course colour) · On Stud.IP · → Saved as · Why. Long names are cut with "…".
- **Courses closed to you** appear in grey: "◎ files closed to you: …" *(0.4.0)*
- **New courses** that are not set up yet are pointed out.

### Confirm dialog
"Sync now?" with a short summary such as "**3 new** · **1** to the **default folder** · 4 not downloaded".

### While syncing
A progress bar over all files and a log with one line per file (`Esc` stops after the current file). After the downloads, the date check also shows its progress per course. *(0.4.0)*

### After the sync
- "✓ **Sync finished** – **3** new", or "**Sync finished with 2 problem(s)** – retried next time"
- "**Done.** **3** downloaded" (failed files appear only if there are any, in red)
- **Per course: what landed in the default folder**, for example "! **Software Engineering**: **2** in **Sonstiges** – Orga.pdf, Termine.pdf"
- **Per course and reason: what was not downloaded**, for example "◎ Rechnernetze: 3 not downloaded, too large: vorlesung.mp4, …"
- Changed course dates, merged slides, unlocked PDFs and a hint about files to reorganize, as before

---

## 5. The other screens

*0.3.0 and 0.4.0.* The same principle applies everywhere: numbers and key words in bold, long names cut with "…", hints shortened to what is needed.

| Screen | Changes |
|---|---|
| **Courses** (`c`) | "**1** course synced of 2" in bold. New courses are bold with "**1 new** – Enter: choose a folder or 'don't sync'", and their folder column says "Enter: choose". The "Type" column is gone. Course names in their colour. |
| **What's new** (`w`) | Columns When · **Course** (coloured) · File · In (the folder *inside* the course). Times as "today 14:02", "yesterday 09:15" or a date; today's entries in bold. Count of the last 7 days on top. |
| **Announcements** (`a`) | Date dimmed, course in bold and in its colour, title cut. Without unread news: "✓ No unread announcements". |
| **Dates** (`t`) | "Changed dates (n)" and "Exams (n)" as headings. Date and "in **12** days" in bold, course in its colour, cancellations red and bold. Proper padding. |
| **Rules** (`r`) | Suggestions with course (coloured) and target in bold. "**1** suggestion – **w** adds the ticked ones". The name tester says why: "by file name", "your rule x" or "nothing matched – default folder". Courses whose files are closed are named instead of breaking the list. |
| **Reorganize** (`o`) | "**3** files can move – **Enter** moves them". Without anything to move, "✓ **Everything is in place**" and no empty table. Columns Now · → Moves to · Problem. |
| **Doctor** (`d`) | A summary on top ("**1 failed** · **15** ok"). Problems first. All course folders that are fine in **one** line. Paths shortened: `~/Library/Mobile Documents/com~apple~CloudDocs/…` → `iCloud Drive/…`. A missing course folder says how to fix it ("Courses (c): Enter chooses its folder"). Info lines are no longer cut. |
| **Setup** | Shorter step texts ("3 short steps", "Folder for everything else"). Step titles in bold. A progress bar while connecting. |
| **Help** (`?`) | A compact key overview instead of long sentences. |

---

## 6. Settings

*0.3.0 and 0.4.0.*

- **Essentials on top:** course folders in, default folder, auto-sync, notifications, skip videos, size limit, unzip archives, theme.
- **Everything else under "▸ advanced – 14 more settings"**, which opens in place with Enter, Space or ←/→ and closes the same way.
- **The explanation appears only under the focused row.** The list is about half as long.
- **The focused row has a coloured background**, so you can see where you are.
- **`,` opens the settings from every screen.**
- Changes are still saved immediately, and comments in the config file are kept.

---

## 7. Merge slides

*0.4.0.*

### Before
The screen only showed a hint to add `merge = { source = "Slides", output = "All slides.pdf" }` to the config by hand.

### Now
- **A table of all synced courses:** Course · Slides from · → One PDF · Last run.
- **Enter on a course** lists its folders, slide folders first. Choose one and every PDF in it is combined into "All slides.pdf" in the same folder. "don't merge this course" turns it off again.
- **`m` merges now.** Courses that are set up are also merged after each sync, as before.
- Your own files stay untouched. The setting is written to the config with its comments kept.

---

## 8. Progress bars

*0.4.0.*

A progress bar appears wherever the app waits for Stud.IP:

| Where | Shows |
|---|---|
| Sync preview | "Reading course **3/10**: Rechnernetze…", filling up course by course |
| After a sync | date check, course by course |
| Courses | while asking Stud.IP for the courses |
| Announcements, Dates, Rule suggestions | course by course |
| Merge slides | course by course |
| Doctor | while the checks run |
| Setup | while connecting to Stud.IP |

The bar of the sync itself (one step per file) existed before.

---

## 9. Course colours

*0.4.0, Stud.IP colours since 0.4.1.*

- **Every course has one colour, the same on every screen:** sync preview, Courses, What's new, Announcements, Dates, Rules, Merge and the sync log.
- **Your Stud.IP colours first** *(0.4.1)*: the tool reads each course's colour group from Stud.IP. These are the colours of "My courses" and of your timetable. The nine Stud.IP colours (purple, pink, teal, orange, green, brown, lime, red, yellow) are slightly lightened so they stay readable on a dark terminal.
- **Courses without a Stud.IP colour** get their own colour in the order of your config. Unlike in 0.4.0, no two courses share one (up to ten courses).
- The colours are fetched in the background when the home screen loads and remembered in `course-colors.json` in the state folder (`~/.local/state/studip-router/` on macOS and Linux, or `$XDG_STATE_HOME/studip-router/` when that is set).
- **Profiles share this file.** `course-colors.json` is not kept per profile, and each fetch replaces it. With two profiles (for example two universities), the Stud.IP colours of the profile opened last win; the other profile falls back to the config-order colours until its home screen fetches them again.
- **Made for dark terminals.** The colours are lightened for a dark background. With the `textual-light` theme, light ones such as yellow (`#ffd47b`) may be hard to read. *Not tested.*
- Red, green and yellow keep their meaning (error, done, needs a look) for status texts.

---

## 10. Courses closed to you (HTTP 403/404)

*0.4.0.*

### Problem
A course whose announcements, dates or files Stud.IP closes to you answers with "HTTP 403". The tool took that as an error, and **one such course stopped everything**: the announcements of all courses, the rule suggestions and the home screen's status.

### Now
- Stud.IP's answer code is kept with every error.
- **403 and 404 mean "closed to you"**: the course is skipped and named, and the other courses are read normally.
  - Sync preview: "◎ files closed to you: Course X"
  - CLI summary: "Files closed to you on Stud.IP (skipped): …"
  - `--json`: new field `closed_courses`
  - Rule suggestions: "◎ not looked at: Course X (files closed to you)"
  - Announcements and dates of such a course: none, without an error
- **Other server errors** (500, 400, network) are still reported as errors.
- A rejected login (401) still stops the run at once and pauses auto-sync.

### Changed for scripts: exit code and notifications
- **Exit code:** `studip sync` exits with 1 only for failed files and courses in `course_errors`. A course closed to you is no longer an error, so a sync that exited with **1** in 0.2.4 because of such a course now exits with **0**. Scripts that check the exit code no longer see it; read `closed_courses` from `--json` instead.
- **Notifications:** the auto-sync notification counts problems the same way, so closed courses no longer appear as "n problem(s)".

### Caveat: 404 is not always "closed"
A 404 can also mean that a course was deleted on Stud.IP or that you are no longer a member. Since 0.4.0 that case is shown in grey as "closed to you" too, with exit code 0 and no notification. If a course disappears from your synced files without a reason, check it on Stud.IP.

---

## 11. System files from Stud.IP

*0.4.0.*

Files that operating systems leave behind sometimes end up on Stud.IP by accident. Until now they were downloaded too, for example `.DS_Store` as `ds_store`. They are now skipped with the reason "system file":

- `.DS_Store`, `.localized`, `Icon\r` (macOS)
- `._*` (macOS resource forks)
- `Thumbs.db`, `desktop.ini` (Windows)

**Files downloaded before 0.4.0 stay**, because the tool never deletes anything. Remove them by hand.

---

## 12. Keys

| Key | Where | What | New |
|---|---|---|---|
| `Enter` | Home | do the suggested next step | 0.3.0 |
| `+` | Home | show / hide more actions | 0.4.0 |
| `,` | **everywhere** | settings | 0.4.0 (before: home only) |
| `Enter` | Merge slides | choose a course's slides folder | 0.4.0 |
| `m` | Merge slides | merge now | 0.4.0 |
| `Enter` / `Space` / `←` `→` | Settings, "advanced" row | open / close the advanced settings | 0.4.0 |

All other keys are unchanged (`s`, `p`, `w`, `a`, `t`, `c`, `r`, `o`, `m`, `d`, `?`, `q`, `v`, `i`, `u`, `e`).

---

## 13. Coming from 0.2.x

1. Update with `uv tool upgrade studip-router`. If you installed from GitHub (`git+…`), switch to PyPI once with `uv tool install --force studip-router`.
2. macOS with auto-sync: allow keychain access again after the update, with `studip schedule run-now` → *Always Allow*.
3. Open `studip` and press `p` to see where new files go now. **With auto-sync on, do this before the next scheduled run:** the background sync sorts new files by the new order straight away, and it shows no preview.
4. If the home screen shows files that "belong in another folder now": `o` moves them, `u` moves them back.
5. Delete files from older versions that would no longer be downloaded (`ds_store` and similar) by hand.
6. If a script or cron job checks the exit code of `studip sync`: courses closed to you no longer make it fail (see [section 10](#10-courses-closed-to-you-http-403404)).

Your config, rules and sync state are kept; none of them is part of the package.

---

## 14. Known limits

- The keyword lists sort by words in names. Some files will still land where you would not put them, for example code archives in a folder called "Folien": the folder name wins. A rule in the config fixes that for one course.
- Your own keywords can only be added as rules (`name_regex`), not as a simple word list.
- Without Stud.IP colours there are ten different course colours; from the eleventh course on, they repeat.
- The Stud.IP colours come from the `group` field of `/users/{id}/course-memberships`. Whether every Stud.IP instance sends it is not checked; without it the tool silently uses the fallback colours.
- Course colours are shared between profiles and made for dark terminals (see [section 9](#9-course-colours)).
- A course that answers 404 is treated as closed to you, even if it was deleted or you left it (see [section 10](#10-courses-closed-to-you-http-403404)).
- All tests run against the fake Stud.IP in `tests/fake_studip.py`. The three releases were not checked against a real Stud.IP instance before publishing.
- The interface is English.
- Login only with username and password (local or LDAP accounts); no SSO and no two-factor authentication. No Windows.

---

## 15. Ideas and roadmap

Not built yet. Notes on what is already true in the code are marked *checked*; everything else is a proposal and unverified.

### Semester change

**Semester change assistant.** Today the FAQ in the README says to run `studip config set sync.root "~/Uni/Semester 6"` by hand (*checked*). Better: as soon as new courses appear, the home screen offers to
- create a new semester folder,
- copy the subfolder structure of the current courses,
- turn off the old courses.

This happens every six months and is currently the only routine task that requires knowing a config key.

### Getting information where students already look

1. **Calendar export (ICS).** `studip dates` already knows exams and moved dates. As an `.ics` file or a subscribable feed they land in Apple, Google or Thunderbird calendars, where students look anyway.
2. **Clickable notifications.** Clicking "3 new files in Mathe" should open the folder or the file. *Checked:* `notify.py` uses `osascript … display notification` on macOS and `notify-send` on Linux. `display notification` cannot carry a click action, so notifications are **not** clickable today. On macOS this needs another mechanism (for example a small helper app or `terminal-notifier`); on Linux, `notify-send --action` where the desktop supports it.

### Less friction when installing and setting up

3. **Shell completions for zsh and bash.** *Checked:* only fish completions exist (`shell/fish`), but macOS uses zsh by default. Typer/Click can generate them with little effort.
4. **Homebrew tap:** `brew install studip-router` instead of installing `uv` first. For non-developers on macOS that is one hurdle less.
5. **Optional update notice.** *Checked:* the README promises "no update checks". A one-time notice such as "0.5 is available – `uv tool upgrade …`", only when switched on, would keep that promise.
6. **Check the instance before asking for the password.** In setup step 1, first test whether `jsonapi.php/v1/discovery` is reachable. If not, say "Your university has not enabled the JSON:API" instead of showing a cryptic error. A list of known instances to choose from would also save typing the URL.

### New features

7. **Opencast recordings** (also listed in `DESIGN.md`). Often asked for, but it is a separate API, and the videos are large.
8. **Assignment deadlines:** show deadlines from Stud.IP (Vips / assignments plugin) on the home screen and in the calendar. Depends on whether the JSON:API provides them; not checked.
9. **Search across files:** `/` in the UI searches file names and PDF text of all courses, not only What's new.
10. **macOS menu bar app** on top of `--json`: status, "Sync now", latest files. Also already on the wish list in `DESIGN.md`.
11. **Raycast and Alfred extensions.** `--json` already provides the data; only the ready-made extension is missing.

### Small everyday things

12. **What's new: mark everything as read** with one key.
13. **Create a rule from a file:** in the preview, press a key on a file in the default folder, and the tool suggests a rule for exactly that folder or name pattern.
14. **`studip open COURSE`:** open the course folder in Finder or the file manager.
15. **Switchable English and German interface.** The folder names are already bilingual; the interface texts are English only.

### Sorting

16. **Shared rule sets per university or course.** Export and import the rules of a course, so a whole year can share one file. This helps more than ever longer keyword lists.
17. **Own keywords as a list in the config,** for example `keywords.exercises = ["Werkstatt"]`, instead of only `name_regex` rules (see [Known limits](#14-known-limits)).
18. **"Sorted wrong" key in the preview.** It writes an issue text with only the folder and file name, no course data, so the keyword lists grow from real mistakes instead of research.
19. **Check after an update.** Show which synced files the new version would place elsewhere, *before* auto-sync runs again. This closes the gap described in [section 13](#13-coming-from-02x), step 3.

### Robustness

20. **Tell 403 from 404.** Treat only 403 as "closed to you"; keep a 404 on a whole course as an error, so a deleted course or a lost membership does not disappear quietly.
21. **Course colours per profile:** `course-colors-<profile>.json` like the state database.
22. **Colours for light themes:** a second, darkened set of the Stud.IP colours when a light theme is chosen.
23. **A check against a real Stud.IP instance before a release,** for example a manual smoke test (`studip doctor`, `studip sync --dry-run`) listed in `CONTRIBUTING.md`.

### Semester end

24. **Archive old courses.** At the end of a semester, take finished courses out of auto-sync and mark their folders as read-only. Fits the semester change assistant above.

---

# Appendix: for contributors

Sections A to C are for people working on the code. Users can stop at section 15.

## A. Release process and CI

*0.3.0.*

- **Release dry run:** `.github/workflows/release.yml` can be started by hand (*Actions → Release → Run workflow*). It runs the same checks, builds, hands the files over between jobs and checks they arrived, but **uploads nothing**. Publishing to PyPI only happens on a version tag.
  - Reason: Dependabot had raised `actions/upload-artifact` from v4 to v7 and `actions/download-artifact` from v4 to v8, in a workflow that only runs on tags. The dry run was green before 0.3.0.
  - When to run it: after every change to `release.yml` or its actions. `CONTRIBUTING.md` lists it as a release step.
- **pytest warning fixed:** `asyncio_default_fixture_loop_scope = "function"` is set in `pyproject.toml`.
- **Releases:** 0.3.0, 0.4.0 and 0.4.1 were each prepared as a version commit (CHANGELOG, `pyproject.toml`, `__version__`, `uv.lock`) and published through the GitHub release page.

---

## B. Documentation

- **README:**
  - Installing with plain `pip` in a venv is documented next to uv and pipx, with a note on the "externally-managed-environment" error. *(0.3.0)*
  - The home screen, sync preview, automatic sorting and `sync.auto_route` describe the new order (folder name → folder type → file name).
  - The merge setting points to the UI.
- **`config.example.toml`:** the comment on `auto_route` describes the file-name step.
- **Screenshots** in `docs/screenshots/` regenerated: home, preview, confirm dialog, sync finished, settings, courses, rule suggestions, setup.
- **CHANGELOG** for 0.3.0, 0.4.0 and 0.4.1.

---

## C. Tests

From 324 tests (0.2.4) to **474 tests** (0.4.1), all against the fake Stud.IP in `tests/fake_studip.py`. Lint (`ruff`), formatting and type checks (`mypy`) are clean.

New or changed:
- **`tests/test_keywords.py`**: 137 cases. Real folder and file names with the category a student expects, and names that must stay unmatched (Merkblatt, Prüfungsordnung, Examples, Raumbuchung, …).
- **Routing:** file name when the folder says nothing; folder name wins; homework folder type; literature only with a local folder; code files are no scripts.
- **HTTP 403:** announcements return nothing, other errors still raise, dates are skipped quietly.
- **System files** are skipped.
- **Home screen:** the next step is highlighted, "More" opens, Enter opens Courses.
- **Settings:** the advanced settings are closed until opened.
- **Colours:** ten configured courses get ten colours, and a Stud.IP colour group overrides the fallback.
- Tests that used "Blatt" as a neutral file name use neutral names now, because such files are now sorted into exercises.
