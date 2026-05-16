-- create temp table input (
--   word text primary key,
--   word_key text not null
-- ) without rowid;

-- insert into input
-- select word, word_key from fuzzy where word in ('color', 'velcro', 'doth', 'cross-reference', 'cafe', 'café', 'militaries', 'checkboxes');

create temp table in_esdb as
select i.word as orig_word, w.word = i.word as exact, w.*
  from words as w
  join fuzzy on w.word = fuzzy.word
  join input as i using (word_key);

create index in_esdb_idx_orig_word_exact on in_esdb(orig_word) where exact;

create temp table in_dict as
select orig_word, in_esdb.word as word, exact, d.*
  from input as i
  join in_esdb on i.word = orig_word
  join speller_dicts as d using (word_id)
union
select i.word as orig_word, adj_word as word, i.word = adj_word as exact, d.*
  from input as i
  join fuzzy using (word_key)
  join speller_dicts as d on fuzzy.word = d.adj_word;

create temp view exact as
select * from in_dict where exact and orig_word = adj_word;

create temp view filtered as
select * from in_dict where exact and orig_word != adj_word;

create temp view inexact as
select * from in_dict where not exact;

create temp view variant_in_dict as
select a.orig_word, a.word_id, b.word,
       en_US, en_US_large, en_GB_ise, en_GB_ize, en_GB_large, en_CA, en_CA_large, en_AU, en_AU_large
  from in_esdb a
  join words b using (group_id, pos)
  join speller_dicts as d on b.word_id = d.word_id
 where exact and a.word_id != b.word_id;

create temp view variant_in_dict_info as
select a.word, a.word_id, a.variant_level, a.spelling,
       b.spelling as nv_spelling, b.word as nv_word,
       en_US, en_US_large, en_GB_ise, en_GB_ize, en_GB_large, en_CA, en_CA_large, en_AU, en_AU_large
  from words_w_variant_info a
  join words_w_variant_info b using (group_id, pos)
  join speller_dicts as d on b.word_id = d.word_id
 where a.word_id != b.word_id and b.variant_level = 0;

create temp view other_form_in_dict as
select a.orig_word, a.word_id, b.word,
       en_US, en_US_large, en_GB_ise, en_GB_ize, en_GB_large, en_CA, en_CA_large, en_AU, en_AU_large
  from in_esdb a
  join words b using (lemma_id)
  join speller_dicts as d on b.word_id = d.word_id
 where exact and a.word_id != b.word_id;

create temp view other_form_in_dict_info as
select a.*,
       en_US, en_US_large, en_GB_ise, en_GB_ize, en_GB_large, en_CA, en_CA_large, en_AU, en_AU_large
  from entries a
  join words b using (lemma_id)
  join speller_dicts as d on b.word_id = d.word_id
 where a.word_id != b.word_id;




