from flask import Flask, request, Response, abort
from markupsafe import Markup, escape
from datetime import datetime, timezone
from pathlib import Path
import io
import json
import os
import re
import sys
import subprocess
import tarfile
import tempfile
import zipfile
import libscowl

# test with: flask --app create run -p 5000
# http://127.0.0.1:5000/create

app = Flask(__name__)

with open('style.css') as f:
    INLINE_STYLE = f'<style>\n{f.read()}</style>'

DB_PATH = 'scowl.db'

SPELLING_MAP = {'US': 'A', 'GBs': 'B', 'GBz': 'Z', 'CA': 'C', 'AU': 'D'}

LEGACY_VARIANT_MAP = {0: 1, 1: 4, 2: 6, 3: 8}

PRESETS = {
    'en_US':       {'max_size': 60, 'spelling': ['US'],        'variant_level': 1, 'diacritic': 'strip'},
    'en_GB-ise':   {'max_size': 60, 'spelling': ['GBs'],       'variant_level': 1, 'diacritic': 'strip'},
    'en_GB-ize':   {'max_size': 60, 'spelling': ['GBz'],       'variant_level': 1, 'diacritic': 'strip'},
    'en_CA':       {'max_size': 60, 'spelling': ['CA'],        'variant_level': 1, 'diacritic': 'strip'},
    'en_AU':       {'max_size': 60, 'spelling': ['AU'],        'variant_level': 1, 'diacritic': 'strip'},
    'en_US-large': {'max_size': 70, 'spelling': ['US'],        'variant_level': 4, 'diacritic': 'strip'},
    'en_GB-large': {'max_size': 70, 'spelling': ['GBs','GBz'], 'variant_level': 4, 'diacritic': 'strip'},
    'en_CA-large': {'max_size': 70, 'spelling': ['CA'],        'variant_level': 4, 'diacritic': 'strip'},
    'en_AU-large': {'max_size': 70, 'spelling': ['AU'],        'variant_level': 4, 'diacritic': 'strip'},
}

SIZES = {
    35: '35 (small)',
    50: '50 (medium)',
    60: '60 (default)',
    70: '70 (large)',
    80: '80 (huge)',
    85: '85 (huge+)',
}

SPELLINGS = {
    'US':  'American',
    'GBs': 'British (-ise / traditional)',
    'GBz': 'British (-ize / Oxford)',
    'CA':  'Canadian',
    'AU':  'Australian',
}

SPELLING_ORDER = ['US', 'GBs', 'GBz', 'CA', 'AU']

VARIANT_LEVELS = {
    0: '0 (none)',
    1: '1 *default*',
    2: '2 (equal)',
    3: '3 (disagreement)',
    4: '4 *common*',
    5: '5 (variant)',
    6: '6 *acceptable*',
    7: '7 (uncommon)',
    8: '8 (archaic)',
    9: '9 (invalid)',
}

DIACRITICS = {
    'strip': 'Strip (café becomes cafe)',
    'keep':  'Keep',
    'both':  'Include Both (cafe & café)',
}
DIACRITIC_ORDER = ['strip', 'keep', 'both']

SPECIALS = {
    'hacker':         'Hacker (for example grepped)',
    'roman-numerals': 'Roman Numerals',
}

with open('scowl/Copyright') as _f:
    _copyright_parts = _f.read().rstrip('\n').split('\n===')
    COPYRIGHT_BASE = _copyright_parts[0].strip('\n')
    COPYRIGHT_SECTIONS = {}
    for _part in _copyright_parts[1:]:
        _first_line, _, _body = _part.partition('\n')
        _key = _first_line.strip()
        if _key:
            COPYRIGHT_SECTIONS[_key] = _body.strip('\n')

with open('scowl/README.md') as _f:
    README_SCOWL = _f.read()

GIT_VER = subprocess.run(
    ['git', 'log', '--pretty=format:%cd [%h]', '-n', '1'],
    cwd='scowl', stdout=subprocess.PIPE, text=True, check=True
).stdout.strip()

GIT_HASH = subprocess.run(
    ['git', 'rev-parse', '--short', 'HEAD'],
    cwd='scowl', stdout=subprocess.PIPE, text=True, check=True,
).stdout.strip()

def build_header(parms):
    parms_block = (
        "Custom wordlist generated from https://app.aspell.net/create using\n"
        "the English Speller Database (ESDB) with parameters:\n"
        + dump_parms(parms)
    ).rstrip('\n')
    parts = [parms_block,
             'https://wordlist.aspell.net',
             f"Using Git Commit From: {GIT_VER}",
             COPYRIGHT_BASE]
    if 'AU' in parms['spelling']:
        parts.append(COPYRIGHT_SECTIONS['AU'])
    if parms['max_size'] > 80:
        parts.append(COPYRIGHT_SECTIONS['UKACD'])
    return '\n\n'.join(parts) + '\n\n'

def dict_name(spellings_raw):
    normalized = set()
    for s in spellings_raw:
        if s in ('GBs', 'GBz'):
            normalized.add('GB')
        else:
            normalized.add(s)
    if len(normalized) == 1:
        return f'en_{next(iter(normalized))}-custom'
    return 'en-custom'

def locale_name(spellings_raw):
    if 'US' in spellings_raw:
        return 'en-US'
    elif 'GBs' in spellings_raw:
        return 'en-GB'
    elif 'GBz' in spellings_raw:
        return 'en-GB-oxendict'
    elif 'CA' in spellings_raw:
        return 'en-CA'
    elif 'AU' in spellings_raw:
        return 'en-AU'
    raise ValueError('unknown spelling')

def make_hunspell_dict(tmpdir, name, parms_str, words):
    parms_path = os.path.join(tmpdir, 'parms.txt')
    with open(parms_path, 'w') as f:
        f.write('With Parameters:\n')
        f.write(parms_str)

    env = os.environ.copy()
    env['SCOWL'] = os.path.abspath('scowl')
    env.pop('SCOWL_VERSION', None)

    result = subprocess.run(
        [env['SCOWL'] + '/speller/make-hunspell-dict', '-one', name, 'parms.txt'],
        input='\n'.join(words) + '\n',
        encoding='iso-8859-1',
        cwd=tmpdir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )


def make_hunspell_zip(name, parms_str, words):
    with tempfile.TemporaryDirectory() as tmpdir:
        make_hunspell_dict(tmpdir, name, parms_str, words)

        zip_path = os.path.join(tmpdir, f'hunspell-{name}.zip')
        with open(zip_path, 'rb') as f:
            return f.read()

def extension_version(now):
    year = now.year
    dayinyear = now.timetuple().tm_yday
    time = 10 * (now.hour * 60 + now.minute) + min(now.second // 6, 9)
    return '.'.join(str(x) for x in (year, dayinyear, time))


def make_libreoffice_ext(name, locale, parms, words):
    import make_libreoffice as lo

    now = datetime.now(timezone.utc)
    date = now.strftime('%Y-%m-%d %H:%M:%S UTC')

    config = {
        'pkg':   f"dict-{locale}-ESDB-custom",
        'dicts': {locale: name},
        'id':    f'custom.{locale}',
        'name':  f'Custom {locale} speller dictionary',
        'descr': Path('descr.txt'),
    }
    version = extension_version(now)
    
    speller_dir = os.path.abspath('scowl/speller')

    with tempfile.TemporaryDirectory() as tmpdir:
        make_hunspell_dict(tmpdir, name, dump_parms(parms), words)
        os.symlink(
            os.path.join(speller_dir, 'libreoffice'),
            os.path.join(tmpdir, 'libreoffice'),
        )
        orig_wd = os.getcwd()
        try:
            os.chdir(tmpdir)
            with open('descr.txt', 'w') as f:
                f.write(f"Custom {locale} speller dictionary generated from "
                        f"https://app.aspell.net/create on {date}, "
                        f"using the English Speller Database (ESDB, git rev {GIT_HASH}) with parameters:\n")
                f.write(dump_parms(parms).rstrip('\n'))
            ext_name = lo.mk_dist(config, version)
            with open(ext_name, 'rb') as f:
                return f.read(), ext_name
        finally:
            os.chdir(orig_wd)


def make_firefox_ext(name, locale, parms, words):
    now = datetime.now(timezone.utc)
    date = now.strftime('%Y-%m-%d %H:%M:%S UTC')

    version = extension_version(now)

    manifest = {
        "manifest_version": 2,
        "name": f"Custom {locale} Dictionary (ESDB)",
        "version": version,
        "description": (
            f"Custom {locale} speller dictionary created from the English Speller Database (ESDB, git rev {GIT_HASH}) "
            f"with parameters: {'; '.join(parm_lines(parms))}"),
        "author": "Kevin Atkinson",
        "homepage_url": "https://app.aspell.net/create",
        "browser_specific_settings": {
            "gecko": {"id": f"{locale}-custom@wordlist.aspell.net"}
        },
        "dictionaries": {locale: f"dictionaries/{name}.dic"},
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        make_hunspell_dict(tmpdir, name, dump_parms(parms), words)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('manifest.json', json.dumps(manifest, indent=4))
            zf.write(f'{tmpdir}/{name}.dic', f"dictionaries/{name}.dic")
            zf.write(f'{tmpdir}/{name}.aff', f"dictionaries/{name}.aff")
            zf.write(f'{tmpdir}/README_{name}.txt', f"README_{name}.txt")

        filename = f"dict-{locale}-ESDB-custom-{version}.xpi"
        return buf.getvalue(), filename


def parm_lines(parms):
    # Size: use display text from SIZES dict
    size = parms['max_size']
    lines = [f"Size: {SIZES.get(size, str(size))}"]

    # Spelling: two-letter codes in order US GB CA AU
    spellings = parms['spelling']
    has_gbs = 'GBs' in spellings
    has_gbz = 'GBz' in spellings
    spell_parts = []
    for code in ['US', 'GB', 'CA', 'AU']:
        if code == 'GB':
            if has_gbs and has_gbz:
                spell_parts.append('GB')
            elif has_gbs:
                spell_parts.append('GB(-ise)')
            elif has_gbz:
                spell_parts.append('GB(-ize/oed)')
        elif code in spellings:
            spell_parts.append(code)
    lines.append(f"Spelling: {' '.join(spell_parts) if spell_parts else '<none>'}")

    # Variant Level: use display text from VARIANT_LEVELS dict
    vl = parms['variant_level']
    lines.append(f"Variant Level: {VARIANT_LEVELS.get(vl, str(vl))}")

    # Special: space-joined values
    special = parms['special']
    lines.append(f"Special: {' '.join(special) if special else '<none>'}")

    # Diacritics: raw value
    lines.append(f"Diacritics: {parms['diacritic']}")

    return lines

def dump_parms(parms):
    return ''.join(f"  {line}\n" for line in parm_lines(parms))


def make_aspell_dict(parms_str, words):
    with tempfile.TemporaryDirectory() as tmpdir:
        parms_path = os.path.join(tmpdir, 'parms.txt')
        with open(parms_path, 'w') as f:
            f.write(parms_str)

        env = os.environ.copy()
        env['SCOWL'] = os.path.abspath('scowl')
        env.pop('SCOWL_VERSION', None)

        subprocess.run(
            [env['SCOWL'] + '/speller/make-aspell-custom', GIT_VER, 'parms.txt'],
            input='\n'.join(words) + '\n',
            encoding='iso-8859-1',
            cwd=tmpdir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )

        out_path = os.path.join(tmpdir, 'aspell6-en-custom.tar.bz2')
        with open(out_path, 'rb') as f:
            return f.read()


def tar_add_bytes(tf, name, data):
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tf.addfile(info, io.BytesIO(data))


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
    variant_html = make_option_list('variant_level', preset['variant_level'], sorted(VARIANT_LEVELS), VARIANT_LEVELS)
    accents_html = make_option_list('diacritic', preset['diacritic'], DIACRITIC_ORDER, DIACRITICS)
    special_defaults = preset.get('special', list(SPECIALS.keys()))
    special_html = make_check_list('special', special_defaults, list(SPECIALS.keys()), SPECIALS)

    return f'''<!DOCTYPE html>
<html>
<head>
<title>ESDB Custom List/Dictionary Creator</title>
{INLINE_STYLE}
</head>
<body>
<p>
Use this tool to create and download customized Word Lists or speller
dictionaries from the
<a href="https://wordlist.aspell.net/">English Speller Database (ESDB)</a>.
</p>
<p>
Using defaults for <b>{escape(defaults)}</b> dictionary.
<p>
Reload with defaults from: {dicts_html} dictionary.
(<a href="https://wordlist.aspell.net/hunspell-readme/" target="_blank">more info</a>)
</p>
<form>
ESDB Size: {sizes_html}
(<a href="https://github.com/en-wl/wordlist/blob/v2/README.md#size" target="_blank">more info</a>)
<p>
Spelling(s): {spellings_html}
<p>
Include Spelling Variants up to Level: {variant_html}
(<a href="https://github.com/en-wl/wordlist/blob/v2/README.md#variant-level" target="_blank">more info</a>)
<p>
Diacritic Handling (for example café): {accents_html}
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
<p>
<button type="submit" name="download" value="hunspell">Download as Hunspell Dictionary</button>
<button type="submit" name="download" value="aspell">Download as Aspell Dictionary</button>
<p>
<button type="submit" name="download" value="libreoffice">Download as LibreOffice extension</button>
<button type="submit" name="download" value="firefox">Download as Firefox/Thunderbird extension</button>
<br>
The extension will install the dictionary under the locale of the first
spelling selected.  To use, make sure no other extensions are installing a
dictionary under the same locale.  To verify that the correct spell-checker
dictionary is being used see <a
href="https://wordlist.aspell.net/test-doc-2026.02.25">test-doc-2026.02.25</a>.
The extension does not contain any executable code, so it should be safe to
install even though it is unsigned.
<p>
<button type="reset">Reset to Defaults</button>
<p style="color: #808080;">
{GIT_VER}
<p>
</form>
</body>'''

# Custom Locale String: <input type="text" size=20 name="locale"></input><br>

@app.route('/create')
def create():
    download = request.args.get('download')

    if not download:
        defaults = request.args.get('defaults', 'en_US')
        if defaults not in PRESETS:
            abort(400, 'Invalid defaults preset')
        return Response(render_form(defaults), content_type='text/html; charset=UTF-8')

    if download not in ('wordlist', 'hunspell', 'aspell', 'libreoffice', 'firefox'):
        abort(400, 'Invalid download type')

    # Parse and validate shared parms
    parms = {}

    try:
        parms['max_size'] = int(request.args.get('max_size', 60))
    except ValueError:
        abort(400, 'max_size must be an integer')
    if parms['max_size'] < 0 or parms['max_size'] > 99:
        abort(400, 'max_size must be 0-99')

    parms['spelling'] = request.args.getlist('spelling') or ['US']
    for s in parms['spelling']:
        if s not in SPELLING_MAP:
            abort(400, f'Invalid spelling: {s}')

    # Handle both legacy max_variant and new variant_level parameters
    if 'variant_level' in request.args:
        try:
            parms['variant_level'] = int(request.args.get('variant_level'))
        except ValueError:
            abort(400, 'variant_level must be an integer')
        if parms['variant_level'] < 0 or parms['variant_level'] > 9:
            abort(400, 'variant_level must be 0-9')
    elif 'max_variant' in request.args:
        # Backwards compatibility: map old max_variant (0-3) to new variant_level
        try:
            legacy_variant = int(request.args.get('max_variant'))
        except ValueError:
            abort(400, 'max_variant must be an integer')
        if legacy_variant not in LEGACY_VARIANT_MAP:
            abort(400, 'max_variant must be 0-3')
        parms['variant_level'] = LEGACY_VARIANT_MAP[legacy_variant]
    else:
        # Default to level 1 (default/include)
        parms['variant_level'] = 1

    parms['diacritic'] = request.args.get('diacritic', 'strip')
    if parms['diacritic'] not in ('strip', 'keep', 'both'):
        abort(400, 'Invalid diacritic option')

    parms['special'] = request.args.getlist('special')
    for s in parms['special']:
        if s not in SPECIALS:
            abort(400, f'Invalid special: {s}')

    # Map to libscowl args
    lc_spellings = [SPELLING_MAP[s] for s in parms['spelling']]
    categories = libscowl.Include(*parms['special'])

    # Generate wordlist
    conn = libscowl.openDB(DB_PATH)
    words = set(libscowl.getWords(conn, size=parms['max_size'], spellings=lc_spellings,
                                  variantLevel=parms['variant_level'], categories=categories,
                                  deaccent=False))

    # Diacritic processing
    if parms['diacritic'] == 'strip':
        words = {libscowl.deaccent(w) for w in words}
    elif parms['diacritic'] == 'both':
        words |= {libscowl.deaccent(w) for w in words}

    # locale = request.args.get('locale', '').strip()
    # if locale:
    #     if not re.fullmatch(r'[a-zA-Z0-9-]+', locale):
    #         abort(400, f'Invalid custom locale: {locale}')
    # else:
    locale = locale_name(parms['spelling'])

    sorted_words = sorted(words)

    if download == 'hunspell':
        name = dict_name(parms['spelling'])
        parms_str = dump_parms(parms)
        try:
            zip_bytes = make_hunspell_zip(name, parms_str, sorted_words)
        except subprocess.CalledProcessError as e:
            sys.stderr.write(e.stderr)
            raise
        filename = f'hunspell-{name}.zip'
        return Response(zip_bytes,
                        content_type='application/zip',
                        headers={'Content-Disposition': f'attachment; filename={filename}'})

    if download == 'libreoffice':
        name = dict_name(parms['spelling'])
        try:
            (ext_bytes, filename) = make_libreoffice_ext(name, locale, parms, sorted_words)
        except subprocess.CalledProcessError as e:
            sys.stderr.write(e.stderr)
            raise
        return Response(ext_bytes,
                        content_type='application/octet-stream',
                        headers={'Content-Disposition': f'attachment; filename={filename}'})

    if download == 'firefox':
        name = dict_name(parms['spelling'])
        try:
            (ext_bytes, filename) = make_firefox_ext(name, locale, parms, sorted_words)
        except subprocess.CalledProcessError as e:
            sys.stderr.write(e.stderr)
            raise
        return Response(ext_bytes,
                        content_type='application/octet-stream',
                        headers={'Content-Disposition': f'attachment; filename={filename}'})

    if download == 'aspell':
        parms_str = dump_parms(parms)
        try:
            tar_bytes = make_aspell_dict(parms_str, sorted_words)
        except subprocess.CalledProcessError as e:
            sys.stderr.write(e.stderr)
            raise
        return Response(tar_bytes,
                        content_type='application/octet-stream',
                        headers={'Content-Disposition': 'attachment; filename=aspell6-en-custom.tar.bz2'})

    # wordlist-specific parms
    encoding = request.args.get('encoding', 'utf-8')
    if encoding not in ('utf-8', 'iso-8859-1'):
        abort(400, 'Invalid encoding')

    fmt = request.args.get('format', 'inline')
    if fmt not in ('inline', 'tar.gz', 'zip'):
        abort(400, 'Invalid format')

    # Build response
    charset = 'UTF-8' if encoding == 'utf-8' else 'ISO-8859-1'
    header = build_header(parms)

    if fmt == 'inline':
        text = header + '---\n' + '\n'.join(sorted_words) + '\n'
        encoded = text.encode(charset)
        return Response(encoded, content_type=f'text/plain; charset={charset}')

    readme_bytes = header.encode(charset)
    scowl_readme_bytes = README_SCOWL.encode('utf-8')

    buf = io.BytesIO()
    if fmt == 'tar.gz':
        words_bytes = ('\n'.join(sorted_words) + '\n').encode(charset)
        with tarfile.open(fileobj=buf, mode='w:gz') as tf:
            tar_add_bytes(tf, 'SCOWL-wl/README', readme_bytes)
            tar_add_bytes(tf, 'SCOWL-wl/words.txt', words_bytes)
            tar_add_bytes(tf, 'SCOWL-wl/README_SCOWL.md', scowl_readme_bytes)
        return Response(buf.getvalue(),
                        content_type='application/octet-stream',
                        headers={'Content-Disposition': 'attachment; filename=SCOWL-wl.tar.gz'})
    else:  # zip
        words_bytes = ('\r\n'.join(sorted_words) + '\r\n').encode(charset)
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('README', readme_bytes.replace(b'\n', b'\r\n'))
            zf.writestr('words.txt', words_bytes)
            zf.writestr('README_SCOWL.md', scowl_readme_bytes.replace(b'\n', b'\r\n'))
        return Response(buf.getvalue(),
                        content_type='application/zip',
                        headers={'Content-Disposition': 'attachment; filename=SCOWL-wl.zip'})

