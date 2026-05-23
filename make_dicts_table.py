#!/usr/bin/env python3

import sys
import re
from inspect import signature
import libscowl as esdb

from dict_params import *

conn = esdb.openDB('scowl.db')

def fieldName(name):
    return name.replace('-','_')

def create_dict_table(name, size, spellings, variantLevel, diacritics, **kwargs):

    queryArgs = {**{p.name: kwargs.pop(p.name, p.default) for p in signature(esdb.queryString).parameters.values()},
                 'size': size, 'spellings': spellings, 'variantLevel': variantLevel}
    query = f"select word_id, word from scowl_ {esdb.queryString(**queryArgs).where}";

    filterArgs = {p.name: kwargs.pop(p.name, p.default) for p in signature(esdb.wordFilterRegEx).parameters.values()}
    wordFilter = re.compile(esdb.wordFilterRegEx(**filterArgs))

    def words():
        for word_id, orig in conn.execute(query):
            m = wordFilter.fullmatch(orig)
            if not m:
                yield word_id, ''
                continue
            word = m[1]
            if diacritics == 'strip':
                yield word_id, esdb.deaccent(word)
                continue
            yield word_id, word
            if diacritics != 'both':
                continue
            deaccented = esdb.deaccent(word)
            if word != deaccented:
                yield word_id, deaccented

    for word_id, adj_word in words():
        conn.execute(f"insert into speller_dicts (word_id, adj_word, {name}) values (?, ?, 1) "
                     f"  on conflict (word_id, adj_word) do update set {name} = 1", (word_id, adj_word))

conn.execute("begin")

conn.execute("drop table if exists speller_dicts")

conn.execute(f"""create table speller_dicts (
    word_id integer not null,
    adj_word text not null,
    {''.join(f"{fieldName(name)} integer not null default 0, " for name in DICT_PARMS.keys())}
    primary key(word_id, adj_word)
) without rowid""")

for name, parms in DICT_PARMS.items():
    create_dict_table(fieldName(name), **parms)

conn.execute("create index speller_dicts_idx on speller_dicts(adj_word)")

differ = set()
for name in DICT_PARMS.keys():
    in_database = set(w for w, 
                      in conn.execute(f"select distinct adj_word from speller_dicts where {fieldName(name)} and adj_word != ''"))

    with open(f"diff/{name}.txt") as f:
        from_file = set(line.strip() for line in f)

    only_in_database = in_database - from_file
    if only_in_database:
        sys.stderr.write("{name}: only in database: {','.join(only_in_database)}\n")
        differ.add(name)

    only_in_file = from_file - in_database
    if only_in_file:
        sys.stderr.write(f"{name}: only in file: {','.join(only_in_file)}\n")
        differ.add(name)

if differ:
    sys.stderr.write(f"database and file disagree for: {', '.join(differ)}\n")
    sys.stderr.write(f"aborting transaction\n")
    conn.execute("rollback")
    sys.exit(1)

conn.execute("commit")
