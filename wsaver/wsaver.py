"""Script for saving and restoring window positions on a Linux desktop"""
import argparse
import os
import re
import sys
from collections import namedtuple
from pathlib import Path
from pprint import pformat
from subprocess import Popen, call, check_output
from time import sleep
from typing import Any, Optional

from colorama import Fore, Style, init
from frosch import hook

# FIXME!  For multiple windows like terminals, need a way to try to match up window IDs with PIDs,
#  so that windows won't get scrambled around when they are repositioned/resized.
VERSION = "1.0.2"
WFILE = Path(os.environ["HOME"], ".windowlist")
DEBUG = 1
WIN_TITLE_HEIGHT = 24
# A namedtuple to describe the output of "wmctrl -lpG".
lpg = namedtuple("lpg", ["win_id", "dtop", "pid", "x", "y", "width", "height", "uname", "title"])
# A namedtuple to describe the contents of the .windowlist file.
wlist = namedtuple("wlist", ["app", "pid", "dtop", "x", "y", "w", "h"])


def dprint(msg: str, lvl: int = 0) -> None:
    """This function sends its message to STDERR only if DEBUG is set to a value greater than or equal to lvl."""
    if lvl <= DEBUG:
        print(f"{Fore.RED}{msg}\n{Fore.YELLOW}======={Style.RESET_ALL}", file=sys.stderr)


def get_output(command: str) -> str:
    """Return the output from a shell command."""
    return check_output(["/bin/bash", "-c", command]).decode("utf-8")


def goto_workspace(ws_num: int = 0) -> None:
    """Jump to a given workspace.  Some things only seem to work in WS 0."""
    get_output(f"wmctrl -s {ws_num}")


def check_window(w_id: str) -> bool:
    w_type = get_output(f"xprop -id {w_id}")
    true_types = [" _NET_WM_WINDOW_TYPE_NORMAL", '"xterm"']
    return any(t in w_type for t in true_types)


def get_res() -> Optional[tuple]:
    """Gets the current resolution from xrandr.  Never called now?"""
    xrandr = get_output("xrandr")
    res = None
    # Find X and Y from "current XXXX x YYYY," in this output
    if found := re.search(r"current (\d+) x (\d+),", xrandr, re.I):
        res = found.groups()
    dprint(f"{res=}", 2)
    return res


def get_vpdata() -> list:
    """Get "workspace correction" (vector?) data from wmctrl.
    Seems to always be [0, 0]?
    According to "man wmctrl", this is the "viewport position",
    so it seems unlikely to be anything other than [0, 0]."""
    vp_data = get_output("wmctrl -d").splitlines()
    curr_vpdata = [int(n) for v in vp_data for n in v.split()[5].split(",") if n != "N/A"]
    dprint(f"{curr_vpdata=}", 2)
    return curr_vpdata


def pid_name(pid: int) -> str:
    """Return the process name for the given pid."""
    return check_output(["/usr/bin/ps", "-q", str(pid), "-o", "comm="]).decode("utf-8").strip()


def format_relevant(r: list) -> str:
    """Formats one entry from the weird "relevant" list for nice output."""
    # This is app_name, PID, desktop_num, x, y, w, h.
    return f"{r[0]:<15} {r[1]:>8} {r[2][0]:>2} {r[2][1]:>5} {r[2][2]:>5} {r[2][3]:>5} {r[2][4]:>5}"


def read_windows(opts: argparse.Namespace) -> None:
    """Get the currently-open windows for output to file or STDOUT."""
    w_list = [lpg._make(w.split(maxsplit=8)) for w in get_output("wmctrl -lpG").splitlines()]
    dprint(f"w_list={pformat(w_list)}", 1)
    relevant = [
        [pid_name(w.pid), w.pid, [int(n) for n in [w.dtop, w.x, w.y, w.width, w.height]]]
        for w in w_list
        if check_window(w.win_id)
    ]
    dprint(f"relevant={pformat(relevant)}", 1)
    if opts.no_file:
        for r in relevant:
            print(format_relevant(r))
    else:
        with open(WFILE, "wt") as out:
            for r in relevant:
                out.write(f"{format_relevant(r)}\n")


def read_window_ids() -> dict[Any, Any]:
    """Get the currently-open windows for comparison to the saved list."""
    w_list = [lpg._make(w.split(maxsplit=8)) for w in get_output("wmctrl -lpG").splitlines()]
    dprint(f"w_list={pformat(w_list)}", 1)
    running: dict = {pid_name(w.pid): [] for w in w_list if check_window(w.win_id)}
    for w in sorted(w_list, key=lambda p: int(p.pid)):
        if check_window(w.win_id):
            running[pid_name(w.pid)].append([w.pid, w.win_id])
    dprint(f"{running=}", 1)
    return running


def open_appwindow(app, dtop, x, y, w, h):  # noqa: PLR0913
    """Open apps from the saved list that are not currently running."""
    ws1 = get_output("wmctrl -lp")
    # fix command for certain apps that open in new tab by default
    if app == "gedit":
        option = "--new-window"
    else:
        option = ""
    # fix command if process name and command to run are different
    if "gnome-terminal" in app:
        app = "gnome-terminal"
    elif "chrome" in app:
        app = "/usr/bin/google-chrome-stable"

    Popen(["/bin/bash", "-c", f"{app} {option}"])
    # fix exception for Chrome (command = google-chrome-stable, but processname = chrome)
    app = "chrome" if "chrome" in app else app

    MAX_TRIES = 30
    t = 0
    while t < MAX_TRIES:
        ws2 = [w.split()[0:3] for w in get_output("wmctrl -lp").splitlines() if w not in ws1]
        procs = [[(p, w[0]) for p in get_output("ps -e ww").splitlines() if app in p and w[2] in p] for w in ws2]
        if len(procs) > 0:
            sleep(0.5)
            w_id = procs[0][0][1]
            reposition_window(w_id, dtop, x, y, w, h)
            break
        sleep(0.5)
        t += 1


def reposition_window(w_id, dtop, x, y, w, h) -> None:  # noqa: PLR0913
    """Move and resize windows using wmctrl."""
    v_offset = 35
    cmds = []
    cmds.append(f"wmctrl -ir {w_id} -b remove,maximized_horz")
    cmds.append(f"wmctrl -ir {w_id} -b remove,maximized_vert")
    cmds.append(f"wmctrl -ir {w_id} -t {dtop}")
    cmds.append(f"wmctrl -ir {w_id} -e 0,{x},{int(y) - v_offset},{w},{h}")
    for cmd in cmds:
        call(["/bin/bash", "-c", cmd])


def run_remembered(opts: argparse.Namespace) -> None:
    """Open and/or resize app windows based on the saved list."""
    res = get_vpdata()
    running = read_window_ids()
    dprint(f"{running=}", 1)
    if WFILE.is_file():
        with open(WFILE, "rt") as inp:
            while this_line := inp.readline():
                f = wlist._make(this_line.split())
                x = str(int(f.x) - res[0])
                y = str(int(f.y) - res[1] - WIN_TITLE_HEIGHT)
                apps = [a[0] for a in running]
                if f.app in apps:
                    idx = apps.index(f.app)
                    if not opts.dry_run:
                        reposition_window(running[idx][1], f.dtop, x, y, f.w, f.h)
                    running.pop(idx)
                elif not opts.dry_run:
                    open_appwindow(f.app, f.dtop, x, y, f.w, f.h)
    else:
        dprint(f"File not found: {WFILE!r}")


def main(args: Optional[list] = None) -> int:
    """This function collects arguments and configuration, and starts the crawling process."""
    # Run frosch's hook.
    hook()
    # Run colorama's init.
    init()

    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(description=f"WSaver {VERSION}")
    parser.add_argument(
        "-s",
        "--save",
        action="store_true",
        default=False,
        dest="save_windows",
        required=False,
        help=f"Get list of currently-open windows, save in file {WFILE!r}",
    )
    parser.add_argument(
        "-n",
        "--no-file",
        action="store_true",
        default=True,
        dest="no_file",
        required=False,
        help="Send output to STDOUT rather than a file",
    )
    parser.add_argument(
        "-r",
        "--restore",
        action="store_true",
        default=False,
        dest="restore_windows",
        required=False,
        help="Move windows back to their expected positions",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        default=False,
        dest="dry_run",
        required=False,
        help="Generate output without actually doing anything",
    )
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    opts = parser.parse_args(args)
    args = opts.rest

    if opts.save_windows:
        # goto_workspace(0)
        # sleep(1)
        read_windows(opts)
    elif opts.restore_windows:
        # goto_workspace(0)
        # sleep(1)
        run_remembered(opts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
