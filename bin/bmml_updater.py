#!/usr/bin/env python3
"""
bmml_updater.py — populate bmml class descriptions, feature comments,
invariants, and pre/post conditions from adoc specs.

For each .bmml file in the bmm tree, finds the matching openEHR specification
.adoc class file, then:
  * replaces the class-level block comment with the full adoc description
  * replaces feature block comments with the full adoc attribute/function docs
  * adds/replaces an invariant block from adoc invariants
  * adds pre_cond / post_cond sub-blocks to function declarations

Usage
-----
  # Update one file explicitly
  bmml_updater.py <path/to/CLASS.bmml> <path/to/org.openehr.*.class.adoc>

  # Auto-discover and update all files
  bmml_updater.py --all [--bmm-dir <dir>] [--specs-dir <dir>] [--dry-run]

Defaults (relative to the script's parent directory):
  --bmm-dir   ../bmm
  --specs-dir  ../../specifications
"""

import re
import os
import sys
import textwrap
from collections import OrderedDict

CONTENT_WIDTH = 90  # chars of text content per comment line (after "    | ")

# ─── adoc parsing ─────────────────────────────────────────────────────────────

def parse_adoc(content):
    """
    Parse an openEHR adoc class table.

    Returns
    -------
    class_description : str
        Full class description text (from h|*Description* section).
    features : OrderedDict
        {lowercase_name: raw_doc_text}
    conditions : dict
        {lowercase_name: {'pre': [str, ...], 'post': [str, ...]}}
        Only features with at least one pre or post condition are present.
    invariants : list
        [(lowercase_name, raw_condition_str)]
    """
    class_description = ''
    features = OrderedDict()
    conditions = {}
    invariants = []

    lines = content.split('\n')
    n = len(lines)
    i = 0

    in_table = False
    in_feat  = False   # inside Attributes or Functions section
    in_inv   = False   # inside Invariants section

    while i < n:
        raw = lines[i]
        s   = raw.strip()

        # ── Table boundaries ────────────────────────────────────────────────
        if s == '|===':
            in_table = not in_table
            if not in_table:
                in_feat = in_inv = False
            i += 1
            continue

        if not in_table:
            i += 1
            continue

        # ── Class description ────────────────────────────────────────────────
        if s == 'h|*Description*':
            # Look ahead for the 2+a| cell, then collect until next h| or |===
            j = i + 1
            while j < n and not lines[j].strip().startswith('2+a|'):
                if lines[j].strip().startswith('h|') or lines[j].strip() == '|===':
                    break
                j += 1
            if j < n and lines[j].strip().startswith('2+a|'):
                desc = lines[j].strip()[4:]   # strip leading '2+a|'
                j += 1
                while j < n:
                    ns = lines[j].strip()
                    if re.match(r'^h\|', ns) or ns == '|===':
                        break
                    desc += '\n' + lines[j].rstrip()
                    j += 1
                class_description = desc.strip()
                i = j   # advance to the terminating h| (processed next iteration)
            else:
                i += 1
            continue

        # ── Section markers ──────────────────────────────────────────────────
        if s in ('h|*Attributes*', 'h|*Functions*'):
            in_feat = True
            in_inv  = False
            i += 1
            continue

        if s == 'h|*Invariants*':
            in_feat = False
            in_inv  = True
            i += 1
            continue

        # Skip other header rows (Class, Inherit, Signature, Meaning …)
        if re.match(r'(h\||2\+[\^]?h\||\^h\|)', s) and not re.match(r'h\|\*\d', s):
            if not in_inv:
                i += 1
                continue

        # ── Feature rows (Attributes / Functions) ──────────────────────────
        if in_feat and re.match(r'h\|\*\d', s):
            # The h| multiplicity cell may span multiple lines (e.g. '(abstract)*').
            # Skip those continuation lines before the signature cell.
            j = i + 1
            while j < n:
                sl = lines[j].strip()
                if not sl:
                    j += 1
                    continue
                if sl.startswith('|') or sl.startswith('h|') or \
                   sl.startswith('^h|') or sl.startswith('a|'):
                    break
                j += 1   # continuation of h| cell — skip

            # Collect the signature cell (each line may end with ' +' to continue)
            sig_lines = []
            while j < n:
                sl = lines[j].strip()
                if not sl or sl.startswith('h|') or sl.startswith('a|') or sl.startswith('^h|'):
                    break
                sig_lines.append(sl)
                if not sl.endswith(' +'):
                    j += 1
                    break
                j += 1

            sig = ' '.join(sl.rstrip(' +').strip() for sl in sig_lines)
            # Extract feature name from |*name*: … or |*name* (…): …
            nm = re.match(r'\|[\*_`]*([a-z_][a-zA-Z0-9_]*)[\*_`]*', sig)
            if nm:
                feat_name = nm.group(1)

                # ── Collect pre/post conditions ──────────────────────────────
                # Condition lines appear between the sig and the a| description cell.
                # They look like:  __Pre__: `cond`  or  __Post_foo__: `cond`
                pre_conds = []
                post_conds = []
                k = j
                while k < n:
                    sl = lines[k].strip()
                    if sl.startswith('a|') or re.match(r'h\|\*', sl) or sl == '|===':
                        break
                    # Strip trailing ' +' before matching
                    sl_clean = re.sub(r'\s*\+\s*$', '', sl).strip()
                    m_cond = re.match(r'__(.+?)__:\s*`(.+?)`', sl_clean)
                    if m_cond:
                        label = m_cond.group(1).lower()
                        cond  = m_cond.group(2).strip()
                        if label.startswith('pre'):
                            pre_conds.append(cond)
                        elif label.startswith('post'):
                            post_conds.append(cond)
                    k += 1

                if pre_conds or post_conds:
                    conditions[feat_name] = {}
                    if pre_conds:
                        conditions[feat_name]['pre'] = pre_conds
                    if post_conds:
                        conditions[feat_name]['post'] = post_conds

                # ── Find a| description cell ──────────────────────────────────
                # k already stopped at a| or a terminator
                if k < n and lines[k].strip().startswith('a|'):
                    desc = lines[k].strip()[2:]   # strip leading 'a|'
                    k += 1
                    while k < n:
                        ns = lines[k].strip()
                        if re.match(r'(h\|\*|2\+[\^]?h\||^\|===)', ns):
                            break
                        desc += '\n' + lines[k].rstrip()
                        k += 1
                    desc = desc.strip()
                    if feat_name not in features:
                        features[feat_name] = desc

            i = j
            continue

        # ── Invariant rows ───────────────────────────────────────────────────
        if in_inv:
            # 2+a|__Name_here__: `condition …`
            m = re.match(r'2\+a\|__(.+?)__:\s*`?(.+?)`?\s*$', s)
            if m:
                inv_name = m.group(1).lower().replace(' ', '_')
                invariants.append((inv_name, m.group(2).strip()))
            i += 1
            continue

        i += 1

    return class_description, features, conditions, invariants


# Keep backward-compatible alias for any external callers
def parse_adoc_features(content):
    """Backward-compatible wrapper; returns (features, invariants)."""
    _, features, _, invariants = parse_adoc(content)
    return features, invariants


# ─── text cleaning ────────────────────────────────────────────────────────────

_LINK_RE = re.compile(r'link:[^\[]+\[([^\^]+)\^?\]')   # link:...[Name^]  → Name
_XREF_RE = re.compile(r'<<[^,>]+,([^>]+)>>')           # <<_x,Name>>      → `Name`
_VAR_RE  = re.compile(r'\{[a-z_]+\}')                  # {rm_release}     → ''

def clean_adoc_text(text):
    """Strip adoc link/macro/variable artifacts and collapse double spaces."""
    text = _LINK_RE.sub(r'\1', text)
    text = _XREF_RE.sub(r'`\1`', text)
    text = _VAR_RE.sub('', text)
    text = re.sub(r'  +', ' ', text)
    text = '\n'.join(l.rstrip() for l in text.split('\n'))
    return text


_CLASSNAME_RE = re.compile(r'(?<![`\w])([A-Z][A-Z_][A-Z_0-9]*)(?![`\w])')

def add_backticks(text):
    """Wrap bare CLASS_NAMES (2+ uppercase letters) in backticks."""
    return _CLASSNAME_RE.sub(r'`\1`', text)


def wrap_doc(text):
    """
    Word-wrap doc text to CONTENT_WIDTH.  Paragraph breaks (blank lines) and
    list-item lines are preserved.
    """
    paragraphs = re.split(r'\n{2,}', text.strip())
    out_paras = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        sub = para.split('\n')
        if any(re.match(r'\s*[*\-.]\s', l) for l in sub):
            # List paragraph — wrap each item individually
            items = []
            for line in sub:
                line = line.strip()
                if line:
                    items.extend(textwrap.wrap(line, width=CONTENT_WIDTH))
            out_paras.append('\n'.join(items))
        else:
            merged = ' '.join(l.strip() for l in sub if l.strip())
            out_paras.append('\n'.join(textwrap.wrap(merged, width=CONTENT_WIDTH)))
    return '\n\n'.join(out_paras)


# ─── invariant / condition transformation ────────────────────────────────────

_INV_TRANSFORMS = [
    # Void checks (most specific — must precede generic operator replacements)
    # Both capitalised (Void) and lowercase (void) appear in the adoc sources
    (re.compile(r'(\w[\w.]*)\s*/=\s*[Vv]oid'),   r'∃\1'),
    (re.compile(r'[Vv]oid\s*/=\s*(\w[\w.]*)'),   r'∃\1'),
    (re.compile(r'(\w[\w.]*)\s*=\s*[Vv]oid'),    r'¬∃\1'),
    (re.compile(r'[Vv]oid\s*=\s*(\w[\w.]*)'),    r'¬∃\1'),
    # Eiffel short-circuit forms (before plain and/or)
    (re.compile(r'\band\s+then\b'),  '∧'),
    (re.compile(r'\bor\s+else\b'),   '∨'),
    # Logical operators
    (re.compile(r'\bimplies\b'),     '⇒'),
    (re.compile(r'\bfor_all\b'),     '∀'),
    (re.compile(r'\bthere_exists\b'),'∃'),
    (re.compile(r'\bmatches\b'),     '∈'),
    (re.compile(r'\bxor\b'),         '⊻'),
    (re.compile(r'\band\b'),         '∧'),
    (re.compile(r'\bor\b'),          '∨'),
    (re.compile(r'\bnot\b'),         '¬'),
]

def transform_invariant(cond):
    """Convert Eiffel-style condition to symbolic notation."""
    for pat, repl in _INV_TRANSFORMS:
        cond = pat.sub(repl, cond)
    cond = re.sub(r'  +', ' ', cond).strip()
    # Remove space between prefix symbol and identifier: ¬ x → ¬x
    cond = re.sub(r'([¬∃∀])\s+(\w)', r'\1\2', cond)
    return cond


# ─── bmml comment building ────────────────────────────────────────────────────

def build_comment(doc_text, indent='    '):
    """Render doc_text as a bmml-style comment block."""
    result = [f'{indent}|']
    for para in re.split(r'\n{2,}', doc_text.strip()):
        para = para.strip()
        if not para:
            continue
        for line in para.split('\n'):
            result.append(f'{indent}| {line.strip()}')
        result.append(f'{indent}|')
    # Ensure the block ends with exactly one blank-bar line
    while result and result[-1] == f'{indent}|':
        result.pop()
    result.append(f'{indent}|')
    return '\n'.join(result)


# ─── bmml updating ────────────────────────────────────────────────────────────

_FEAT_RE = re.compile(
    r'^(?P<indent>\s+)(?P<kw>prop|func|const)\s+(?P<name>\w+)'
)

_CLASS_DECL_RE = re.compile(
    r'^(?:abstract\s+)?(?:class|enumeration)\s+\w'
)


def update_bmml(bmml_content, class_description='', features=None,
                conditions=None, invariants=None):
    """
    Return updated bmml content with:
      * refreshed class-level description block comment
      * refreshed feature block comments
      * pre_cond / post_cond sub-blocks on func declarations
      * updated invariant block

    All changes are idempotent: running on its own output makes no change.
    """
    if features   is None: features   = {}
    if conditions is None: conditions = {}
    if invariants is None: invariants = []

    lines = bmml_content.split('\n')
    out   = []
    i     = 0
    n     = len(lines)

    while i < n:
        line = lines[i]

        # ── Feature declaration ──────────────────────────────────────────────
        m = _FEAT_RE.match(line)
        if m:
            indent    = m.group('indent')
            kw        = m.group('kw')
            feat_name = m.group('name').lower()

            # ── Replace preceding feature comment ────────────────────────────
            if feat_name in features:
                while out:
                    if out[-1].strip() == '' or re.match(r'^\s*\|', out[-1]):
                        out.pop()
                    else:
                        break
                raw     = features[feat_name]
                cleaned = clean_adoc_text(raw)
                wrapped = wrap_doc(cleaned)
                out.append('')
                out.append(build_comment(wrapped, indent))

            # ── Pre / post conditions (func only) ────────────────────────────
            feat_conds = conditions.get(feat_name, {}) if kw == 'func' else {}
            has_pre  = bool(feat_conds.get('pre'))
            has_post = bool(feat_conds.get('post'))

            if has_pre or has_post:
                # Emit the func line with any trailing ' ;' stripped
                func_line = re.sub(r'\s*;\s*$', '', line.rstrip())
                out.append(func_line)
                i += 1

                # Scan the existing function body:
                #   * preserve non-condition inner content (e.g. 'alias "op" ;')
                #   * skip existing pre_cond / post_cond blocks (idempotency)
                #   * consume the function-level closing ';'
                indent2 = indent + '    '
                indent3 = indent2 + '    '
                preserved = []
                while i < n:
                    sl       = lines[i]
                    stripped = sl.strip()
                    if not stripped:                         # blank ends block
                        break
                    if not sl.startswith(indent2):           # outer indent again
                        break
                    if stripped in ('pre_cond', 'post_cond'):
                        # Skip block header + all deeper condition lines
                        i += 1
                        while i < n and lines[i].startswith(indent3):
                            i += 1
                        continue
                    if stripped == ';' and sl.rstrip() == indent2 + ';':
                        # Function-block closer — consume; we emit our own
                        i += 1
                        break
                    # Anything else (e.g. alias) — preserve
                    preserved.append(sl)
                    i += 1

                for pl in preserved:
                    out.append(pl)
                # Emit new condition blocks and block closer
                if has_pre:
                    out.append(f'{indent2}pre_cond')
                    for cond in feat_conds['pre']:
                        out.append(f'{indent3}{transform_invariant(cond)};')
                if has_post:
                    out.append(f'{indent2}post_cond')
                    for cond in feat_conds['post']:
                        out.append(f'{indent3}{transform_invariant(cond)};')
                out.append(f'{indent2};')
                continue   # i already advanced; do not fall through

            out.append(line)
            i += 1
            continue

        # ── Class declaration — update class-level description comment ───────
        if class_description and _CLASS_DECL_RE.match(line):
            # Strip the existing class comment block (only bare '|' lines, not
            # blank lines, so surrounding whitespace is preserved).
            while out and re.match(r'^\s*\|', out[-1]):
                out.pop()
            # Build and insert the new class description
            raw     = class_description
            cleaned = clean_adoc_text(raw)
            wrapped = wrap_doc(cleaned)
            out.append(build_comment(wrapped, ''))
            out.append(line)
            i += 1
            continue

        out.append(line)
        i += 1

    # ── Invariants ───────────────────────────────────────────────────────────
    if invariants:
        inv_block = ['', 'invariant']
        for name, cond in invariants:
            inv_block.append(f'    {name}: {transform_invariant(cond)};')
        inv_block.append('')

        inv_idx = end_idx = None
        for idx, l in enumerate(out):
            if re.match(r'^invariant\s*$', l.strip()):
                inv_idx = idx
            if l.strip() == 'end':
                end_idx = idx

        def trim_trailing_blanks(lst, before):
            while before > 0 and not lst[before - 1].strip():
                before -= 1
            return before

        if inv_idx is not None:
            # Replace existing invariant block
            blk_end = inv_idx + 1
            while blk_end < len(out):
                s = out[blk_end].strip()
                if s == 'end' or (s and not out[blk_end].startswith('    ')):
                    break
                blk_end += 1
            trim = trim_trailing_blanks(out, inv_idx)
            out = out[:trim] + inv_block + out[blk_end:]
        elif end_idx is not None:
            trim = trim_trailing_blanks(out, end_idx)
            out = out[:trim] + inv_block + [out[end_idx]]

    return '\n'.join(out)


def process_file(bmml_path, adoc_path, dry_run=False, verbose=True):
    """Update one bmml file from one adoc source. Returns True if changed."""
    with open(adoc_path, encoding='utf-8') as f:
        adoc = f.read()
    with open(bmml_path, encoding='utf-8') as f:
        bmml = f.read()

    class_description, features, conditions, invariants = parse_adoc(adoc)
    if verbose:
        print(f"  class_desc : {class_description[:60]!r}..." if class_description else "  class_desc : (none)")
        print(f"  features   : {list(features.keys())}")
        print(f"  conditions : {list(conditions.keys())}")
        print(f"  invariants : {[n for n, _ in invariants]}")

    new_bmml = update_bmml(bmml, class_description, features, conditions, invariants)
    changed  = new_bmml != bmml
    if changed and not dry_run:
        with open(bmml_path, 'w', encoding='utf-8') as f:
            f.write(new_bmml)
    return changed


# ─── mapping discovery ────────────────────────────────────────────────────────

# Maps a substring of the bmml directory path to an ordered list of preferred
# adoc namespace prefixes (first match wins).
_DIR_TO_NS = [
    ('/healthcare/ehr_extract/',             ['org.openehr.rm.ehr_extract']),
    ('/healthcare/ehr/',                     ['org.openehr.rm.ehr']),
    ('/demographic/',                        ['org.openehr.rm.demographic']),
    ('/subject_record/composition/',         ['org.openehr.rm.composition', 'org.openehr.rm.common']),
    ('/subject_record/statement/',           ['org.openehr.rm.composition', 'org.openehr.rm.common']),
    ('/infostructure/org.openehr/resource/', ['org.openehr.base.resource', 'org.openehr.rm.common']),
    ('/infostructure/org.openehr/versioning/',['org.openehr.rm.common']),
    ('/languages/org.openehr/aom14/',        ['org.openehr.am.aom14']),
    ('/languages/org.openehr/aom2/',         ['org.openehr.am.aom2']),
    ('/languages/org.openehr/bmm/',          ['org.openehr.lang.bmm']),
    ('/languages/org.openehr/bel/',          ['org.openehr.lang.beom', 'org.openehr.lang.bmm']),
    ('/representation/org.openehr/builtin_types/', ['org.openehr.base.foundation_types']),
    ('/representation/org.openehr/data_marking/',  ['org.openehr.rm.common']),
    ('/representation/org.openehr/data_structures/',['org.openehr.rm.data_structures']),
    ('/representation/org.openehr/data_types/',    ['org.openehr.rm.data_types']),
    ('/representation/org.openehr/definitions/',   ['org.openehr.base.foundation_types',
                                                    'org.openehr.base.base_types',
                                                    'org.openehr.rm.common']),
    ('/representation/org.openehr/identification/',['org.openehr.base.base_types']),
    ('/representation/org.openehr/integration/',   ['org.openehr.rm.integration']),
]

# Manual overrides for ambiguous multi-spec class names
_MANUAL_MAP = {
    # class name (uppercase) → preferred adoc path substring
    'ACTION':                   'specifications-RM',
    'AUTHORED_RESOURCE':        'specifications-BASE',
    'RESOURCE_DESCRIPTION':     'specifications-BASE',
    'RESOURCE_DESCRIPTION_ITEM':'specifications-BASE',
    'TRANSLATION_DETAILS':      'specifications-BASE',
    'ELEMENT':                  'specifications-RM',
    'EVENT':                    'specifications-RM',
}


def build_mapping(bmm_dir, specs_dir):
    """
    Return a list of (classname, bmml_path, adoc_path) triples.
    Unresolved classes (no adoc found) are omitted.
    """
    # Build adoc index: lowercase_classname → [full_path, ...]
    adoc_index = {}
    for root, _, files in os.walk(specs_dir):
        if 'specifications-SM' in root:
            continue
        if not root.endswith(os.sep + 'classes') and not root.endswith('/classes'):
            continue
        for fname in files:
            if not fname.endswith('.adoc'):
                continue
            stem      = fname[:-5]                          # strip .adoc
            classname = stem.rsplit('.', 1)[-1].lower()    # last dot-component
            full_path = os.path.join(root, fname)
            adoc_index.setdefault(classname, []).append(full_path)

    mapping = []
    for root, _, files in os.walk(bmm_dir):
        for fname in files:
            if not fname.endswith('.bmml'):
                continue
            bmml_path = os.path.join(root, fname)
            classname = fname[:-5]                          # strip .bmml
            key       = classname.lower()

            candidates = adoc_index.get(key, [])
            if not candidates:
                continue

            if len(candidates) == 1:
                mapping.append((classname, bmml_path, candidates[0]))
                continue

            # Manual override takes priority
            if classname.upper() in _MANUAL_MAP:
                frag   = _MANUAL_MAP[classname.upper()]
                chosen = next((c for c in candidates if frag in c), candidates[0])
                mapping.append((classname, bmml_path, chosen))
                continue

            # Use directory-to-namespace heuristic
            chosen = None
            for dir_pat, ns_list in _DIR_TO_NS:
                if dir_pat in root:
                    for ns in ns_list:
                        for c in candidates:
                            if os.path.basename(c).startswith(ns):
                                chosen = c
                                break
                        if chosen:
                            break
                    break
            mapping.append((classname, bmml_path, chosen or candidates[0]))

    return mapping


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    import argparse

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root  = os.path.dirname(script_dir)

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd')

    # Single-file mode
    p1 = sub.add_parser('update', help='Update one bmml from one adoc')
    p1.add_argument('bmml')
    p1.add_argument('adoc')
    p1.add_argument('--dry-run', action='store_true')

    # Batch mode
    p2 = sub.add_parser('all', help='Auto-discover and update all bmml files')
    p2.add_argument('--bmm-dir',   default=os.path.join(repo_root, 'bmm'))
    p2.add_argument('--specs-dir', default=os.path.join(os.path.dirname(repo_root), 'specifications'))
    p2.add_argument('--dry-run',   action='store_true')
    p2.add_argument('--verbose',   action='store_true')

    # Legacy two-positional-argument form: bmml_updater.py <bmml> <adoc>
    if len(sys.argv) == 3 and not sys.argv[1].startswith('-'):
        args = parser.parse_args(['update'] + sys.argv[1:])
    # Legacy --all flag
    elif len(sys.argv) >= 2 and sys.argv[1] == '--all':
        args = parser.parse_args(['all'] + sys.argv[2:])
    else:
        args = parser.parse_args()

    if args.cmd == 'update':
        changed = process_file(args.bmml, args.adoc, dry_run=args.dry_run)
        print(f"{'Changed' if changed else 'No change'}: {args.bmml}")

    elif args.cmd == 'all':
        mapping   = build_mapping(args.bmm_dir, args.specs_dir)
        changed_n = error_n = 0
        for classname, bmml_path, adoc_path in mapping:
            if not os.path.exists(adoc_path):
                continue
            try:
                changed = process_file(bmml_path, adoc_path,
                                       dry_run=args.dry_run,
                                       verbose=args.verbose)
                if changed:
                    print(f"  {'(dry) ' if args.dry_run else ''}Updated: {classname}")
                    changed_n += 1
            except Exception as exc:
                print(f"  ERROR {classname}: {exc}")
                error_n += 1
        action = 'Would update' if args.dry_run else 'Updated'
        print(f"\nDone: {action} {changed_n} files"
              + (f", {error_n} errors" if error_n else ''))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
