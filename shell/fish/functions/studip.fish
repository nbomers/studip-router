# Optional wrapper: only needed if `studip` is not on your PATH
# (e.g. you run it from a source checkout). Set STUDIP_ROUTER_SRC to that checkout.
function studip --description 'Stud.IP sync with routing rules'
    if set -q STUDIP_ROUTER_SRC
        uv run --project $STUDIP_ROUTER_SRC studip $argv
    else if command -q studip
        command studip $argv
    else
        echo "studip-router is not installed. Run: uv tool install studip-router" >&2
        return 127
    end
end
