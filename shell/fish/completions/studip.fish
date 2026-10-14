# fish completions for studip-router (`studip`)
# Install: cp shell/fish/completions/studip.fish ~/.config/fish/completions/

function __studip_courses
    command studip _complete courses 2>/dev/null
end

function __studip_profiles
    command studip _complete profiles 2>/dev/null
end

set -l cmds init courses sync status reorganize undo history news merge doctor tui auth config rules schedule ignore pdf-password dates
complete -c studip -f
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -s p -l profile -xa '(__studip_profiles)' -d 'Config/state profile'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -l json -d 'Machine-readable output'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -l version -d 'Show version'

complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a init -d 'Interactive setup'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a courses -d 'List courses and sync status'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a sync -d 'Download new and changed files'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a status -d 'Open issues'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a reorganize -d 'Apply rules to synced files'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a undo -d 'Revert last reorganize'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a history -d 'Recently synced files'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a news -d 'Course announcements'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a merge -d 'Merge slide PDFs'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a doctor -d 'Check setup'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a tui -d 'Terminal UI'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a auth -d 'Stored password'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a config -d 'Configuration'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a rules -d 'Routing rules'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a schedule -d 'Background sync'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a ignore -d 'Files you never want'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a dates -d 'Changed dates and exams'
complete -c studip -n "not __fish_seen_subcommand_from $cmds" -a pdf-password -d "Unlock a course's PDFs"

# options taking a course
for sub in sync reorganize news merge
    complete -c studip -n "__fish_seen_subcommand_from $sub" -l course -xa '(__studip_courses)' -d Course
end
complete -c studip -n "__fish_seen_subcommand_from sync courses" -s s -l semester -xa 'current all' -d Semester
complete -c studip -n "__fish_seen_subcommand_from sync reorganize" -s n -l dry-run -d 'Only show the plan'
complete -c studip -n "__fish_seen_subcommand_from sync" -l include-large -d 'Ignore size limit'
complete -c studip -n "__fish_seen_subcommand_from sync" -s v -l verbose -d 'Also list skips'
complete -c studip -n "__fish_seen_subcommand_from sync" -s q -l quiet -d 'Only print problems'
complete -c studip -n "__fish_seen_subcommand_from sync" -l notify -d 'Desktop notification'
complete -c studip -n "__fish_seen_subcommand_from reorganize undo schedule" -s y -l yes -d 'Do not ask'
complete -c studip -n "__fish_seen_subcommand_from news" -l all -d 'Include read ones'
complete -c studip -n "__fish_seen_subcommand_from merge" -l force -d 'Download iCloud-only PDFs'
complete -c studip -n "__fish_seen_subcommand_from dates" -l days -x -d 'Changes of the last N days'
complete -c studip -n "__fish_seen_subcommand_from status doctor dates" -l offline -d 'Do not contact Stud.IP'
complete -c studip -n "__fish_seen_subcommand_from history" -s n -l limit -x -d 'Number of files'
complete -c studip -n "__fish_seen_subcommand_from init" -l force -d 'Overwrite config'

complete -c studip -n "__fish_seen_subcommand_from auth; and not __fish_seen_subcommand_from login status logout" -a 'login status logout'
complete -c studip -n "__fish_seen_subcommand_from config; and not __fish_seen_subcommand_from path validate edit set" -a 'path validate edit set'
complete -c studip -n "__fish_seen_subcommand_from schedule; and not __fish_seen_subcommand_from install uninstall status run-now" -a 'install uninstall status run-now'
complete -c studip -n "__fish_seen_subcommand_from ignore; and not __fish_seen_subcommand_from add list remove" -a 'add list remove'
complete -c studip -n "__fish_seen_subcommand_from pdf-password; and not __fish_seen_subcommand_from set list remove" -a 'set list remove'
complete -c studip -n "__fish_seen_subcommand_from pdf-password; and __fish_seen_subcommand_from set remove" -xa '(__studip_courses)'
complete -c studip -n "__fish_seen_subcommand_from doctor" -l test-notification -d 'Send a test notification'
complete -c studip -n "__fish_seen_subcommand_from schedule; and __fish_seen_subcommand_from install" -l every -x -d 'Minutes'
complete -c studip -n "__fish_seen_subcommand_from rules; and not __fish_seen_subcommand_from list test suggest" -a 'list test suggest'
complete -c studip -n "__fish_seen_subcommand_from rules; and __fish_seen_subcommand_from list test" -xa '(__studip_courses)'
complete -c studip -n "__fish_seen_subcommand_from rules; and __fish_seen_subcommand_from test" -s f -l folder -x -d 'Stud.IP folder'
complete -c studip -n "__fish_seen_subcommand_from rules; and __fish_seen_subcommand_from suggest" -l write -d 'Append to config'
complete -c studip -n "__fish_seen_subcommand_from rules; and __fish_seen_subcommand_from suggest" -l course -xa '(__studip_courses)'
