#!/usr/bin/env python3

from flask import Flask, request, Response, abort, redirect
from urllib.parse import urlencode, quote as urlescape
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
from collections import namedtuple
import libscowl
from libscowl import clusterKey, validateWord

# BOTH A COMMAND LINE AND FLASK APP

# Command line usage:
#   ./speller-lookup.py DB DICT < WORDLIST

# Flask app test:
#   flask --debug --app speller-lookup run -p 5000
#   http://127.0.0.1:5000/speller-lookup

DB_PATH = 'scowl.db'
if __name__ == '__main__' and len(sys.argv) > 1:
    DB_PATH=sys.argv[1]
DB_PATH=f"file:{urlescape(DB_PATH, safe='/')}?mode=ro"

# Used for startup of flask app and then deleted, each thread has it's own connection
# connection reused for command line app
conn = sqlite3.connect(DB_PATH, uri=True)

DictInfo = namedtuple('DictInfo', ['name', 'larger', 'spellings', 'lookup_order'])

DICTS = {
    'en_US':       DictInfo('en_US',       'en_US_large', "('_','A')", 'BZCD'),
    'en_US_large': DictInfo('en_US-large', None,          "('_','A')", 'BZCD'),
    'en_GB_ise':   DictInfo('en_GB-ise',   'en_GB_large', "('_','B')", 'ZACD'),
    'en_GB_ize':   DictInfo('en_GB-ize',   'en_GB_large', "('_','Z')", 'BACD'),
    'en_GB_large': DictInfo('en_GB-large', None,          "('_','B','Z')", 'ZACD'),
    'en_CA':       DictInfo('en_CA',       'en_CA_large', "('_','C')", 'BAZD'),
    'en_CA_large': DictInfo('en_CA-large', None,          "('_','C')", 'BAZD'),
    'en_AU':       DictInfo('en_AU',       'en_AU_large', "('_','D')", 'BZAC'),
    'en_AU_large': DictInfo('en_AU-large', None,          "('_','D')", 'BZAC'),
}

SPELLINGS = {
    'A': 'American',
    'B': 'British',
    'C': 'Canadian',
    'D': 'Australian',
    'Z': 'Oxford',
}

VARIANTS = {
    level: descr if descr == 'variant' else f"{descr} variant"
      for level, descr in conn.execute("select variant_level, variant_descr from variant_levels")
}

BASE_POSES = {
    base_pos: descr
      for base_pos, descr in conn.execute("select base_pos, descr from base_poses")
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
    key = clusterKey(word).decode('iso-8859-1')
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
    def __init__(self, body, css_class=None):
        self.body = body
        self.css_class = css_class

def nv_word_variant(conn, dict_key, word):
    return conn.execute(f"""
with
  annotated as (
    select word_id, variant_level, min(variant_level) over () as min_variant_level, nv_word
      from variant_in_dict_info
     where word = ? and spelling = nv_spelling and nv_spelling in {DICTS[dict_key].spellings} and {dict_key})
select group_concat(distinct word_id) as word_ids, min(variant_level) as variant_level, nv_word
  from annotated
 where variant_level = min_variant_level
group by nv_word;
    """, (word,))

def nv_word_other(conn, dict_key, word):
    return conn.execute(f"""
with
  annotated as (
    select word_id, spelling, variant_level, min(variant_level) over () as min_variant_level, nv_word
      from variant_in_dict_info
     where word = ? and nv_spelling in {DICTS[dict_key].spellings} and {dict_key})
select group_concat(distinct word_id) as words_ids, variant_level, group_concat(distinct spelling) as spellings, nv_word
  from annotated
 where variant_level = min_variant_level
group by nv_word;
    """, (word,))

def missing_form(conn, dict_key, word):
    return conn.execute(f"""
select distinct word_id = lemma_id as is_lemma, pos == 'ns' as is_plural
  from other_form_in_dict_info
 where word = ? and {dict_key}
    """, (word,))

def nv_word_all(conn, dict_key, word):
    return conn.execute(f"""
select group_concat(distinct word_id) as words_ids, word
  from variant_in_dict where {dict_key}
  and orig_word = ?
group by word;
    """, (word,))

def get_entry_info(conn):
    return conn.execute("""
with lemma_ids as (
  select lemma_id, min(order_num) as order_num
    from matching_entries cross join words using (word_id)
   group by lemma_id)
select lemma, lemmas.base_pos, pos_class, defn_note, usage_note
  from lemma_ids l
  join lemmas using (lemma_id)
  join base_poses bp using (base_pos)
 order by l.order_num, lemma, defn_note, bp.order_num, pos_class
    """)

def format_lemma_info(lemma, base_pos, pos_class, defn_note, usage_note):
    out = lemma
    if base_pos and pos_class:
        out += f' <{base_pos}/{pos_class}>'
    elif base_pos:
        out += f' <{base_pos}>'
    if defn_note:
        out += f' {{{defn_note}}}'
    if usage_note:
        out += f' ({usage_note})'
    return out

def build_rows(conn, dict_key):
    proc(conn, dict_key)
    conn.execute("attach database 'history.db' as history")
    esdb_exact = {w for w, in conn.execute("select orig_word from in_esdb where exact group by orig_word")}
    larger_key = DICTS[dict_key].larger
    if larger_key:
        larger_dict = {w for w, in conn.execute(f"select orig_word from exact where {larger_key}")}
    else:
        larger_dict = set()
    rows = []
    footnotes = set()
    poses_used = set()
    dict_display = DICTS[dict_key].name
    conn.execute("create temp table matching_entries (word_id integer primary key, order_num integer not null)")
    for code, word in conn.execute("select status, word from status order by word"):
        conn.execute("delete from matching_entries")
        notes = []
        add_remove = next(conn.execute("select current_state, hash, author_date, release_tag from word_state where dict=? and word = ?", 
                                       (DICTS[dict_key].name, word)), None)
        if add_remove:
            state, hash, date, tag = add_remove
            date_str = datetime.fromisoformat(date).date()
            hash_url = f'https://github.com/en-wl/wordlist/commit/{hash}'
            note = Markup(f'<span class=mod-what>{"added" if state == "add" else "removed"}</span>')
            if tag:
                note += Markup(f' <span class=mod-in>in {escape(tag)}</span>')
            note += Markup(f' on {escape(str(date_str))} (<a href="{escape(hash_url)}">{escape(hash[:7])}</a>)')
            notes.append(note)
        def check_larger(msg):
            if word not in larger_dict: return False
            notes.append(msg.format(DICTS[larger_key].name))
            conn.execute(f"insert or ignore into matching_entries select word_id, 2 from exact where {larger_key} and orig_word = ?", (word,))
            return True
        def check_esdb(msg):
            if word not in esdb_exact: return False
            notes.append(msg)
            conn.execute("insert or ignore into matching_entries select word_id, 2 from in_esdb where exact and orig_word = ?", (word,))
            return True
        if code == '-':
            status = f'in {dict_display}'
            conn.execute(f"insert or ignore into matching_entries select word_id, 1 from exact where {dict_key} and orig_word = ?", (word,))
        elif code == '!':
            status = 'filtered'
            if word in larger_dict:
                notes.append(f'(in {DICTS[larger_key].name})')
            conn.execute(f"insert or ignore into matching_entries select word_id, 1 from filtered where {dict_key} and orig_word = ?", (word,))
        elif code == '~':
            status = 'missing'
            notes.append('inexact match found')
            conn.execute(f"insert or ignore into matching_entries select word_id, 1 from inexact where {dict_key} and orig_word = ?", (word,))
            check_larger('exact match in {}') or check_esdb('exact match in ESDB')
        elif code == 'v':
            status = 'missing'
            matching_entries = set()
            if not matching_entries:
                for word_ids, variant_level, nv_word in nv_word_variant(conn, dict_key, word):
                    notes.append(f'{VARIANTS[variant_level]} of “{nv_word}”')
                    matching_entries |= set(map(int, word_ids.split(',')))
            if not matching_entries:
                lookup_order = DICTS[dict_key].lookup_order
                for word_ids, variant_level, spellings, nv_word in nv_word_other(conn, dict_key, word):
                    spellings = spellings.split(',')
                    spelling = next((sp for sp in lookup_order if sp in spellings), None)
                    what = 'spelling' if variant_level < 4 else 'variant'
                    notes.append(f"{SPELLINGS.get(spelling,'alternative')} {what} of “{nv_word}”")
                    matching_entries |= set(map(int, word_ids.split(',')))
            if not matching_entries:  # fallback
                for words_ids, nv_word in nv_word_all(conn, dict_key, word):
                    notes.append(f'variant of {nv_word}')
                    matching_entries |= set(map(int, words_ids.split(',')))
            conn.executemany("insert into matching_entries values (?, 1)", ((id,) for id in matching_entries))
            check_larger('in {}')
        elif code == 'o':
            status = 'missing'
            for is_lemma, is_plural in missing_form(conn, dict_key, word):
                if is_lemma:
                    notes.append('inflected forms found, lemma missing')
                elif is_plural:
                    notes.append('noun found, plural missing')
                else:
                    notes.append('lemma found, inflected form missing')
            conn.execute(f"insert or ignore into matching_entries select word_id, 1 from other_form_in_dict_info where {dict_key} and word = ?", (word,))
            check_larger('in {}')
        elif code == '+':
            status = 'missing'
            check_larger('in {}') or check_esdb('in ESDB')

        word = TableCell(word, 'word-default')

        if status == 'filtered':
            footnotes.add('*')
            status = Markup('filtered<sup>*</sup>')
        if status == 'missing':
            status = TableCell(status, 'status-missing')

        entries_class = None if code == '-' else 'entries-filtered' if code == '!' else 'entries-other'
        entry_rows = list(get_entry_info(conn))
        poses_used.update(r[1] for r in entry_rows if r[1])
        entry_lines = [format_lemma_info(*r) for r in entry_rows]
        if entries_class == 'entries-other':
            entry_lines = [f"({line})" for line in entry_lines]
        entries_cell = TableCell('<br>'.join(escape(line) for line in entry_lines), entries_class)
        rows.append([word, status,
                     TableCell(Markup(';<br>').join(escape(line) for line in notes)),
                     entries_cell])
    return rows, poses_used, footnotes

def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <database> <dict_name>", file=sys.stderr)
        sys.exit(1)

    dict_name = sys.argv[2].replace('-', '_')
    if dict_name not in DICTS:
        print(f"Unknown dict '{dict_name}'. Valid: {', '.join(sorted(DICTS))}", file=sys.stderr)
        sys.exit(1)

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
    conn.close()
    del conn

    app = Flask(__name__)

    with open('style.css') as f:
        INLINE_STYLE = f'''<style>
{f.read()}
table             {{ line-height: 100%; }}
.mod-what         {{ font-weight: bold; }}
.mod-in           {{ font-weight: bold; }}
.word-default     {{ font-weight: bold; }}
.status-missing   {{ font-weight: bold; }}
.entries-filtered {{ text-decoration: line-through; }}
.entries-other    {{ color: gray; }}
</style>'''

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
    return f'''<!DOCTYPE html>
<html>
<head>
<title>ESDB Speller Dict Lookup</title>
{INLINE_STYLE}
</head>
<body>
<p>
Use this tool to lookup if a list of words is in an <a href="https://wordlist.aspell.net/dicts/">official ESDB created speller dictionary</a>.  
Enter one word per line, entries are case sensitive.
<form method="post">
<textarea name="words" rows=40 cols=30>
</textarea>
<br>
{make_option_list('dict', 'en_US', DICTS.keys(), {k: v.name for k, v in DICTS.items()})}
<button type="submit">Submit</button>
</form>
<p style="color: #808080;">
{GIT_VER}
</body>'''


def render_error(bad_lines):
    items = ''.join(f'<li>{escape(w)}</li>' for w in bad_lines)
    body = f'<p>Invalid word(s):</p><ul>{items}</ul>' if bad_lines else '<p>No valid words provided.</p>'
    return f'''<!DOCTYPE html>
<html>
<head>
<title>ESDB Speller Dict Lookup</title>
{INLINE_STYLE}
</head>
<body>
{body}
</body>'''

def render_cell(value):
    if isinstance(value, TableCell):
        attr = f' class="{value.css_class}"' if value.css_class else ''
        return Markup(f'<td{attr}>{value.body}</td>')
    return Markup(f'<td>{escape(value)}</td>')


def render_result(dict_display, rows, skipped, poses_used, footnotes):
    warn_html = ''
    if skipped:
        items = ''.join(f'<li>{escape(w)}</li>' for w in skipped)
        warn_html = f'<p>Skipped invalid word(s):</p><ul>{items}</ul>'
    tbody = ''.join(
        Markup(f'<tr>{render_cell(word)}{render_cell(status)}'
               f'{render_cell(notes)}{render_cell(entries)}</tr>\n')
        for word, status, notes, entries in rows
    )
    pos_codes_html = ''
    poses_used.discard('')
    if poses_used:
        rows_html = ''.join(
            Markup(f'<tr><td>&lt;{escape(code)}&gt;</td><td>{escape(BASE_POSES.get(code, ""))}</td></tr>')
            for code in sorted(poses_used)
        )
        pos_codes_html = f'<p><table class="pos-legend">{rows_html}</table></p>'
    footnotes_html = ''
    if '*' in footnotes:
        footnotes_html += f'''*
a word that is marked as belonging to {escape(dict_display)}, but filtered out for one reason or another.
'''
    if footnotes_html:
        footnotes_html = f'<p>{footnotes_html}</p>'
    table = f'''<table border=1 cellpadding=3>
<thead><tr><th>Word</th><th>Status</th><th>Notes</th><th>Entries Found</th></tr></thead>
<tbody>{tbody}</tbody>
</table>'''
    return f'''<!DOCTYPE html>
<html>
<head>
<title>ESDB Speller Dict Lookup</title>
{INLINE_STYLE}
</head>
<body>
<p>
<a href="/speller-lookup">ESDB Speller Dict Lookup</a> results for <b>{escape(dict_display)}</b>:
</p>
{warn_html}
{table}
{pos_codes_html}
{footnotes_html}
<p>
See the <a href="https://github.com/en-wl/wordlist/blob/v2/README.md#file-format">ESDB README</a>
for help with interpreting the ESDB entries and the meaning of the variant levels.
<p style="color: #808080;">
{GIT_VER}
</body>'''


def split_words(words_raw):
    words = []
    for line in io.StringIO(words_raw):
        for word in line.split(','):
          word = word.strip()
          if not word:
              continue
          if len(words) >= 1000:
              abort(400, 'Too many words: the limit is 1000')
          words.append(word)
    return words

def parse_words(words_raw):
    words = []
    skipped = []
    for word in split_words(words_raw):
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
    conn = sqlite3.connect(DB_PATH, uri=True)
    init(conn)

    for word in words:
        key = clusterKey(word).decode('iso-8859-1')
        conn.execute("insert or ignore into input values (?, ?)", (word, key))

    rows, poses_used, footnotes = build_rows(conn, dict_key)

    return Response(render_result(DICTS[dict_key].name, rows, skipped, poses_used, footnotes),
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
        return redirect('/speller-lookup?' 
                        + urlencode([('words', ','.join(words + skipped)), ('dict', dict_key)],safe=','))

    return process_lookup(words, dict_key, skipped)
