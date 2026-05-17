#!/usr/bin/env python3

from flask import Flask, request, Response, abort, redirect
from urllib.parse import urlencode
from markupsafe import Markup, escape
from datetime import datetime, timezone
from pathlib import Path
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import libscowl
from libscowl import clusterKey, validateWord

# BOTH A COMMAND LINE AND FLASK APP

# test flash app with:
#   flask --app speller-lookup run -p 5000
#   http://127.0.0.1:5000/speller-lookup

DB_PATH = 'scowl.db'

DICTS = {
    'en_US':       'en_US',
    'en_US_large': 'en_US-large',
    'en_GB_ise':   'en_GB-ise',
    'en_GB_ize':   'en_GB-ize',
    'en_GB_large': 'en_GB-large',
    'en_CA':       'en_CA',
    'en_CA_large': 'en_CA-large',
    'en_AU':       'en_AU',
    'en_AU_large': 'en_AU-large',
}

LARGER_DICT = {
    'en_US':     'en_US_large',
    'en_GB_ise': 'en_GB_large',
    'en_GB_ize': 'en_GB_large',
    'en_CA':     'en_CA_large',
    'en_AU':     'en_AU_large',
}

def init(conn):
    conn.execute("""create temp table input (
      word text primary key,
      word_key text not null
    ) without rowid""")

def add_word(conn, word):
    word = word.strip()
    if not word:
        return
    validateWord(word)
    key = clusterKey(word).decode('ascii')
    conn.execute("insert or ignore into input values (?, ?)", (word, key))

def proc(conn, dict_name):
    with open('speller-lookup.sql') as f:
        conn.executescript(f.read())

    conn.execute("""create temp table status (
      word text primary key,
      status text not null
    ) without rowid""")

    for status_code, table in [('-', 'exact'),
                               ('!', 'filtered'),
                               ('v', 'variant_in_dict'),
                               ('o', 'other_form_in_dict'),
                               ('~', 'inexact')]:
        conn.execute(f"insert or ignore into status select orig_word, ? from {table} where {dict_name}",
                     (status_code,))
    conn.execute("insert or ignore into status select word, '+' from input")

class TableCell:
    # slots: 'body' # noescape
    def __init__(self, body):
        self.body = body

def build_rows(conn, dict_key):
    proc(conn, dict_key)
    esdb_exact = {w for w, in conn.execute("select orig_word from in_esdb where exact group by orig_word")}
    larger_dict = LARGER_DICT.get(dict_key)
    if larger_dict:
        larger_dict_set = {w for w, in conn.execute(f"select orig_word from exact where {larger_dict}")}
    else:
        larger_dict_set = set()
    rows = []
    dict_display = DICTS[dict_key]
    for status, word in conn.execute("select status, word from status order by word"):
        cols = [word]
        if status == '-':
            cols.append(f'in {dict_display}')
        elif status == '!':
            cols.append('filtered')
        else:
            cols.append('missing')

        notes = []
        if status in ('!', '+','v','o') and word in larger_dict_set:
            notes.append(f'in {DICTS[larger_dict]}')

        if status == '+':
            if word not in larger_dict_set and word in esdb_exact:
                notes.append('in ESDB')
        elif status == 'v':
            notes.append('variant')
        elif status == '~':
            notes.append('inexact match found')
            if word in larger_dict:
                notes.append(f'exact match in {DICTS[larger_dict]}')
            elif word in esdb_exact:
                notes.append('exact match in ESDB')
        elif status == 'o':
            notes.append('missing form')

        cols.append(TableCell(';<br>'.join(escape(line) for line in notes)))
        rows.append(cols)
    return rows

def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <dict_name>", file=sys.stderr)
        sys.exit(1)

    dict_name = sys.argv[1].replace('-', '_')
    if dict_name not in DICTS:
        print(f"Unknown dict '{dict_name}'. Valid: {', '.join(sorted(DICTS))}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect('file:scowl.db?mode=ro', uri=True)

    init(conn)

    for line in sys.stdin:
        try:
            add_word(conn, line)
        except ValueError:
            word = line.strip()
            print(f"Warning: skipping invalid word: {word!r}", file=sys.stderr)
            continue

    proc(conn, dict_name)

    for status, word in conn.execute("select status, word from status order by word"):
        print(f"{status} {word}")

if __name__ == '__main__':
    main()
    sys.exit(0)
else:
    app = Flask(__name__)

    with open('style.css') as f:
        INLINE_STYLE = f'<style>\n{f.read()}</style>'

    GIT_VER = subprocess.run(
        ['git', 'log', '--pretty=format:%cd [%h]', '-n', '1'],
        cwd='scowl', stdout=subprocess.PIPE, text=True, check=True
    ).stdout.strip()

    GIT_HASH = subprocess.run(
        ['git', 'rev-parse', '--short', 'HEAD'],
        cwd='scowl', stdout=subprocess.PIPE, text=True, check=True,
    ).stdout.strip()


def make_option_list(name, default, keys, values):
    parts = [f'<select name="{escape(name)}">']
    for k in keys:
        selected = ' selected' if str(k) == str(default) else ''
        parts.append(f'  <option value="{escape(k)}"{selected}>{escape(values[k])}</option>')
    parts.append('</select>')
    return Markup('\n'.join(parts))

def render_form():
    return f'''<html>
<head>
<title>ESDB Speller Dict Lookup</title>
{INLINE_STYLE}
</head>
<body>
<p>
Use this tool to lookup if a list of words is in an official ESDB created speller dictionary.
<form method="post">
<textarea name="words" rows=40 cols=30>
</textarea>
<br>
{make_option_list('dict', 'en_US', DICTS.keys(), DICTS)}
<button type="submit">Submit</button>
</form>
<p style="color: #808080;">
{GIT_VER}
</body>'''


def render_error(bad_lines):
    items = ''.join(f'<li>{escape(w)}</li>' for w in bad_lines)
    body = f'<p>Invalid word(s):</p><ul>{items}</ul>' if bad_lines else '<p>No valid words provided.</p>'
    return f'''<html>
<head>
<title>ESDB Speller Dict Lookup</title>
{INLINE_STYLE}
</head>
<body>
{body}
</body>'''

def render_cell(value):
    if isinstance(value, TableCell):
        return Markup(f'<td>{value.body}</td>')
    return Markup(f'<td>{escape(value)}</td>')


def render_result(dict_display, rows, skipped):
    warn_html = ''
    if skipped:
        items = ''.join(f'<li>{escape(w)}</li>' for w in skipped)
        warn_html = f'<p>Skipped invalid word(s):</p><ul>{items}</ul>\n'
    tbody = ''.join(
        Markup(f'<tr>{render_cell(word)}{render_cell(status)}{render_cell(notes)}</tr>')
        for word, status, notes in rows
    )
    table = f'''<table border=1 cellpadding=2>
<thead><tr><th>Word</th><th>Status</th><th>Notes</th></tr></thead>
<tbody>{tbody}</tbody>
</table>'''
    return f'''<html>
<head>
<title>ESDB Speller Dict Lookup</title>
{INLINE_STYLE}
</head>
<body>
<p>Results for <b>{escape(dict_display)}</b>:</p>
{warn_html}{table}
<p style="color: #808080;">
{GIT_VER}
</body>'''


def split_lines(words_raw):
    lines = []
    for line in io.StringIO(words_raw):
        line = line.strip()
        if not line:
            continue
        if len(lines) >= 1000:
            abort(400, 'Too many words: the limit is 1000')
        lines.append(line)
    return lines

def parse_words(words_raw):
    words = []
    skipped = []
    for word in split_lines(words_raw):
        try:
            if len(word) > 60:
                raise ValueError
            validateWord(word)
        except ValueError:
            skipped.append(word)
            if len(skipped) >= 3:
                abort(Response(render_error(skipped), status=400,
                               content_type='text/html; charset=UTF-8'))
            continue
        words.append(word)

    if not words:
        abort(Response(render_error(skipped), status=400,
                       content_type='text/html; charset=UTF-8'))
    return words, skipped


def process_lookup(words, dict_key, skipped):
    conn = sqlite3.connect('file:scowl.db?mode=ro', uri=True)
    init(conn)

    for word in words:
        key = clusterKey(word).decode('ascii')
        conn.execute("insert or ignore into input values (?, ?)", (word, key))

    rows = build_rows(conn, dict_key)

    return Response(render_result(DICTS[dict_key], rows, skipped),
                    content_type='text/html; charset=UTF-8')


@app.route('/speller-lookup', methods=['GET', 'POST'])
def speller_lookup():
    if not request.values:
        return Response(render_form(), content_type='text/html; charset=UTF-8')

    words_raw = request.values.get('words', '')
    dict_key = request.values.get('dict', 'en_US')

    if dict_key not in DICTS:
        abort(400, 'Invalid dict')

    words, skipped = parse_words(words_raw)

    if request.method == 'POST' and len(words) + len(skipped) <= 5:
        return redirect('/speller-lookup?' + urlencode(
            [('words', '\n'.join(words + skipped)), ('dict', dict_key)]))

    return process_lookup(words, dict_key, skipped)
