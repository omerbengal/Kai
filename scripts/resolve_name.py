#!/usr/bin/env python3
"""
Fuzzy contact name resolver.
Usage: python resolve_name.py "<partner>"
Output: JSON {"id": N, "name": "...", "matched_variant": "...", "score": N}
        or    {"id": null, "name": null, "matched_variant": null, "score": 0} if no match above threshold
"""
import sys, json, sqlite3, io, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = 'D:/Kai/kai.db'
THRESHOLD = 60  # minimum score to consider a match
PEOPLE_BONUS = 10  # curated `people` matches get this score bonus over phone_contacts ties

# Hebrew niqud (vowel points) and ta'amim (cantillation marks) — ignored during matching.
# Range U+0591–U+05C7 covers all combining Hebrew marks (sheva, holam, etc.).
_NIQUD_RE = re.compile(r'[֑-ׇ]')
def strip_niqud(s):
    return _NIQUD_RE.sub('', s) if s else s

def get_contacts():
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT p.id, p.name, p.phone, GROUP_CONCAT(pn.nickname, '|||') as nicknames
        FROM people p
        LEFT JOIN people_nicknames pn ON p.id = pn.person_id
        GROUP BY p.id
    """).fetchall()
    conn.close()
    contacts = []
    for r in rows:
        names = [r['name']]
        if r['nicknames']:
            names += r['nicknames'].split('|||')
        contacts.append({'id': r['id'], 'canonical': r['name'], 'phone': r['phone'], 'variants': names})
    return contacts

def resolve(query: str):
    contacts = get_contacts()

    try:
        from rapidfuzz import fuzz
        _score = lambda a, b: fuzz.ratio(a, b)
    except ImportError:
        import difflib
        _score = lambda a, b: difflib.SequenceMatcher(None, a, b).ratio() * 100

    # Compare with niqud stripped from both sides so "כהֵן" and "כהן" match.
    score_fn = lambda a, b: _score(strip_niqud(a), strip_niqud(b))

    # Always evaluate BOTH sources, then pick the highest-scoring candidate.
    # A weak match in `people` must not block a strong match in `phone_contacts`.

    # --- people table ---
    best_people_score = 0
    best_people_contact = None
    best_people_match = None
    for contact in contacts:
        for variant in contact['variants']:
            score = score_fn(query, variant)
            if score > best_people_score:
                best_people_score = score
                best_people_contact = contact
                best_people_match = variant

    # --- phone_contacts table ---
    # Strip niqud from query parts and from stored names (via SQLite UDF) so matches
    # ignore Hebrew vowel marks (e.g. "כהן" matches "כהֵן").
    parts = [strip_niqud(p) for p in query.split()]
    like_patterns = [f'%{p}%' for p in parts]
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    conn.create_function('strip_niqud', 1, strip_niqud)

    # Prefer AND match (all parts present) over OR match (any part present)
    and_clauses = ' AND '.join(['strip_niqud(name) LIKE ?' for _ in like_patterns])
    rows_and = conn.execute(
        f"SELECT name, phone FROM phone_contacts WHERE {and_clauses} ORDER BY length(name) ASC",
        like_patterns
    ).fetchall()
    if not rows_and:
        or_clauses = ' OR '.join(['strip_niqud(name) LIKE ?' for _ in like_patterns])
        rows_and = conn.execute(
            f"SELECT name, phone FROM phone_contacts WHERE {or_clauses} ORDER BY length(name) ASC",
            like_patterns
        ).fetchall()
    conn.close()

    best_pc_score = 0
    best_pc_row = None
    for row in rows_and or []:
        score = score_fn(query, row['name'])
        if score > best_pc_score:
            best_pc_score = score
            best_pc_row = row

    # --- pick winner ---
    # Curated people get a small bonus so they win close ties.
    people_effective = best_people_score + (PEOPLE_BONUS if best_people_contact else 0)

    if best_people_contact and people_effective >= best_pc_score and best_people_score >= THRESHOLD:
        return {
            'id': best_people_contact['id'],
            'name': best_people_contact['canonical'],
            'phone': best_people_contact.get('phone'),
            'matched_variant': best_people_match,
            'score': round(best_people_score, 1)
        }

    if best_pc_row and best_pc_score >= THRESHOLD:
        return {
            'id': None,
            'name': best_pc_row['name'],
            'phone': best_pc_row['phone'],
            'matched_variant': best_pc_row['name'],
            'score': round(best_pc_score, 1)
        }

    # Nothing above threshold — return the best raw score we saw, for debugging.
    return {
        'id': None, 'name': None, 'phone': None, 'matched_variant': None,
        'score': round(max(best_people_score, best_pc_score), 1)
    }

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: resolve_name.py <name>'}))
        sys.exit(1)
    query = sys.argv[1]
    result = resolve(query)
    print(json.dumps(result, ensure_ascii=False))
