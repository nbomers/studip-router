# Sorting, previews and interface conventions

[Quick start](../README.md) · [Configuration](CONFIGURATION.md) · [Troubleshooting](TROUBLESHOOTING.md)

## Language

The interface, CLI help and maintained reference documentation use English.
German and English course vocabulary is supported equally by automatic sorting.
Configuration keys stay in English so examples can be shared unchanged.

| Interface term | Deutsch | Meaning |
|---|---|---|
| Root | Hauptordner | Contains your course folders (`sync.root`). |
| Course target | Kursordner | A course's folder inside the root, or an explicit absolute folder. |
| Rule | Sortierregel | First matching rule decides; order matters. |
| Default folder | Auffangordner | Receives files that no rule or category places. |
| Preview | Vorschau | Plans with real server metadata; downloads nothing. |
| Test a file name | Regeltest | Simulates the metadata you enter; does not inspect a real document. |
| Reorganize / Undo | Umsortieren / Rückgängig | Moves tracked files with a journal; never replaces an occupied target. |

## Sorting priority and vocabulary

1. A course rule wins on its first match, including a skip rule.
2. Otherwise the first meaningful remote folder wins, scanning from parent to
   child at every depth. `Folien/Übungen` stays slides; a generic path such as
   `Material/Woche 1/Dateien/Übungen` reaches exercises.
3. A known Stud.IP folder type (for example HomeworkFolder) is used if no folder
   name matched. A file name is used only if neither supplied a category.
4. A corresponding local folder is required; otherwise use the default folder.
   Solutions use the exercises folder when there is no separate solutions folder.

The standard categories are exams, solutions, exercises, slides and reading.
Local and remote synonyms are shared: `Präsentationen`, `Handouts` and
`Zusammenfassungen` can be local slides targets. Own `[sync.keywords]` terms
apply to both remote names and local targets, and take precedence over default
false-friend exclusions. For example `Werkstatt` can identify exercises on both
sides. Explicit rules resolve ambiguous course-specific meanings.

`Quiz` is treated as an exam; `Flashcards` and `Lernkarten` as exercises. Data,
software and media stay in the default folder unless you add a specific rule:
there is no generally correct destination for those types across courses.
`Examples`, `Wallpapers`, `Datasheets`, `Cheat sheets` and German administrative
forms do not become exam/reading/exercise targets just because a word fragment
resembles a category. Actual exam dates reuse the exam vocabulary.

## Previews and rule tests

Preview uses the same planner and real metadata as sync. Press `d` for the
selected file's full source, target, size, MIME type, change time and description;
Ctrl+Y copies the details if the terminal permits clipboard access. Long table
cells retain both ends, so an extension or path ending remains visible. Press
`/` to filter course names and paths. Large previews show 500 rows per page;
`[` and `]` page through the complete plan. Sync still processes the complete
plan. Press Enter to return to the table, and Escape
while editing the filter to hide it. This keeps everyday navigation keys active
when the filter is not being edited.

Rules → Test a file name is a simulation. The initial size is 100,000 bytes,
MIME is `application/octet-stream`, change time is now, description and folder
type are empty. Edit those fields to test size/date/MIME/description/type rules.
A hypothetical test cannot predict local-file adoption, download failures or
server permissions; use Preview for the actual sync plan. Your rules lists every
configured match condition and respects first-match order.

## Small terminals and keyboard access

Dialogs, help and report forms are bounded by the terminal width and scroll
vertically when needed. At widths below 70 columns, announcements show one pane;
`v` switches between the list and text. Resize is supported without restarting.
For regular use 80×24 or larger offers the clearest tables. Arrow keys and Tab
work throughout; j/k are alternatives outside text inputs. Status symbols and
words accompany colors so color alone is never required to understand a result.

Settings save immediately and confirm the changed label. `u` restores the last
config setting changed in the current settings screen; it refuses if that value
was subsequently changed elsewhere. Theme changes are included. Scheduler and
account operations have their own confirmations and are not in this undo stack.
Size limit retains fractional values; press Enter to enter any finite,
non-negative MB value (0 means no size limit).

Terminal and screenreader behavior depends on the emulator's accessibility and
clipboard support. Headless layout and keyboard tests do not establish complete
screenreader compatibility; report the emulator and operating system with any
accessibility issue.
