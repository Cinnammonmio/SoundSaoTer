"""PyInstaller runtime hook — runs before every other hook and before soundboard.py.

A onefile exe started by a PyInstaller app inherits that app's environment and reuses
its unpacked folder. The updater of SoundSaoTer 1.5.x and older starts the new exe that
way, so right after an update the new version would run on the OLD version's DLLs and
data — and crash in another runtime hook before our own code even starts.

build.py stamps the version into the bundle. If the unpacked folder carries another
stamp (or none), start this exe again with a clean environment and quit. Only builtin
and pure-python modules are used here: the folder may be missing anything else.
"""
import os
import sys


def _fresh_start():
    if os.environ.pop('SOUNDSAOTER_RELAUNCHED', None):
        return
    if len(sys.argv) > 1 and sys.argv[1] == '--yt-worker':
        return                      # clipper jobs share their parent's files on purpose
    import version
    try:
        with open(os.path.join(sys._MEIPASS, 'build_stamp.txt'), encoding='utf-8') as fh:
            if fh.read().strip() == version.VERSION:
                return
    except OSError:
        pass
    import subprocess
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1', SOUNDSAOTER_RELAUNCHED='1')
    subprocess.Popen([sys.executable] + sys.argv[1:], cwd=os.path.dirname(sys.executable),
                     close_fds=True, env=env)
    os._exit(0)


_fresh_start()
