from flask import Flask, request, Response, abort
from markupsafe import Markup, escape
import libscowl

app = Flask(__name__)
# test with: flask --app create run -p 5000
# http://127.0.0.1:5000/create

DB_PATH = 'scowl.db'

SPELLING_MAP = {'US': 'A', 'GBs': 'B', 'GBz': 'Z', 'CA': 'C', 'AU': 'D'}

VARIANT_MAP = {0: 1, 1: 4, 2: 6, 3: 8}

PRESETS = {
    'en_US':       {'max_size': 60, 'spelling': ['US'],        'max_variant': 0, 'diacritic': 'strip'},
    'en_GB-ise':   {'max_size': 60, 'spelling': ['GBs'],       'max_variant': 0, 'diacritic': 'strip'},
    'en_GB-ize':   {'max_size': 60, 'spelling': ['GBz'],       'max_variant': 0, 'diacritic': 'strip'},
    'en_CA':       {'max_size': 60, 'spelling': ['CA'],        'max_variant': 0, 'diacritic': 'strip'},
    'en_AU':       {'max_size': 60, 'spelling': ['AU'],        'max_variant': 0, 'diacritic': 'strip'},
    'en_US-large': {'max_size': 70, 'spelling': ['US'],        'max_variant': 1, 'diacritic': 'strip'},
    'en_GB-large': {'max_size': 70, 'spelling': ['GBs','GBz'], 'max_variant': 1, 'diacritic': 'strip'},
    'en_CA-large': {'max_size': 70, 'spelling': ['CA'],        'max_variant': 1, 'diacritic': 'strip'},
    'en_AU-large': {'max_size': 70, 'spelling': ['AU'],        'max_variant': 1, 'diacritic': 'strip'},
}

SIZES = {
    10: '10',
    20: '20',
    35: '35 (small)',
    40: '40',
    50: '50 (medium)',
    55: '55',
    60: '60 (default)',
    70: '70 (large)',
    80: '80 (huge)',
    95: '95 (insane)',
}

SPELLINGS = {
    'US':  'American',
    'GBs': 'British (-ise spelling)',
    'GBz': 'British (-ize/OED spelling)',
    'CA':  'Canadian',
    'AU':  'Australian',
}
SPELLING_ORDER = ['US', 'GBs', 'GBz', 'CA', 'AU']

VARIANTS = {
    0: '0 (none)',
    1: '1 (common)',
    2: '2 (acceptable)',
    3: '3 (seldom-used)',
}

DIACRITICS = {
    'strip': 'Strip (caf\u00e9 becomes cafe)',
    'keep':  'Keep',
    'both':  'Include Both (cafe &amp; caf\u00e9)',
}
DIACRITIC_ORDER = ['strip', 'keep', 'both']

SPECIALS = {
    'hacker':         'Hacker (for example grepped)',
    'roman-numerals': 'Roman Numerals',
}


def make_option_list(name, default, keys, values):
    parts = [f'<select name="{escape(name)}">']
    for k in keys:
        selected = ' selected' if str(k) == str(default) else ''
        parts.append(f'  <option value="{escape(k)}"{selected}>{escape(values[k])}</option>')
    parts.append('</select>')
    return Markup('\n'.join(parts))


def make_check_list(name, defaults, keys, values):
    parts = []
    for k in keys:
        checked = ' checked' if k in defaults else ''
        parts.append(
            f' <label for="{escape(name)}-{escape(k)}">'
            f'<input type="checkbox" id="{escape(name)}-{escape(k)}" '
            f'name="{escape(name)}" value="{escape(k)}"{checked}>'
            f'{escape(values[k])}</label>'
        )
    return Markup(''.join(parts))


def render_form(defaults):
    preset = PRESETS[defaults]
    dicts_html = ' \n'.join(
        f'<a href="?defaults={escape(d)}">{escape(d)}</a>' for d in PRESETS
    )

    sizes_html = make_option_list('max_size', preset['max_size'], sorted(SIZES), SIZES)
    spellings_html = make_check_list('spelling', preset['spelling'], SPELLING_ORDER, SPELLINGS)
    variant_html = make_option_list('max_variant', preset['max_variant'], sorted(VARIANTS), VARIANTS)
    accents_html = make_option_list('diacritic', preset['diacritic'], DIACRITIC_ORDER, DIACRITICS)
    special_defaults = preset.get('special', list(SPECIALS.keys()))
    special_html = make_check_list('special', special_defaults, list(SPECIALS.keys()), SPECIALS)

    return f'''<html>
<head>
<title>SCOWL Custom List/Dictionary Creator</title>
</head>
<body>
<p>
Use this tool to create and download custimized Word Lists or Hunspell
dictionaries from <a href="http://wordlist.aspell.net/">SCOWL</a>.
</p>
<p>
Using defaults for <b>{escape(defaults)}</b> dictionary.
<p>
Reload with defaults from: {dicts_html} dictionary.
</p>
<form>
SCOWL Size: {sizes_html}
<p>
Spelling(s): {spellings_html}
<p>
Include Spelling Variants up to Level: {variant_html}
<p>
Diacritic Handling (for example caf\u00e9): {accents_html}
<p>
Special Lists to Include: {special_html}
<p style="line-height: 2">
<button type="submit" name="download" value="wordlist">Download as Word List</button> Encoding: <select name="encoding">
  <option value="utf-8">UTF-8
  <option value="iso-8859-1">ISO-8859-1
</select>
Format: <select name="format">
  <option value="inline">Inline
  <option value="tar.gz">tar.gz (Unix EOL)
  <option value="zip">zip (Windows EOL)
</select>
<br>
<button type="submit" name="download" value="hunspell">Download as Hunspell Dictionary</button>
<button type="submit" name="download" value="aspell">Download as Aspell Dictionary</button>
<p>
<button type="reset">Reset to Defaults</button>
<p>
<i>
For additional help on the meaning of any of these options please see the <a href="http://wordlist.aspell.net/scowl-readme/">SCOWL Readme</a>.
</i>
</form>
</body>'''


@app.route('/create')
def create():
    download = request.args.get('download')

    if not download:
        defaults = request.args.get('defaults', 'en_US')
        if defaults not in PRESETS:
            abort(400, 'Invalid defaults preset')
        return render_form(defaults)

    if download in ('hunspell', 'aspell'):
        abort(501)

    if download != 'wordlist':
        abort(400, 'Invalid download type')

    # Parse and validate params
    try:
        max_size = int(request.args.get('max_size', 60))
    except ValueError:
        abort(400, 'max_size must be an integer')
    if max_size < 0 or max_size > 99:
        abort(400, 'max_size must be 0-99')

    spellings_raw = request.args.getlist('spelling') or ['US']
    for s in spellings_raw:
        if s not in SPELLING_MAP:
            abort(400, f'Invalid spelling: {s}')

    try:
        max_variant = int(request.args.get('max_variant', 0))
    except ValueError:
        abort(400, 'max_variant must be an integer')
    if max_variant not in VARIANT_MAP:
        abort(400, 'max_variant must be 0-3')

    diacritic = request.args.get('diacritic', 'strip')
    if diacritic not in ('strip', 'keep', 'both'):
        abort(400, 'Invalid diacritic option')

    specials = request.args.getlist('special')
    for s in specials:
        if s not in SPECIALS:
            abort(400, f'Invalid special: {s}')

    encoding = request.args.get('encoding', 'utf-8')
    if encoding not in ('utf-8', 'iso-8859-1'):
        abort(400, 'Invalid encoding')

    fmt = request.args.get('format', 'inline')
    if fmt not in ('inline', 'tar.gz', 'zip'):
        abort(400, 'Invalid format')
    if fmt != 'inline':
        abort(501)

    # Map to libscowl args
    lc_spellings = [SPELLING_MAP[s] for s in spellings_raw]
    variant_level = VARIANT_MAP[max_variant]
    categories = libscowl.Include(*specials)

    # Generate wordlist
    conn = libscowl.openDB(DB_PATH)
    words = set(libscowl.getWords(conn, size=max_size, spellings=lc_spellings,
                                  variantLevel=variant_level, categories=categories,
                                  deaccent=False))

    # Diacritic processing
    if diacritic == 'strip':
        words = {libscowl.deaccent(w) for w in words}
    elif diacritic == 'both':
        words |= {libscowl.deaccent(w) for w in words}

    # Build response
    text = '\n'.join(sorted(words)) + '\n'
    charset = 'UTF-8' if encoding == 'utf-8' else 'ISO-8859-1'
    encoded = text.encode(charset)
    resp = Response(encoded, content_type=f'text/plain; charset={charset}')
    return resp
