import subprocess

GIT = ['git', '-c', 'safe.directory=*']

def git(*args, **kwargs):
    return subprocess.run(
        [*GIT, *args],
        stdout=subprocess.PIPE, text=True, check=True, **kwargs,
    ).stdout.strip()

GIT_VER = git('log', '--pretty=format:%cd [%h]', '-n', '1', cwd='scowl')
APP_GIT_VER = git('log', '--pretty=format:%cd [%h]', '-n', '1')
GIT_HASH = git('rev-parse', '--short', 'HEAD', cwd='scowl')

def git_revision_str(sep='\n'):
    return (f"ESDB Git Revision: {GIT_VER}{sep}"
            f"App Git Revision: {APP_GIT_VER}")

GIT_FOOTER = f"""<p class=git-revision>
{GIT_VER} (ESDB); {APP_GIT_VER} (App)
<p>
"""
