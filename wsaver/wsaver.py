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
from typing import Optional

from colorama import Fore, Style, init
from frosch import hook

VERSION = "1.0.1"
WFILE = Path(os.environ["HOME"], ".windowlist")
DEBUG = 3
WIN_TITLE_HEIGHT = 24
lpg = namedtuple("lpg", ["win_id", "desktop_id", "pid", "x", "y", "width", "height", "uname", "title"])


def dprint(msg: str, lvl: int = 0) -> None:
    """This function sends its message to STDERR only if DEBUG is set to a value greater than or equal to lvl."""
    if lvl <= DEBUG:
        print("%s%s%s" % (Fore.RED, msg, Style.RESET_ALL), file=sys.stderr)


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


def get_res() -> tuple:
    """Gets the current resolution from xrandr.  Never called now?"""
    xrandr = get_output("xrandr")
    # Find X and Y from "current XXXX x YYYY," in this output
    res = re.search(r"current (\d+) x (\d+),", xrandr, re.I).groups()
    dprint(f"{res=}", 2)
    return res


def get_vpdata() -> list:
    """Get "workspace correction" (vector?) data from wmctrl.
    Seems to always be [0, 0]?"""
    vp_data = get_output("wmctrl -d").splitlines()
    curr_vpdata = [int(n) for v in vp_data for n in v.split()[5].split(",") if n != "N/A"]
    dprint(f"{curr_vpdata=}", 2)
    return curr_vpdata


def pid_name(pid: int) -> str:
    """Return the process name for the given pid."""
    return check_output(["/usr/bin/ps", "-q", pid, "-o", "comm="]).decode("utf-8").strip()


def read_windows(opts: argparse.Namespace) -> None:
    """Get the currently-open windows for output to file or STDOUT"""
    w_list = [lpg._make(w.split(maxsplit=8)) for w in get_output("wmctrl -lpG").splitlines()]
    relevant = [
        [w.pid, [int(n) for n in [w.desktop_id, w.x, w.y, w.width, w.height]]] for w in w_list if check_window(w.win_id)
    ]
    dprint(f"w_list={pformat(w_list)}", 1)
    for i, r in enumerate(relevant):
        relevant[i] = pid_name(r[0]) + " " + str((" ").join([str(n) for n in r[1]]))
    if opts.no_file:
        for r in relevant:
            print(r)
    else:
        with open(WFILE, "wt") as out:
            for r in relevant:
                out.write(f"{r}\n")


def read_window_ids() -> list:
    """Get the currently-open windows for comparison to the saved list."""
    w_list = [lpg._make(w.split(maxsplit=8)) for w in get_output("wmctrl -lpG").splitlines()]
    dprint(f"w_list={pformat(w_list)}", 1)
    relevant = [[w.pid, w.win_id] for w in w_list if check_window(w.win_id)]
    dprint(f"{relevant=}", 2)
    # Replace PID with process name in the relevant list.
    for i, r in enumerate(relevant):
        relevant[i][0] = pid_name(r[0])
    dprint(f"{relevant=}", 1)
    return relevant


def open_appwindow(app, dtop, x, y, w, h):
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

    t = 0
    while t < 30:
        ws2 = [w.split()[0:3] for w in get_output("wmctrl -lp").splitlines() if w not in ws1]
        procs = [[(p, w[0]) for p in get_output("ps -e ww").splitlines() if app in p and w[2] in p] for w in ws2]
        if len(procs) > 0:
            sleep(0.5)
            w_id = procs[0][0][1]
            reposition_window(w_id, dtop, x, y, w, h)
            break
        sleep(0.5)
        t += 1


def reposition_window(w_id, dtop, x, y, w, h) -> None:
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
    wlist = namedtuple("wlist", ["app", "dtop", "x", "y", "w", "h"])
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
