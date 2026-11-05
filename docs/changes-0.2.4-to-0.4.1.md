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
| *not released* | Fixes from the review ([section 16](#16-review-findings-after-041-bugs-ux-and-security)) and ten roadmap items ([section 15](#15-ideas-and-roadmap)), on `claude/busy-ramanujan-trqb68` |

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
16. [Review findings after 0.4.1: bugs, UX and security](#16-review-findings-after-041-bugs-ux-and-security)

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
- **The focused row has a coloured background**, so you can see where you are. *(In 0.4.1 a second CSS rule cancels it; fixed, not released yet, see [U10](#16-review-findings-after-041-bugs-ux-and-security).)*
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
- **`m` merges now.** Courses that are set up are also merged after each sync, as before. *(In 0.4.1 opening the screen already merges; fixed, not released yet, see [U8](#16-review-findings-after-041-bugs-ux-and-security).)*
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

### Caveat: 404 and courses that are gone
*Corrected after the review.* A course that was deleted on Stud.IP, or that you left, is not a 404: it no longer appears in your course list at all. The tool reports such configured courses as "not found on Stud.IP" (CLI summary). 403 and 404 only concern courses that *are* in your list, so treating both as "closed to you" is right.

Until 0.4.1 the UI did not show courses that are gone. *Fixed, not released:* the sync preview says "! not on Stud.IP any more: …", and `--json` has `missing_courses`. Exit code and notifications stay unchanged: a course you left is no error.

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
| `?` | **everywhere** | help (in 0.4.1 only on the home screen) | not released |
| `x` | Announcements | mark all as read | not released |
| `Esc` | Setup | one step back (in 0.4.1: quit) | not released |

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
- ~~Your own keywords can only be added as rules (`name_regex`), not as a simple word list.~~ *Not released:* `[sync.keywords]` in the config, see section 15, item 17.
- Without Stud.IP colours there are ten different course colours; from the eleventh course on, they repeat.
- The Stud.IP colours come from the `group` field of `/users/{id}/course-memberships`. Whether every Stud.IP instance sends it is not checked; without it the tool silently uses the fallback colours.
- ~~Course colours are shared between profiles and made for dark terminals.~~ *Not released:* one colour file per profile, darker colours for light themes (items 21, 22).
- ~~A course that answers 404 is treated as closed to you, even if it was deleted or you left it.~~ A deleted or left course is not a 404 at all; see the corrected [section 10](#10-courses-closed-to-you-http-403404).
- All tests run against the fake Stud.IP in `tests/fake_studip.py`. The three releases, and the fixes after the review, were not checked against a real Stud.IP instance. `CONTRIBUTING.md` now asks for that before every release (item 23).
- The interface is English.
- Login only with username and password (local or LDAP accounts); no SSO and no two-factor authentication. No Windows.

---

## 15. Ideas and roadmap

Notes on what is already true in the code are marked *checked*; everything else is a proposal and unverified.

**Status after the review** (built on `claude/busy-ramanujan-trqb68`, not released):

| Status | Items |
|---|---|
| ✅ built | 1 calendar export · 3 zsh/bash completions · 6 setup explains a failed login · 12 mark all read (in Announcements) · 14 `studip open` · 17 own keywords · 20 (reframed: courses gone from Stud.IP are shown) · 21 colours per profile · 22 colours for light themes · 23 smoke test in `CONTRIBUTING.md` |
| ⬜ open | semester change assistant · 2 · 4 · 5 · 7 · 8 · 9 · 10 · 11 · 13 · 15 · 16 · 18 · 19 · 24 |

Why these stay open: each is a feature with its own design questions, not a fix. 4 (Homebrew tap) needs a public repository and 18 opens issues, both public steps that need a decision first. 5 (update notice) would contact PyPI and so break the promise "the only host contacted is Stud.IP" unless it is strictly opt-in. 7, 8 and 10 need a real Stud.IP or a separate app.

### Semester change

**Semester change assistant.** Today the FAQ in the README says to run `studip config set sync.root "~/Uni/Semester 6"` by hand (*checked*). Better: as soon as new courses appear, the home screen offers to
- create a new semester folder,
- copy the subfolder structure of the current courses,
- turn off the old courses.

This happens every six months and is currently the only routine task that requires knowing a config key.

### Getting information where students already look

1. ✅ **Calendar export (ICS).** *Built:* `studip dates --ics ~/Uni/dates.ics` writes all known dates of your courses (cancelled ones marked). A subscribable feed is still open. Original idea: `studip dates` already knows exams and moved dates. As an `.ics` file or a subscribable feed they land in Apple, Google or Thunderbird calendars, where students look anyway.
2. **Clickable notifications.** Clicking "3 new files in Mathe" should open the folder or the file. *Checked:* `notify.py` uses `osascript … display notification` on macOS and `notify-send` on Linux. `display notification` cannot carry a click action, so notifications are **not** clickable today. On macOS this needs another mechanism (for example a small helper app or `terminal-notifier`); on Linux, `notify-send --action` where the desktop supports it.

### Less friction when installing and setting up

3. ✅ **Shell completions for zsh and bash.** *Built:* `shell/zsh/_studip`, `shell/bash/studip`, installation in the README. The bash file was syntax-checked; zsh was not available for a test. *Checked:* only fish completions exist (`shell/fish`), but macOS uses zsh by default. Typer/Click can generate them with little effort.
4. **Homebrew tap:** `brew install studip-router` instead of installing `uv` first. For non-developers on macOS that is one hurdle less.
5. **Optional update notice.** *Checked:* the README promises "no update checks". A one-time notice such as "0.5 is available – `uv tool upgrade …`", only when switched on, would keep that promise.
6. ✅ **Check the instance before asking for the password.** *Built, slightly different:* when the login fails, setup asks `…/discovery` (without the password) and says "Your university has not enabled the JSON:API" or "The address cannot be reached". A list of known instances is still open. In setup step 1, first test whether `jsonapi.php/v1/discovery` is reachable. If not, say "Your university has not enabled the JSON:API" instead of showing a cryptic error. A list of known instances to choose from would also save typing the URL.

### New features

7. **Opencast recordings** (also listed in `DESIGN.md`). Often asked for, but it is a separate API, and the videos are large.
8. **Assignment deadlines:** show deadlines from Stud.IP (Vips / assignments plugin) on the home screen and in the calendar. Depends on whether the JSON:API provides them; not checked.
9. **Search across files:** `/` in the UI searches file names and PDF text of all courses, not only What's new.
10. **macOS menu bar app** on top of `--json`: status, "Sync now", latest files. Also already on the wish list in `DESIGN.md`.
11. **Raycast and Alfred extensions.** `--json` already provides the data; only the ready-made extension is missing.

### Small everyday things

12. ✅ **Mark everything as read** with one key. *Built in Announcements* (`x`), where read/unread exists; What's new has no read state.
13. **Create a rule from a file:** in the preview, press a key on a file in the default folder, and the tool suggests a rule for exactly that folder or name pattern.
14. ✅ **`studip open COURSE`:** open the course folder in Finder or the file manager. *Built;* without a file manager it prints the path.
15. **Switchable English and German interface.** The folder names are already bilingual; the interface texts are English only.

### Sorting

16. **Shared rule sets per university or course.** Export and import the rules of a course, so a whole year can share one file. This helps more than ever longer keyword lists.
17. ✅ **Own keywords as a list in the config,** *built:* `[sync.keywords]` with `exams`, `solutions`, `exercises`, `slides`, `reading`; checked before the built-in lists. Original idea: for example `keywords.exercises = ["Werkstatt"]`, instead of only `name_regex` rules (see [Known limits](#14-known-limits)).
18. **"Sorted wrong" key in the preview.** It writes an issue text with only the folder and file name, no course data, so the keyword lists grow from real mistakes instead of research.
19. **Check after an update.** Show which synced files the new version would place elsewhere, *before* auto-sync runs again. This closes the gap described in [section 13](#13-coming-from-02x), step 3.

### Robustness

20. ✅ **Tell 403 from 404.** *Reframed after checking the code:* a deleted or left course never answers 404, it is missing from the course list (see section 10). The UI and `--json` now show such courses. Original idea: Treat only 403 as "closed to you"; keep a 404 on a whole course as an error, so a deleted course or a lost membership does not disappear quietly.
21. ✅ **Course colours per profile:** *built,* `course-colors-<profile>.json` like the state database.
22. ✅ **Colours for light themes:** *built:* each colour has at least 4.6:1 contrast on light grey (#efefef), checked by calculation, not on a real terminal. a second, darkened set of the Stud.IP colours when a light theme is chosen.
23. ✅ **A check against a real Stud.IP instance before a release,** *now step 4 of the release steps in `CONTRIBUTING.md`;* for example a manual smoke test (`studip doctor`, `studip sync --dry-run`) listed in `CONTRIBUTING.md`.

### Semester end

24. **Archive old courses.** At the end of a semester, take finished courses out of auto-sync and mark their folders as read-only. Fits the semester change assistant above.

---

## 16. Review findings after 0.4.1: bugs, UX and security

A code review of 0.4.1 (state of `main` at 2210848, line numbers from there) found the problems below. All but two are fixed on the branch `claude/busy-ramanujan-trqb68` and wait for the next release (0.4.2); each finding says its status. Every finding was checked in the code; those marked *reproduced* were also triggered in a test run (TUI headless with `App.run_test` against `tests/fake_studip.py`, or a Python snippet). Section 14 lists limits that are known and accepted; this section lists defects.

Severity: **high** = data loss, wrong files, a crash or a broken promise in `SECURITY.md`; **medium** = wrong result or a clearly misleading screen; **low** = annoyance or hardening.

### Status overview

| Status | Findings |
|---|---|
| ✅ fixed (not released) | S1–S7 · B1–B11 · U1–U11, U14 |
| ◐ partly fixed | U13 (two short options kept on purpose) |
| ⏸ not changed, on purpose | U12 (keys) |

The fixes are covered by 27 new tests in `tests/test_review_0_4_1.py` (501 tests in total, `ruff` and `mypy` clean). The tests for S3 and B4 were checked to fail against the 0.4.1 code.

### Fix first (all fixed now)

| # | What | Severity |
|---|---|---|
| S1 | Escape sequences from Stud.IP reach the terminal | high |
| S3 | A download can silently overwrite a file the user just saved | medium–high |
| U1 | Enter after a finished sync runs the old plan again and records fake failures | high |
| U3 | Settings crash the app after the config became invalid | high |
| B1 | Esc right after a ZIP download leaves it packed for good | medium |
| B4 | Reorganize moves homework-folder files back to the default folder | medium |
| B6 | Suggested "by name" rules contradict the keyword classifier | medium |

### Security

**S1. Escape sequences from Stud.IP reach the terminal.** *High. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Every string in a JSON answer loses C0/C1 control characters except `\t`, `\n`, `\r` (`api._clean`); `esc()` also strips them and bidi overrides. `\r` stays because `Icon\r` must still be recognised as a system file.
- Where: `report.py:20` (`esc()` escapes Rich markup only), `cli.py:958` (`console.print(Text(n.text + "\n"))`, the raw announcement text), `report.py:134` (raw `folder_path`/`name` in `--dry-run`/`-v`), course titles in `report.py` and the date output. `news.html_to_text` does not remove control characters either. The log file stores the same raw text.
- Problem: Rich passes `ESC` through unchanged (`Text("a\x1b[2Jb")` → `'a\x1b[2Jb'`). A JSON string `"\u001b…"` in an announcement, title or file name reaches the terminal.
- Scenario: anyone who can post an announcement in a course writes an OSC 8 hyperlink with a fake target, clears the screen, changes the window title, or (terminal-dependent) writes the clipboard via OSC 52. `studip news` prints it. This breaks the promise in `SECURITY.md`: "Text from Stud.IP … is never interpreted as terminal markup or links".
- Fix: strip `fsutil._CONTROL` (except `\n`, `\t`) in `esc()`, in every `Text(...)` built from remote data, in `html_to_text` and before logging.

**S2. A course called `~` makes the sync write into the home folder.** *Medium (only with `only_configured_courses = false`). Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Implicit course folders replace a leading `~` with `_` (`sync._implicit_target`).
- Where: `sync.py:169` `target=sanitize_filename(c.title)` and `config.py:96` `Path(course.target).expanduser()`.
- Problem: `sanitize_filename("~")` returns `"~"`, and `expanduser()` turns it into `$HOME`. Implicit targets skip the `unsafe_folder`/`rel_path` checks, which only run when the config is parsed.
- Scenario: a course is titled `~`. The default folder is created in `~`, `auto_route` picks matching subfolders of the home folder, and ADOPT can take over unrelated files of the same name and size anywhere under `~`. `~nonexistent` raises a `RuntimeError`.
- Fix: replace a leading `~` in `sanitize_filename`, and run `unsafe_folder` on implicit targets too.

**S3. A new download can overwrite a file the user saved meanwhile.** *Medium–high. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. DOWNLOAD and NEW_VERSION commit with `move_no_clobber` (hard link, no replace). A file that appeared meanwhile is kept, the download counts as failed and is retried next time.
- Where: `executor.py:225` checks `action.dest.exists()` *before* the download; `fsutil.py:247` then does `os.replace(tmp, dest)` without checking again (for DOWNLOAD and NEW_VERSION there is no `before_replace`).
- Scenario: during a long download (large file, retries with back-off) the user saves a file with the same name, for example from the browser. `os.replace` overwrites it without a word. `SECURITY.md` lists "silent overwriting of user files" as in scope.
- Fix: commit without clobbering (`os.link(tmp, dest)` then `unlink(tmp)`, as `move_no_clobber` already does), or check `exists()` again in `commit`.

**S4. Notifications on Linux: option and markup injection.** *Low. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. `--` before the arguments; `&`, `<`, `>` escaped.
- Where: `notify.py:28` `["notify-send", title, message]` without `--`. The date notification starts with the course title (`dates.py:39`).
- Scenario: a course title starting with `--` (e.g. `--urgency=…`) is parsed as an option, so the notification is empty or fails. The notification daemon renders the body as markup: `<a href=…>` makes a link, `&` breaks the display.
- Fix: pass `--` and escape `<`, `>`, `&`. *Checked:* the macOS path (`osascript`) escapes correctly.

**S5. `STUDIP_ROUTER_PROFILE` is not validated.** *Low, hardening.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. `paths.profile()` checks the variable; the CLI reports a bad value with exit code 2.
- Where: `paths.py:30` returns the environment variable as is; only `set_profile()` checks `_PROFILE_RE`. The value ends up in file names (`schedule.py:109` unit files, state database, config).
- Scenario: `STUDIP_ROUTER_PROFILE=../x` writes units or state outside the intended folder. Only relevant if someone else controls the environment.

**S6. File permissions are inconsistent.** *Low.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. State folder via `private_dir` in `schedule.py` and for the colour file; the config file is written 0600 (setup, `studip init`, settings).
- `schedule.py:162` and `tui/widgets.py:87` create the state folder with plain `mkdir()` (0755) instead of `private_dir`. If `studip schedule install` runs before the first sync, launchd writes `scheduler.log` as 0644 into a 0755 folder until `State` tightens it.
- The config file is written as 0644 (`cli.py:403`, `tui/setup.py:337`, `config_edit.py:36`). It holds the user name and course names, no password. `SECURITY.md` promises nothing here, but 0600 would cost nothing.

**S7. The server can abort a run with `Retry-After`.** *Low. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Clamped to 0–60 s, `nan` ignored.
- Where: `api.py:176` `min(float(value), 60.0)` lets negative values and `nan` through. `time.sleep(-1)` raises `ValueError`, which is not an `ApiError`.
- Scenario: a 503 with `Retry-After: -1` (proxy, misconfigured server) aborts the run or the course instead of retrying.
- Fix: clamp to `0 ≤ x ≤ 60` and reject `nan`.

*Checked and fine:* password handling (keychain only, not in logs or JSON), credentials on redirects, ZIP extraction, path traversal through remote names, permissions of state database and log, systemd and plist quoting, the new Stud.IP colour `group` (only integers 0–8 are used, no markup injection), 403/404 courses are not marked as deleted.

### Bugs

**B1. Esc right after a ZIP download: the archive is never extracted.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. `_unzip` runs before `on_progress`.
- Where: `executor.py:81-85`: `on_progress(action)` runs before `_unzip(...)`; the TUI callback (`tui/views.py:507`) raises `KeyboardInterrupt` when Esc was pressed. The file is already recorded in the state.
- Result: with `unzip = true`, Esc while `Blatt3.zip` downloads → the next sync sees the ZIP as "unchanged" (`planner.py:403`) and never extracts it.
- Fix: unzip before `on_progress`, or plan a missing extraction again.

**B2. Failed files neither move the progress bar nor check Esc.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. New callback `on_failed(action, message)`; the TUI moves the bar, logs a red line and stops on Esc; the CLI prints the failure.
- Where: `executor.py:68-78`: the error branch ends with `continue` before `on_progress`.
- Result: with failures the bar never reaches 100 %. Worse: when the network drops and every file fails after its retries, Esc has no effect until the end of the run, although the screen says "Stopping after the current file…".

**B3. A 403/404 on one courseware page closes the whole course.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Each Courseware unit is read on its own; a 403/404 skips that unit with a warning. 401 and other errors still stop as before.
- Where: `api.py:356-383`: only `courseware-units` is wrapped in try/except. A 403/404 on `/courseware-structural-elements/{root}` or `/descendants` propagates to `sync.py:197-203`, which marks the whole course as closed.
- Result: the normal files of that course (already fetched) are skipped too. Nothing is deleted, but nothing new arrives, and the course is shown as "closed to you".

**B4. Reorganize routes without folder type and semester.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. New column `folder_type` in the state (added automatically, no schema bump). Files synced before have no folder type: reorganize never sends such an auto-placed file to the default folder. A rule whose `rename` uses `{semester}` keeps the current name and only changes the folder.
- Where: `reorganize.py:38-49` builds the `RemoteFile` without `folder_type` (the `FileRecord` does not store it); `reorganize.py:56` calls `build_router(cfg, course)` without the semester that `sync.py` passes.
- Result (folder type): `Meier.pdf` in a homework folder "Gruppe A" is synced to `Übungen` (step 3 of section 1). Reorganize does not know the folder type, routes it to the default folder, offers a move, and `studip reorganize` moves it there. The home screen promotes exactly this as the next step.
- Result (semester): a rule with `rename = "{semester}_{orig_name}"` makes reorganize want to rename every file to `_name.pdf`.
- Fix: store `folder_type` in the state and pass the semester.

**B5. Planner errors give exit code 0.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Planned `ERROR` actions count like failed files (exit code 1, notification, `--quiet` output).
- Where: `cli.py:585`: exit 1 only for `failed` or `sync_plan.errors`. Actions of kind `ERROR` ("course folder does not exist", "target folder does not exist", rename errors, `on_conflict = "error"`) are never executed and therefore not counted.
- Result: `print_summary` lists them under "Errors:", yet the run exits with 0 and the background notification does not count them. Combined with the 403/404 change of section 10, a script sees even fewer problems than before.

**B6. Suggested "by name" rules contradict the keyword classifier.** *Medium. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. With automatic sorting on (the default) no "by name" rules are suggested: step 4 already sorts by file name. With `auto_route = false` they come in the classifier's order (exams → solutions → exercises → slides → reading), and the regex starts with a look-ahead built from the exclusion list, so Merkblatt, Deckblätter, Examples or Prüfungsordnung are not matched.
- Where: `suggest.py:437-450`: the rule regex is built from the keywords without the exclusion list ("Names that only contain a keyword", section 2), and the rules are sorted alphabetically (exams, exercises, slides, solutions) instead of by category priority. Rules win over automatic sorting.
- Result, in a course with Übungen, Lösungen, Folien, Klausuren folders:

| File | Rule sends it to | Classifier says |
|---|---|---|
| `Lösung Blatt 3.pdf` | Übungen | solutions |
| `Vorlesung 3 Lösung.pdf` | Folien | solutions |
| `Merkblatt.pdf` | Übungen | none |
| `Laufzettel.pdf` | Übungen | none |
| `Examples.pdf` | Klausuren | none |
| `Scripts.zip` | Folien | none |

Accepting the suggestions makes sorting *worse* than without them.

**B7. "By name" suggestions are offered again on every run.** *Low–medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. A category whose `<category>-by-name` rule exists is not suggested again.
- Where: `cli.py:797-801` and `tui/views.py:1146`: `known_folders` skips rules with an empty `folder`, so a suggestion with `folder = ""` is never recognised as already added. `_unique` then creates `exercises-by-name-2`, `-3`, …
- Result: `studip rules suggest --yes` appends duplicate rules to the config on every run.

**B8. Short keywords do not match in the plural.** *Low. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Short keywords also match with a plural "s" (Labs, HWs, VLs); a local folder "Labs" is an exercise folder.
- Where: `suggest.py:182-190`: the whole-word check `(?![a-z])` rejects "Labs", "HWs", "VLs". A local folder "Labs" is not recognised as an exercise folder either.

**B9. Wrong hits of substring keywords.** *Low. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Added to the exclusion list: Spick-/Notiz-/Handzettel, Titel-/Kalenderblatt, Sheet music, Labor-/Praktikums-/Übungsordnung, Klausur-/Prüfungsanmeldung, Facebook, Wallpaper. A code extension anywhere in the name (`script.py.zip`) prevents "slides".
- exercises: Spickzettel, Notizzettel, Handzettel, Titelblatt, Kalenderblatt, "Sheet music", Laborordnung, Praktikumsordnung, Übungsordnung
- exams: Klausuranmeldung, Prüfungsanmeldung
- reading: Facebook, Wallpaper
- slides: `script.py.zip` (the code check only looks at the last extension)
- Mostly relevant for files in the course root or in neutral folders. Candidates for the exclusion list.

**B10. The "old exams" bonus is partly dead code.** *Low.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. "Klausur/Alt", "Exams/Old exams" are targets for exams again; archive folders elsewhere ("Folien/alt") still never receive files.
- Where: `suggest.py:338-353`: `_ARCHIVE` (alt, old, previous) excludes a folder before the `_PAST` bonus (alt, old, previous) can apply. "Exams/Old exams", "Klausur/Alt" are never chosen; only "past", "frueher" and "altklausur" work.

**B11. Course colours depend on the news fetch and are not written atomically.** *Low. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Colours are fetched right after the course list, before the announcements; no `clear()` before the update; written via tmp file + `os.replace`; `true` is no longer read as group 1.
- Where: `tui/app.py:320-336`: if `fetch_news` fails for any course with something other than 403/404 (e.g. 500, no network), the function returns before the colours are fetched.
- `tui/widgets.py:83-89`: the background thread clears the colour table and refills it without a lock (a screen drawn in between briefly shows fallback colours), and `write_text` is not atomic (no tmp + replace). Broken JSON is ignored on load, so no crash. `true` in the file is read as group 1 (`bool` is an `int`).

*Checked and fine:* 401 still stops everything; closed courses get no plan, so their files are not treated as deleted; system files are skipped (`ehthumbs.db`, `~$x.docx` and `.~lock.*#` are not, which only matters if someone uploads them); the merge output is excluded from its own inputs and written atomically.

### User experience

**U1. Enter after a finished sync runs the old plan again.** *High. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. A plan that ran (or was stopped) is marked used; Enter/`s` loads a fresh one first. Refresh shows the table again.
- Where: `tui/views.py:490-498` (with `310`, `448-450`): `self.plan` is not reset after the sync, and `_todo()` is still > 0.
- What the user sees: after "✓ Sync finished", another Enter or `s` opens "Sync now? **3 new** · 1 to the default folder" again. Confirming gives "Done. 0 downloaded, **3 failed**: refusing to overwrite unexpected file /tmp/…/Orga.pdf". The home screen then says "3 file(s) failed last time".
- Fix: set `self.plan = None` (or reload it) when the sync ends.

**U2. The next step can loop.** *High. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. The home screen counts failures from the state (`pending_failures`: not synced since, not ignored) instead of the last run's summary. Ignoring a file clears its failure, and a complete plan drops failures of files Stud.IP no longer lists. Files in back-off still count until they are retried.
- Where: `tui/app.py:243-246`, together with U1.
- "Try again" opens the preview, which says "✓ Everything is up to date". No sync runs, so the failures of the last run stay, and the home screen shows "3 file(s) failed last time" again. The same happens whenever failed files are no longer in the plan (for example after ignoring them with `i`). The most prominent box on the home screen then points to an action that cannot clear it.

**U3. Settings crash the app after the config became invalid.** *High. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. After an edit that makes the config invalid, the settings close and the home screen shows the problem; saving without a config only shows a message.
- Where: `tui/settings.py:612-625` with `561-563` and `574-580`.
- Edit the config with `e` so that it is invalid. The app says "Config now invalid", the settings stay open and `app.cfg` is `None`. The next change (e.g. `l`, Enter) ends the app with `AttributeError: 'NoneType' object has no attribute 'path'` and exit code 1.

**U4. Every highlighted announcement reloads everything from Stud.IP.** *High. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. The home screen recounts once, when the screen is left. An announcement counts as read when you move past it, press Enter on it, or leave the screen after moving through the list; the one shown on opening is not read just because it was first. `x` marks all as read.
- Where: `tui/views.py:1034-1045`, `tui/app.py:477-482`, `218`.
- Moving the cursor over an announcement calls `refresh_home(remote_changed=True)`. Each call starts a thread that fetches the course list, the news of all courses and the colours. Three cursor moves = three full fetches. With ten courses that is dozens of requests per second of scrolling.
- An announcement also counts as read as soon as the cursor touches it; the first one is marked read the moment the screen opens.

**U5. The sync screen stays broken after a run.** *Medium. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. `show_plan` shows the table and hides bar and log; `counted()` always ends the busy bar (`finally`).
- Where: `tui/views.py:478-481`, `546-551`, `522-542`.
- `r` (refresh) after a sync or a cancel reloads the plan, but the table stays hidden and the progress bar stays visible: "Everything is up to date" without a table.
- On cancel, `Locked` or `ApiError`, the bar stays half full. If the date check is aborted, the `#busy` bar stays too, because `counted()` never calls `idle()`.

**U6. Esc in the setup quits the whole app.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Esc: step 3 → 2 → 1; on step 1 "Quit the setup?".
- Where: `tui/setup.py:58` `Binding("escape", "app.quit", …)`.
- In each of the three steps Esc quits without asking, and everything typed is lost. There is no way back from step 3 to 2 or from 2 to 1. Everywhere else Esc means "back".

**U7. `?` opens the help only on the home screen.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. `?` is bound on every view and in the settings.
- Where: the help (`tui/widgets.py:245-250`) says "Everywhere: ? this help", but `?` is bound only in `tui/app.py:112`. On What's new or in the settings nothing happens.

**U8. Opening "Merge slides" already merges.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. Only `m` merges (and each sync). The "Last run" column stays empty until then.
- Where: `tui/views.py:1375-1376`: `on_mount` calls `action_merge()` as soon as one course is set up.
- A screen opened to look at writes PDFs. Section 7 says "`m` merges now".

**U9. Without network: no hint, but repeated fetches.** *Medium. Checked in code.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. One status fetch at a time; when Stud.IP cannot be reached the status line says "Stud.IP not reachable (tried again in 1 min)", and that answer is kept for a minute instead of retrying on every screen change.
- Where: `tui/app.py:331-332`, `170-171`, `218`.
- If the status fetch fails, nothing says so; "unread" and "new courses" are simply missing. Nothing is cached, so every return to the home screen starts another thread that retries with back-off (1 + 2 + 4 + 8 s) against an unreachable server. At start-up two full fetches run in parallel (`on_mount` and `on_screen_resume`).

**U10. The focused settings row has no visible background.** *Medium. Reproduced.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. The second CSS rule is gone.
- Where: `tui/settings.py:305-306`: a second rule `SettingRow:focus { background: $boost; }` overrides `$accent 25%`. Measured background: fully transparent. Only the "▸" shows where the focus is. Section 6 promises a coloured background.
- Fix: delete line 306.

**U11. Enter on the home screen can hit a different action than shown.** *Low.*
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. After the status arrives, the cursor stays on the action you chose; if you had not moved, it goes to the (possibly new) suggested step.
- Where: `tui/app.py:160-161`, `344`: when the remote status arrives, the list is refilled but the cursor index stays. Pressing Enter at that moment can trigger, say, "Choose whether to sync" instead of "Sync now". Not destructive: sync and reorganize always ask first.

**U12. The same key means different things.** *Low.*
- Status: ⏸ **Not changed, on purpose.** Renaming keys would break habits of existing users. Each screen shows its keys in the footer and `?` (now everywhere) lists them. To be decided before 1.0.
- `r`: Rules (home), Refresh (sync), Reveal (What's new). `o`: Reorganize (home), Open (What's new). `a`: Announcements (home), "all" (Courses, News). No hard conflict, but it trains the wrong reflex.

**U13. CLI inconsistencies.** *Low. Checked with `--help`.*
- Status: ◐ **Partly fixed.** All options have help texts; `studip merge --help` points to the UI. Not changed, on purpose: `-n` stays `--limit` for `history` (scripts use it); `--json` and `-p` stay options of `studip` itself (`studip --json sync`), as the help shows.
- `-n` is `--dry-run` for `sync` and `reorganize`, but `--limit` for `history`.
- No help text: `sync --semester`, `reorganize --dry-run/--yes`, `undo --yes`, `history --limit`, `news --keep-unread`, `doctor --offline`, `rules test --mime`.
- `studip sync --json` and `studip sync -p x` fail with "No such option": both only work before the subcommand (`studip --json sync`). Users try the other order first.
- `studip merge --help` still says "needs `merge` in the course config", although the UI sets it up now.

**U14. Small things.**
- Status: ✅ **Fixed** on `claude/busy-ramanujan-trqb68`, not released yet. The help says "announcements"; "skip" in the rule list is dimmed text; error messages in the sync screen show paths relative to the sync folder and `~` for the home folder.
- The help calls `a` "news", the UI "Announcements".
- Error messages in the sync screen and the rule search show full absolute paths (`tui/views.py:580`, `1142`), which wrap and are hard to read; the Doctor already shortens paths.
- `tui/views.py:1114` passes `"[dim]skip[/dim]"` through `esc()`, so the markup is shown literally.

*Checked and fine:* no tracebacks in the UI except U3; Enter is never destructive on its own (sync and reorganize confirm, with a 0.4 s Enter lock); the status symbols (✓ ✗ ! ◎) carry the meaning without colour (`NO_COLOR`); `,`, `q`, `j`, `w` can be typed in the rule input fields.

### Changed behaviour with the fixes (for the 0.4.2 notes)

- **Exit code:** `studip sync` now also exits with **1** when the plan contains files it cannot place (B5). A script that was green with 0.4.1 can turn red, rightly so. Together with section 10 the rule is now: closed courses → 0, anything that needs you → 1.
- **Merge slides** no longer merges when the screen opens; press `m` (U8).
- **Announcements** are read when you move past them, not when the screen opens (U4).
- **Config file** is written readable only by you (0600) (S6).
- **Setup:** Esc goes back a step instead of quitting (U6).
- **Rule suggestions:** with automatic sorting on, no "… by name" rules are offered any more (B6). Existing ones stay in the config; with automatic sorting on they can be deleted, because they only override step 4 with a less exact copy of it.
- **A file that appears during its download** is kept, the download is reported as failed and retried with the next sync (S3).
- **State database:** a new column `folder_type`, added automatically on first start, and schema version 2. **No way back without a step:** 0.4.1 then stops with "written by a newer studip-router". Without the version bump it would have crashed on the unknown column (checked in `state._record`). From now on unknown columns are ignored, so later additions need no bump.

### What this means for the roadmap

- Items 20 and 23 of section 15 stay urgent: U1/U2 would have shown up in one manual run against a real instance. Nothing of the above was checked against a real Stud.IP either.
- S1 and S3 contradicted `SECURITY.md`. They should be released soon as 0.4.2, with the CHANGELOG entries already written under *Unreleased*.

---

# Appendix: for contributors

Sections A to C are for people working on the code. Users can stop at section 16.

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
- **After the review (not released):** `tests/test_review_0_4_1.py` with 27 tests for the fixes of [section 16](#16-review-findings-after-041-bugs-ux-and-security) and the roadmap items built, 501 tests in total. One known flaky warning: `test_untrusted_content.py::test_tui_sync_preview_survives_markup_in_names` sometimes reports "coroutine … was never awaited" from the home screen's status thread when the test app closes (`tui/app.py`, `call_from_thread` after exit). It was seen once in several runs and does not fail the test; whether it also occurs without the fixes was not established.
