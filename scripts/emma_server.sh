#!/system/bin/sh
# Start / stop / status of the Emma server that runs ON the robot.
# Run as root (su 0): by `scripts/robot_server.py start|stop|status` from a PC,
# and by the face app's ServerLauncher at start-up and as a watchdog -- one
# implementation, so the two can never disagree about how the server runs.
#
# The server itself runs as nobody (9999) + inet (3003): never root, and this
# build's su refuses every uid but root and shell, so it cannot climb back.
# EMMA_DNS (Android's DNS servers, from the app) is passed through to
# scripts/robot_dns.py, digits/dots/commas only. The rest of the caller's
# environment is inherited (su keeps it) -- python-dotenv lets an existing
# variable win over .env, so a caller must not export setting names; neither
# the app nor `adb shell` does.
#
# ROOT NEVER OPENS, READS OR WALKS ANYTHING UNDER code/data/ (review, 9 Oct).
# data/ belongs to the server, so the server decides what every name in it
# is -- a file, or a symlink to any path on the robot. The first version did
# `>> code/data/server.log` in this root shell: a server that swapped
# server.log for a symlink got root to open the file it named for append and
# fill it with the server's own output -- this script, which root runs every
# 30 s, included. Its `chmod -R` over data/ walked the same tree as root. The
# whole data/ half of a start (log rotation, permissions, the log itself) is
# now `run`, executed AS the server user, where a symlink can only reach what
# that user could reach anyway. Root keeps to run/ (root 0700).
B=/data/local/emma-server
R=$B/runtime
C=$B/code
PIDF=$B/run/server.pid
LOCK=$B/run/start.lock
LAUNCH=$B/run/launch.log      # su's own complaints; the server's log is data/server.log

# su hands its child umask 0. Measured on the robot 9 Oct: the server's own
# files came out world-writable (code/data/logs drwxrwxrwx, the turn logs
# -rw-rw-rw-), harmless only because $B is root:nobody 0750. 027: what the
# server writes is its own (nobody) and nobody else's. Set again in `run`,
# because su may reset it.
umask 027

ours() {   # the pid in the file is our server (or its launch shell), not a reused number
  p=$(cat "$PIDF" 2>/dev/null) || return 1
  [ -n "$p" ] && [ -r /proc/$p/cmdline ] || return 1
  # the pid is the launch shell (`... emma_server.sh run`) for the moment
  # before it execs Python, then the server itself
  case "$(tr '\0' ' ' < /proc/$p/cmdline)" in
    *scripts/emma_server.py*|*"scripts/emma_server.sh run"*) ;;
    *) return 1 ;;
  esac
  echo "$p"
}

case "$1" in
  start)
    if p=$(ours); then echo "running $p"; exit 0; fi
    [ -x $R/py ] || { echo "no runtime: robot_server.py install"; exit 2; }
    [ -f $C/.env ] || { echo "no .env: robot_server.py secrets"; exit 2; }
    mkdir -p $B/run && chmod 0700 $B/run
    # One starter at a time. The app's watchdog and a PC `robot_server.py
    # start` can arrive together: both see "not running", the second server
    # loses :8001 and exits, and ITS pid is the one left in the file -- the
    # winner then runs untracked (stop cannot find it) and every watchdog tick
    # starts another loser. mkdir is atomic; a lock older than a minute
    # belongs to a starter that died half way.
    if ! mkdir "$LOCK" 2>/dev/null; then
      age=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
      [ "$age" -gt 60 ] && rmdir "$LOCK" 2>/dev/null
      mkdir "$LOCK" 2>/dev/null || { echo "starting (another start is in progress)"; exit 0; }
    fi
    trap 'rmdir "$LOCK" 2>/dev/null' EXIT
    if p=$(ours); then echo "running $p"; exit 0; fi      # the other starter got there first
    # Started by the face app, this shell is inside the APP's process group
    # (cgroup uid_<app>/pid_<app>). Android kills that whole group when the app
    # dies or is force-stopped (every APK reinstall), and its phantom-process
    # killer ends heavy children of an app in the background -- but the server
    # serves the page and must outlive the app. So the shell moves itself to
    # the root cgroup before forking it. Best effort: if the kernel refuses,
    # the app's watchdog starts the server again when the app comes back.
    for g in /sys/fs/cgroup /acct; do
      [ -f $g/cgroup.procs ] && { echo $$ > $g/cgroup.procs; } 2>/dev/null
    done
    # EMMA_DNS reaches the server through su's environment, never through a
    # command string a shell parses again
    EMMA_DNS=$(echo "$EMMA_DNS" | tr -cd '0-9.,')
    export EMMA_DNS
    cd $B || exit 2
    nohup /system/xbin/su 9999,9999,3003 /system/bin/sh $C/scripts/emma_server.sh run > "$LAUNCH" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 1
    if p=$(ours); then echo "started $p"; else echo "failed to start (see robot_server.py logs; su: $LAUNCH)"; exit 1; fi
    ;;
  run)
    # The server's own half of `start`, as the server user (never root):
    # everything here touches data/, which is the server's.
    if [ "$(id -u)" = 0 ]; then echo "run is the server user's half of start: never as root"; exit 64; fi
    umask 027
    cd $C || exit 2
    # A log this user cannot write is one root created (the root-side `>>`
    # of the first version): renaming needs only data/, which is ours.
    if [ -e data/server.log ] && [ ! -w data/server.log ]; then mv -f data/server.log data/server.log.1; fi
    # keep one previous log; a server that crash-loops must not fill the disk
    if [ -f data/server.log ] && [ "$(wc -c < data/server.log)" -gt 5000000 ]; then
      mv -f data/server.log data/server.log.1
    fi
    # Files written before the umask above was set (the logs, enrolled faces)
    # lose their group/other bits; the owner (9999) keeps all of its own.
    chmod -R g-w,o-rwx data 2>/dev/null
    # -s: no user site-packages. HOME is data/ (the launcher's), and a
    # ~/.local/.../site-packages there would be code the server runs at every
    # boot from the one directory a redeploy never replaces.
    exec $R/py -s scripts/emma_server.py >> data/server.log 2>&1
    ;;
  stop)
    p=$(ours) || { echo "not running"; rm -f "$PIDF"; exit 0; }
    kill -TERM "$p"
    i=0
    while [ $i -lt 20 ] && [ -d /proc/$p ]; do sleep 0.5; i=$((i + 1)); done
    if [ -d /proc/$p ]; then kill -KILL "$p"; echo "killed $p"; else echo "stopped $p"; fi
    rm -f "$PIDF"
    ;;
  restart)
    sh "$0" stop
    sh "$0" start
    ;;
  status)
    if p=$(ours); then echo "running $p"; else echo "not running"; exit 3; fi
    ;;
  *)
    echo "usage: emma_server.sh start|stop|restart|status"; exit 64
    ;;
esac
