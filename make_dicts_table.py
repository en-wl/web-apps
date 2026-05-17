#!/usr/bin/env python3

import re
from inspect import signature
import libscowl as esdb

conn = esdb.openDB('scowl.db')

DICT_PARMS = {
    'en_US': {'size': 60, 'spellings': 'A', 'variantLevel': 1},
    'en_US_large': {'size': 70, 'spellings': 'A', 'variantLevel': 4},
    'en_GB_ise': {'size': 60, 'spellings': 'B', 'variantLevel': 1},
    'en_GB_ize': {'size': 60, 'spellings': 'Z', 'variantLevel': 1},
    'en_GB_large': {'size': 70, 'spellings': 'BZ', 'variantLevel': 4},
    'en_CA': {'size': 60, 'spellings': 'C', 'variantLevel': 1},
    'en_CA_large': {'size': 70, 'spellings': 'C', 'variantLevel': 4},
    'en_AU': {'size': 60, 'spellings': 'D', 'variantLevel': 1},
    'en_AU_large': {'size': 70, 'spellings': 'D', 'variantLevel': 4},
}

def create_dict_table(name, size, spellings, variantLevel, deaccent = True, **kwargs):

    queryArgs = {**{p.name: kwargs.pop(p.name, p.default) for p in signature(esdb.queryString).parameters.values()},
                 'size': size, 'spellings': spellings, 'variantLevel': variantLevel}
    query = f"select word_id, word from scowl_ {esdb.queryString(**queryArgs).where}";

    filterArgs = {p.name: kwargs.pop(p.name, p.default) for p in signature(esdb.wordFilterRegEx).parameters.values()}
    wordFilter = re.compile(esdb.wordFilterRegEx(**filterArgs))
    
    if deaccent:
        deaccent = esdb.deaccent
    else:
        deaccent = lambda w: w
    
    for word_id, orig in conn.execute(query):
        m = wordFilter.fullmatch(orig)
        if m:
            w = m[1]
            w = deaccent(w)
        else:
            w = ''
        conn.execute(f"insert into speller_dicts (word_id, adj_word, {name}) values (?, ?, 1) "
                     f"  on conflict (word_id, adj_word) do update set {name} = 1", (word_id, w))

conn.execute("begin")

conn.execute("drop table if exists speller_dicts")

conn.execute(f"""create table speller_dicts (
    word_id integer not null,
    adj_word text not null,
    {''.join(f'{name} en_US integer not null default 0, ' for name in DICT_PARMS.keys())}
    primary key(word_id, adj_word)
) without rowid""")

for dict, parms in DICT_PARMS.items():
    create_dict_table(dict, **parms)

conn.execute("create index speller_dicts_idx on speller_dicts(adj_word)")

conn.execute("commit")
