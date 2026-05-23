#!/usr/bin/env python3

import unittest
from pathlib import Path
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from create import app
from dict_params import DICT_PARMS

DIFF_DIR = Path(__file__).parent / 'diff'


def extract_form_args(html):
    soup = BeautifulSoup(html, 'html.parser')
    form = soup.find('form')
    args = []
    for sel in form.find_all('select'):
        name = sel.get('name')
        if not name:
            continue
        opt = sel.find('option', selected=True) or sel.find('option')
        if opt is not None:
            args.append((name, opt.get('value', opt.text)))
    for cb in form.find_all('input', {'type': 'checkbox'}):
        if cb.has_attr('checked') and cb.get('name'):
            args.append((cb['name'], cb.get('value', 'on')))
    return args


class PresetWordlistTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_form_submission_matches_diff_files(self):
        for name in DICT_PARMS:
            with self.subTest(preset=name):
                form_resp = self.client.get(f'/create?defaults={name}')
                self.assertEqual(form_resp.status_code, 200)

                args = extract_form_args(form_resp.get_data(as_text=True))
                args.append(('download', 'wordlist'))

                resp = self.client.get('/create?' + urlencode(args))
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.content_type.startswith('text/plain'))

                _, sep, words = resp.get_data(as_text=True).partition('\n---\n')
                self.assertTrue(sep, 'response missing --- separator')

                expected = (DIFF_DIR / f'{name}.txt').read_text()
                self.assertEqual(words, expected)


if __name__ == '__main__':
    unittest.main(buffer=True)
