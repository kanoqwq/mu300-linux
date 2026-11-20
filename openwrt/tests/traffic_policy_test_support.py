"""Read-only Git arguments for native checkouts and Windows-created WSL worktrees."""
import os
import re


def git_options(repo):
    options = ['git', '-c', f'safe.directory={repo}', '-C', str(repo)]
    marker = repo / '.git'
    if os.name != 'nt' and marker.is_file():
        directory = marker.read_text().strip().removeprefix('gitdir:').strip()
        if re.match(r'^[A-Za-z]:[/\\]', directory):
            directory = '/mnt/' + directory[0].lower() + '/' + directory[3:].replace('\\', '/')
            options += ['--git-dir=' + directory, '--work-tree=' + str(repo)]
    return options
